# -*- coding: utf-8 -*-
"""风险与收益指标纯函数库（M2 分析模块）。

公式对照知识库 docs/02-风险与评估/风险衡量指标：波动、回撤与夏普比率.md 与
docs/02-风险与评估/估值指标：PE、PB与估值分位.md：

- 年化波动率 = 日收益率标准差 × √交易日数（A股一年约 250 个交易日，本库默认 252 可调）；
- 最大回撤 = (区间最低价 − 区间最高价) / 区间最高价（从最高点到之后最低点的最大跌幅）；
- 夏普比率 = (年化收益率 − 无风险利率) / 年化波动率；
- 索提诺比率 = 夏普的下行版：分母只统计下跌波动；
- 估值分位 = 历史上比当前值低的样本占比 × 100。

约定：所有函数只读不写、无网络无 DB；输入不足（bar 数 < 2）或结果无意义
（除零、无下行波动等）时返回 None，由调用方决定如何呈现。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.datasource.base import Bar


@dataclass(frozen=True)
class Drawdown:
    """最大回撤结果：幅度（负数）+ 峰值日与谷底日。"""

    magnitude: float  # 负数，如 -0.25 表示最深回撤 25%
    peak_date: str  # 回撤起点（区间最高点日期）
    trough_date: str  # 回撤终点（之后的最低点日期）


@dataclass(frozen=True)
class DailyStats:
    """日收益率基础统计。"""

    mean: float  # 日均收益率
    std: float  # 日收益率样本标准差（ddof=1）
    worst: float  # 最差单日收益率（负数）


def daily_returns(bars: Sequence[Bar]) -> list[float]:
    """由收盘价序列生成日收益率序列（r_i = close_i / close_{i-1} − 1）。"""
    closes = [b.close for b in bars]
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]


def annualized_volatility(bars: Sequence[Bar], trading_days: int = 252) -> float | None:
    """年化波动率：日收益率样本标准差 × √trading_days。

    bar 数不足 2 根时返回 None。
    """
    returns = daily_returns(bars)
    if len(returns) < 1:
        return None
    m = sum(returns) / len(returns)
    var = sum((r - m) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0.0
    return math.sqrt(var) * math.sqrt(trading_days)


def annualized_return(bars: Sequence[Bar], trading_days: int = 252) -> float | None:
    """几何年化收益率：(期末价/期初价)^(trading_days/收益天数) − 1。

    采用复利口径，反映真实持有体验；非算术日均 × 252。
    """
    closes = [b.close for b in bars]
    if len(closes) < 2 or closes[0] <= 0:
        return None
    n = len(closes) - 1
    return (closes[-1] / closes[0]) ** (trading_days / n) - 1


def max_drawdown(bars: Sequence[Bar]) -> Drawdown | None:
    """基于 close 的最大回撤：从历史最高点到之后最低点的最大跌幅。

    返回幅度（负数）与起止日期（峰值日/谷底日）；
    单边上涨等无回撤情形返回 magnitude=0.0（峰=谷=最高价当日）。
    """
    if not bars:
        return None
    peak_price = -math.inf
    peak_date = bars[0].date
    best = Drawdown(0.0, bars[0].date, bars[0].date)
    for b in bars:
        if b.close > peak_price:
            peak_price = b.close
            peak_date = b.date
            if best.magnitude == 0.0:
                best = Drawdown(0.0, b.date, b.date)  # 无回撤时起止锚定在最高价当日
        dd = (b.close - peak_price) / peak_price if peak_price > 0 else 0.0
        if dd < best.magnitude:
            best = Drawdown(dd, peak_date, b.date)
    return best


def sharpe_ratio(bars: Sequence[Bar], risk_free: float = 0.02, trading_days: int = 252) -> float | None:
    """夏普比率 = (年化收益率 − 无风险利率) / 年化波动率。

    无风险利率默认 0.02（十年期国债收益率量级）；波动为零（如常数序列）时无意义返回 None。
    """
    ann_ret = annualized_return(bars, trading_days)
    ann_vol = annualized_volatility(bars, trading_days)
    if ann_ret is None or ann_vol is None or ann_vol <= 0:
        return None
    return (ann_ret - risk_free) / ann_vol


def sortino_ratio(bars: Sequence[Bar], risk_free: float = 0.02, trading_days: int = 252) -> float | None:
    """索提诺比率 = (年化收益率 − 无风险利率) / 年化下行波动率。

    下行波动采用 target=0 的简化口径：sqrt(mean(min(r, 0)^2)) × √trading_days
    （全部样本参与平均，仅负收益计入平方）。序列无任何下跌时下行波动为 0，
    比率无意义，返回 None。
    """
    ann_ret = annualized_return(bars, trading_days)
    returns = daily_returns(bars)
    if ann_ret is None or not returns:
        return None
    down_var = sum(min(r, 0.0) ** 2 for r in returns) / len(returns)
    down_vol = math.sqrt(down_var) * math.sqrt(trading_days)
    if down_vol <= 0:
        return None
    return (ann_ret - risk_free) / down_vol


def daily_returns_stats(bars: Sequence[Bar]) -> DailyStats | None:
    """日收益率统计：均值 / 样本标准差（ddof=1）/ 最差单日。

    bar 数不足 3 根（收益数不足 2）时样本标准差无定义，返回 None。
    """
    returns = daily_returns(bars)
    if len(returns) < 2:
        return None
    m = sum(returns) / len(returns)
    var = sum((r - m) ** 2 for r in returns) / (len(returns) - 1)
    return DailyStats(mean=m, std=math.sqrt(var), worst=min(returns))


def percentile(series: Iterable[float], value: float) -> float | None:
    """估值分位（0-100）：序列中比 value 小的样本占比 × 100。

    对应知识库"历史上比当前估值低的天数占比"。空序列返回 None；
    value 低于全部样本返回 0.0，高于全部返回 100.0。
    """
    data = [x for x in series if x == x]  # 过滤 NaN（x != x）
    if not data:
        return None
    lower = sum(1 for x in data if x < value)
    return lower / len(data) * 100.0
