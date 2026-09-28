# -*- coding: utf-8 -*-
"""分析 API（M2）：标的风险指标 + 估值分位 + 参考仓位，以及风险偏好档案读写。

对照知识库 docs/02-风险与评估/：
- 《风险衡量指标》第六节：参考仓位 = 可承受回撤 ÷ 标的近3年最大回撤（上限 100%）；
- 《估值指标》第三节：估值分位 = 当前 PE/PB 在近10年历史中的位置（0-100）。

注意：本路由同时承载 /api/analysis/* 与 /api/profile 两组端点，
故前缀取 /api（保持与 app/routers/market.py 相同的 APIRouter(prefix=..., tags=[...]) 风格）。
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models_analysis import RiskProfile
from app.services.datasource import get_source
from app.services.datasource.base import Bar
from app.services.indicators import (
    annualized_volatility,
    daily_returns_stats,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from app.services.valuation import get_valuation

router = APIRouter(prefix="/api", tags=["analysis"])

# 参考仓位回看窗口：约 3 年交易日（get_kline 内部按 days*1.6 自然日放大请求区间）
HIST_WINDOW_BARS = 750


# ---------------------------------------------------------------------------
# 出入参模型
# ---------------------------------------------------------------------------


class BarOut(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    change_pct: float


class IndicatorsOut(BaseModel):
    ann_volatility: float | None  # 年化波动率
    max_drawdown: float | None  # 最大回撤（负数）
    max_drawdown_start: str | None  # 回撤峰值日
    max_drawdown_end: str | None  # 回撤谷底日
    sharpe: float | None
    sortino: float | None
    daily_mean: float | None  # 日均收益率
    daily_std: float | None  # 日收益率标准差
    worst_daily: float | None  # 最差单日收益率（负数）


class ValuationOut(BaseModel):
    pe: float | None
    pb: float | None
    pe_percentile: float | None
    pb_percentile: float | None
    window_years: int


class RefPositionOut(BaseModel):
    tolerance: float  # 用户可承受的最大回撤（来自 RiskProfile）
    hist_max_drawdown: float | None  # 标的近3年最大回撤（负数）
    suggested_max_pct: float | None  # 参考仓位上限（0-100）


class AnalysisOut(BaseModel):
    market: str
    code: str
    name: str
    bars: list[BarOut]
    indicators: IndicatorsOut
    valuation: ValuationOut | None  # 估值不可用时为 null
    ref_position: RefPositionOut


class ProfileOut(BaseModel):
    max_drawdown_tolerance: float
    horizon: str
    target_return: float
    attitude: str
    updated_at: str | None = None


class ProfileIn(BaseModel):
    """风险偏好写入：全部可选，缺省保留现有值（首写时取默认值）。"""

    max_drawdown_tolerance: float | None = Field(None, gt=0, le=1)
    horizon: Literal["1y", "3y", "5y", "10y"] | None = None
    target_return: float | None = Field(None, ge=-1, le=10)
    attitude: Literal["conservative", "balanced", "aggressive"] | None = None


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _load_profile(db: Session) -> RiskProfile:
    """读取单行风险偏好；不存在时返回默认值（不落库，读操作无副作用）。

    注意：mapped_column 的 default 仅在 INSERT 时生效，纯内存对象需显式赋默认值。
    """
    row = db.get(RiskProfile, 1)
    if row is None:
        return RiskProfile(
            id=1,
            max_drawdown_tolerance=0.15,
            horizon="3y",
            target_return=0.08,
            attitude="balanced",
        )
    return row


async def _fetch_bars(market: str, code: str, fetch_days: int) -> tuple[list[Bar], str]:
    """拉取 K 线全量窗口 + 标的名称（调用方自行截取指标窗口与3年回看窗口）。"""
    try:
        src = get_source(market)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        bars_full, _stale = await asyncio.to_thread(src.get_kline, code, fetch_days)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"K线不可用: {e}") from e

    name = code
    try:
        found = await asyncio.to_thread(src.search, code, 1)
        if found:
            name = found[0].name
    except Exception:
        pass  # 名称获取失败不阻塞分析
    return bars_full, name


def _build_indicators(bars: list[Bar]) -> IndicatorsOut:
    dd = max_drawdown(bars)
    stats = daily_returns_stats(bars)
    return IndicatorsOut(
        ann_volatility=annualized_volatility(bars),
        max_drawdown=dd.magnitude if dd else None,
        max_drawdown_start=dd.peak_date if dd else None,
        max_drawdown_end=dd.trough_date if dd else None,
        sharpe=sharpe_ratio(bars),
        sortino=sortino_ratio(bars),
        daily_mean=stats.mean if stats else None,
        daily_std=stats.std if stats else None,
        worst_daily=stats.worst if stats else None,
    )


def _build_ref_position(tolerance: float, hist_bars: list[Bar]) -> RefPositionOut:
    """参考仓位 = 可承受回撤 ÷ 近3年最大回撤 × 100（上限 100）。

    历史无回撤（单边上涨/数据过短）时上限 100；无数据时 suggested 为 None。
    """
    dd = max_drawdown(hist_bars) if hist_bars else None
    if dd is None:
        return RefPositionOut(tolerance=tolerance, hist_max_drawdown=None, suggested_max_pct=None)
    if dd.magnitude == 0:
        return RefPositionOut(tolerance=tolerance, hist_max_drawdown=0.0, suggested_max_pct=100.0)
    suggested = min(tolerance / abs(dd.magnitude) * 100.0, 100.0)
    return RefPositionOut(tolerance=tolerance, hist_max_drawdown=dd.magnitude, suggested_max_pct=suggested)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/analysis/{market}/{code}", response_model=AnalysisOut)
async def analysis(
    market: str,
    code: str,
    days: int = Query(250, ge=10, le=3000, description="指标计算与返回的K线窗口（交易日）"),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    bars_full, name = await _fetch_bars(market, code, max(days, HIST_WINDOW_BARS))
    bars = bars_full[-days:]  # 指标窗口
    hist_bars = bars_full  # 近3年回看窗口（fetch_days >= 750）
    profile = _load_profile(db)
    snap = await asyncio.to_thread(get_valuation, market, code)
    return AnalysisOut(
        market=market,
        code=code,
        name=name,
        bars=[BarOut(**b.__dict__) for b in bars],
        indicators=_build_indicators(bars),
        valuation=(
            ValuationOut(
                pe=snap.pe, pb=snap.pb,
                pe_percentile=snap.pe_percentile, pb_percentile=snap.pb_percentile,
                window_years=snap.window_years,
            )
            if snap is not None
            else None
        ),
        ref_position=_build_ref_position(profile.max_drawdown_tolerance, hist_bars),
    )


@router.get("/profile", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)) -> ProfileOut:
    p = _load_profile(db)
    return ProfileOut(
        max_drawdown_tolerance=p.max_drawdown_tolerance,
        horizon=p.horizon,
        target_return=p.target_return,
        attitude=p.attitude,
        updated_at=p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else None,
    )


@router.put("/profile", response_model=ProfileOut)
def put_profile(body: ProfileIn, db: Session = Depends(get_db)) -> ProfileOut:
    """单行 upsert：写入风偏档案，缺省字段保留现有值/默认值。"""
    p = db.get(RiskProfile, 1)
    if p is None:
        p = RiskProfile(id=1)
        db.add(p)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(p, key, value)
    db.commit()
    db.refresh(p)
    return ProfileOut(
        max_drawdown_tolerance=p.max_drawdown_tolerance,
        horizon=p.horizon,
        target_return=p.target_return,
        attitude=p.attitude,
        updated_at=p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else None,
    )
