# -*- coding: utf-8 -*-
"""indicators 纯函数库单测：用手工构造的短序列验证公式（数值为独立 REPL 手算结果）。"""
from __future__ import annotations

from typing import Sequence

import pytest

from app.services.datasource.base import Bar
from app.services.indicators import (
    annualized_return,
    annualized_volatility,
    daily_returns,
    daily_returns_stats,
    max_drawdown,
    percentile,
    sharpe_ratio,
    sortino_ratio,
)


def make_bars(closes: Sequence[float], start: str = "2024-01-01") -> list[Bar]:
    """按收盘价构造连续日期的 Bar 序列。"""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [
        Bar(
            date=(d0 + timedelta(days=i)).isoformat(),
            open=c, high=c, low=c, close=c, volume=0.0, change_pct=0.0,
        )
        for i, c in enumerate(closes)
    ]


# 手算基准：closes=[100,120,90,130]
# 日收益 r = [0.2, -0.25, 4/9]
# mean(r) = 0.13148148148148145；样本 std(r, ddof=1) = 0.3522561030556003
# ann_vol(252) = 0.3522561030556003 × √252 = 5.591892278939148
# 几何年化 = 1.3^(252/3) − 1 = 3725989216.5060716
# sharpe = (ann_ret − 0.02) / ann_vol = 666319920.0956243
# 下行波动 = sqrt(0.25²/3) × √252 = 2.29128784747792
# sortino = (ann_ret − 0.02) / 下行波动 = 1626155011.7272978
BARS = make_bars([100, 120, 90, 130])


class TestDailyReturns:
    def test_values(self) -> None:
        r = daily_returns(BARS)
        assert r == pytest.approx([0.2, -0.25, 4 / 9])

    def test_empty(self) -> None:
        assert daily_returns(make_bars([])) == []
        assert daily_returns(make_bars([100])) == []


class TestAnnualizedVolatility:
    def test_hand_computed_252(self) -> None:
        assert annualized_volatility(BARS) == pytest.approx(5.591892278939148)

    def test_trading_days_param(self) -> None:
        # 知识库口径：A股约 250 个交易日
        assert annualized_volatility(BARS, trading_days=250) == pytest.approx(
            0.3522561030556003 * 250 ** 0.5
        )

    def test_insufficient_data(self) -> None:
        assert annualized_volatility(make_bars([])) is None
        assert annualized_volatility(make_bars([100])) is None

    def test_constant_series_is_zero(self) -> None:
        assert annualized_volatility(make_bars([100, 100, 100])) == 0.0


class TestAnnualizedReturn:
    def test_hand_computed(self) -> None:
        assert annualized_return(BARS) == pytest.approx(3725989216.5060716)

    def test_flat(self) -> None:
        assert annualized_return(make_bars([5, 5, 5, 5])) == pytest.approx(0.0)

    def test_insufficient_data(self) -> None:
        assert annualized_return(make_bars([100])) is None


class TestMaxDrawdown:
    def test_hand_computed(self) -> None:
        dd = max_drawdown(BARS)
        assert dd is not None
        # 峰值 120（第2根）→ 谷底 90（第3根）：(90-120)/120 = -0.25
        assert dd.magnitude == pytest.approx(-0.25)
        assert dd.peak_date == "2024-01-02"
        assert dd.trough_date == "2024-01-03"

    def test_later_recovery_keeps_max(self) -> None:
        # 后段 120 仍低于峰 130，最深回撤保持在 130→90
        dd = max_drawdown(make_bars([100, 130, 90, 95, 120]))
        assert dd is not None
        assert dd.magnitude == pytest.approx(-0.3076923076923077)
        assert dd.peak_date == "2024-01-02"
        assert dd.trough_date == "2024-01-03"

    def test_monotonic_up_no_drawdown(self) -> None:
        dd = max_drawdown(make_bars([100, 110, 120]))
        assert dd is not None
        assert dd.magnitude == 0.0
        assert dd.peak_date == dd.trough_date == "2024-01-03"

    def test_empty(self) -> None:
        assert max_drawdown([]) is None


class TestSharpeRatio:
    def test_hand_computed(self) -> None:
        assert sharpe_ratio(BARS) == pytest.approx(666319920.0956243)

    def test_risk_free_param(self) -> None:
        assert sharpe_ratio(BARS, risk_free=0.0) == pytest.approx(
            3725989216.5060716 / 5.591892278939148
        )

    def test_zero_volatility_is_none(self) -> None:
        assert sharpe_ratio(make_bars([100, 100, 100])) is None

    def test_insufficient_data(self) -> None:
        assert sharpe_ratio(make_bars([100])) is None


class TestSortinoRatio:
    def test_hand_computed(self) -> None:
        assert sortino_ratio(BARS) == pytest.approx(1626155011.7272978)

    def test_no_downside_is_none(self) -> None:
        assert sortino_ratio(make_bars([100, 110, 120])) is None

    def test_insufficient_data(self) -> None:
        assert sortino_ratio(make_bars([100])) is None


class TestDailyReturnsStats:
    def test_hand_computed(self) -> None:
        s = daily_returns_stats(BARS)
        assert s is not None
        assert s.mean == pytest.approx(0.13148148148148145)
        assert s.std == pytest.approx(0.3522561030556003)
        assert s.worst == pytest.approx(-0.25)

    def test_case2(self) -> None:
        s = daily_returns_stats(make_bars([100, 130, 90, 95, 120]))
        assert s is not None
        assert s.mean == pytest.approx(0.07775528565002249)
        assert s.std == pytest.approx(0.27858542420734833)

    def test_needs_two_returns(self) -> None:
        assert daily_returns_stats(make_bars([100, 120])) is None


class TestPercentile:
    def test_hand_computed(self) -> None:
        # [1..5] 中比 3 小的有 2 个 → 40%；比 3.5 小的有 3 个 → 60%
        assert percentile([1, 2, 3, 4, 5], 3) == pytest.approx(40.0)
        assert percentile([1, 2, 3, 4, 5], 3.5) == pytest.approx(60.0)

    def test_bounds(self) -> None:
        assert percentile([1, 2, 3, 4, 5], 0) == pytest.approx(0.0)
        assert percentile([1, 2, 3, 4, 5], 6) == pytest.approx(100.0)

    def test_empty_is_none(self) -> None:
        assert percentile([], 3) is None

    def test_nan_filtered(self) -> None:
        # NaN 剔除后序列为 [1,2,3]，比 2.5 小的有 2 个 → 66.67%
        assert percentile([1, 2, float("nan"), 3], 2.5) == pytest.approx(2 / 3 * 100)
