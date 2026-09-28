# -*- coding: utf-8 -*-
"""估值数据获取（M2）：A股个股 PE/PB 历史与当前估值分位。

对照知识库 docs/02-风险与评估/估值指标：PE、PB与估值分位.md——
"把当前估值放在它自己的历史序列里看位置"，估值分位 = 历史上比当前低的天数占比。

数据源策略（免费源不稳定，全部失败安全降级为 None）：
1. 首选乐咕乐股接口 ak.stock_a_indicator_lg（返回 trade_date/pe/pe_ttm/pb 全量历史）——
   注意：akshare >= 1.18 已移除该接口，仅老版本可用，故按属性探测懒加载；
2. 降级百度个股估值 ak.stock_zh_valuation_baidu（indicator=市盈率(TTM)/市净率，近十年窗口）。

缓存策略：内存缓存 1 天（估值数据日频足够）；拉取失败进入 10 分钟冷却期，
冷却期内直接返回 None 不再撞网络（参考行情快照 SpotProvider 的节流思路）。
指数/ETF/场外基金估值 M2 不做，直接返回 None。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.services.indicators import percentile

logger = logging.getLogger(__name__)

# 内存缓存 TTL（秒）与失败冷却（秒）
CACHE_TTL_SECONDS = 24 * 3600
RETRY_COOLDOWN_SECONDS = 600
# 估值分位的历史窗口（年）
VALUATION_WINDOW_YEARS = 10

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, datetime, "ValuationSnapshot"]] = {}  # code -> (monotonic 时刻, 墙钟, 快照)
_FAILED_AT: dict[str, float] = {}  # code -> 最近一次失败时的 monotonic 时刻


@dataclass(frozen=True)
class ValuationSnapshot:
    """单只标的的当前估值与其历史分位。字段可能缺失（None）。"""

    pe: float | None  # 当前市盈率（TTM 口径）
    pb: float | None  # 当前市净率
    pe_percentile: float | None  # PE 在历史窗口中的分位（0-100，越低越便宜）
    pb_percentile: float | None  # PB 在历史窗口中的分位（0-100）
    window_years: int  # 分位统计的历史窗口年数


@dataclass(frozen=True)
class _ValPoint:
    """单日估值点（内部中间结构，两个数据源统一到此处）。"""

    day: date
    pe: float | None  # TTM 市盈率
    pb: float | None


def _to_float(v) -> float | None:
    """安全转 float：NaN/'-'/None/空串一律视为缺失。"""
    try:
        f = float(v)
        return None if f != f else f  # NaN 判定
    except (TypeError, ValueError):
        return None


def _fetch_lg(symbol: str) -> list[_ValPoint]:
    """乐咕乐股个股估值历史（akshare 老版本接口，返回 trade_date/pe/pe_ttm/pb）。"""
    import akshare as ak  # 懒加载：仅在真正拉取时导入

    fn = getattr(ak, "stock_a_indicator_lg", None)
    if fn is None:
        raise LookupError("当前 akshare 版本无 stock_a_indicator_lg 接口")
    df = fn(symbol=symbol)
    points: list[_ValPoint] = []
    for _, r in df.iterrows():
        d = str(r.get("trade_date", ""))[:10]
        if not d:
            continue
        pe = _to_float(r.get("pe_ttm")) or _to_float(r.get("pe"))
        points.append(_ValPoint(day=date.fromisoformat(d), pe=pe, pb=_to_float(r.get("pb"))))
    return points


def _fetch_baidu(symbol: str) -> list[_ValPoint]:
    """百度个股估值：分别拉 PE(TTM) 与 PB 两个日频序列后按日期对齐合并。"""
    import akshare as ak  # 懒加载：仅在真正拉取时导入

    def series(indicator: str) -> dict[date, float]:
        df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator=indicator, period="近十年")
        out: dict[date, float] = {}
        for _, r in df.iterrows():
            v = _to_float(r.get("value"))
            if v is not None and v > 0:
                out[r["date"]] = v
        return out

    pe_map = series("市盈率(TTM)")
    pb_map = series("市净率")
    if not pe_map and not pb_map:
        raise RuntimeError(f"{symbol} 百度估值接口返回空数据")
    days = sorted(set(pe_map) | set(pb_map))
    return [
        _ValPoint(day=d, pe=pe_map.get(d), pb=pb_map.get(d))
        for d in days
        if pe_map.get(d) is not None or pb_map.get(d) is not None
    ]


def _window_points(points: list[_ValPoint], window_years: int) -> list[_ValPoint]:
    """截取最近 window_years 年（自然日）内的估值点。"""
    cutoff = date.today() - timedelta(days=365 * window_years + 1)
    return [p for p in points if p.day >= cutoff] or points  # 窗口内无数据时退回全量


def _build_snapshot(points: list[_ValPoint], window_years: int) -> ValuationSnapshot | None:
    """由估值点序列构造快照：当前值取最新一天，分位在整个窗口序列上计算。"""
    recent = _window_points(points, window_years)
    latest = recent[-1]
    if latest.pe is None and latest.pb is None:
        return None
    pe_hist = [p.pe for p in recent if p.pe is not None]
    pb_hist = [p.pb for p in recent if p.pb is not None]
    return ValuationSnapshot(
        pe=latest.pe,
        pb=latest.pb,
        pe_percentile=percentile(pe_hist, latest.pe) if latest.pe is not None else None,
        pb_percentile=percentile(pb_hist, latest.pb) if latest.pb is not None else None,
        window_years=window_years,
    )


def get_valuation(market: str, code: str) -> ValuationSnapshot | None:
    """获取个股当前估值与历史分位；不可用/不支持时返回 None（不抛异常）。

    M2 仅支持 a_stock；整个调用持锁串行化（个人工具低并发，避免重复拉取）。
    """
    if market != "a_stock" or not code or not code.isdigit() or len(code) != 6:
        return None
    with _CACHE_LOCK:
        now = time.monotonic()
        cached = _CACHE.get(code)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[2]
        failed_at = _FAILED_AT.get(code)
        if failed_at is not None and now - failed_at < RETRY_COOLDOWN_SECONDS:
            return None  # 冷却期内不重试

        for fetch in (_fetch_lg, _fetch_baidu):
            try:
                points = fetch(code)
                snapshot = _build_snapshot(points, VALUATION_WINDOW_YEARS) if points else None
                if snapshot is not None:
                    _CACHE[code] = (now, datetime.now(), snapshot)
                    _FAILED_AT.pop(code, None)
                    return snapshot
                logger.warning("[a_stock/%s] 估值数据为空", code)
            except Exception:
                logger.exception("[a_stock/%s] 估值拉取失败（%s）", code, fetch.__name__)
        _FAILED_AT[code] = now
        return None


def clear_cache() -> None:
    """清空内存缓存与失败冷却（测试用）。"""
    with _CACHE_LOCK:
        _CACHE.clear()
        _FAILED_AT.clear()


def cache_info() -> dict[str, datetime]:
    """当前缓存概览（调试/测试用）：每只标的的缓存写入时刻（墙钟）。"""
    with _CACHE_LOCK:
        return {code: wall for code, (_mono, wall, _snap) in _CACHE.items()}
