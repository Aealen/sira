# -*- coding: utf-8 -*-
"""valuation 单测：mock 数据源函数，验证分位计算、缓存命中、失败降级与冷却。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import app.services.valuation as valuation
from app.services.valuation import (
    ValuationSnapshot,
    _ValPoint,
    clear_cache,
    get_valuation,
)


def make_points(pe_values: list[float], pb_values: list[float]) -> list[_ValPoint]:
    """构造从今天往回每天一个点的估值序列（pe/pb 等长）。"""
    today = date.today()
    n = len(pe_values)
    return [
        _ValPoint(
            day=today - timedelta(days=n - 1 - i),
            pe=pe_values[i],
            pb=pb_values[i],
        )
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    clear_cache()
    yield
    clear_cache()


class TestGetValuation:
    def test_a_stock_success_and_percentile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake_lg(symbol: str) -> list[_ValPoint]:
            calls.append(symbol)
            # 10 个历史点 + 最新点：PE=20，历史 [10..20] 中比 20 小的有 10 个 → 90.9%
            pe_hist = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
            pb_hist = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9]
            return make_points(pe_hist + [20.0], pb_hist + [2.0])

        monkeypatch.setattr(valuation, "_fetch_lg", fake_lg)
        snap = get_valuation("a_stock", "600519")
        assert snap is not None
        assert snap.pe == pytest.approx(20.0)
        assert snap.pb == pytest.approx(2.0)
        assert snap.pe_percentile == pytest.approx(10 / 11 * 100)
        assert snap.pb_percentile == pytest.approx(10 / 11 * 100)
        assert snap.window_years == 10

        # 内存缓存：第二次调用不再拉取
        snap2 = get_valuation("a_stock", "600519")
        assert snap2 == snap
        assert calls == ["600519"]

    def test_fallback_to_baidu_when_lg_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(symbol: str) -> list[_ValPoint]:
            raise LookupError("no stock_a_indicator_lg")

        def fake_baidu(symbol: str) -> list[_ValPoint]:
            return make_points([30.0, 31.0, 32.0], [3.0, 3.1, 3.2])

        monkeypatch.setattr(valuation, "_fetch_lg", boom)
        monkeypatch.setattr(valuation, "_fetch_baidu", fake_baidu)
        snap = get_valuation("a_stock", "000001")
        assert snap is not None
        assert snap.pe == pytest.approx(32.0)
        assert snap.pb == pytest.approx(3.2)
        assert snap.pe_percentile == pytest.approx(2 / 3 * 100)

    def test_all_sources_fail_returns_none_and_cooldown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts: list[str] = []

        def boom_lg(symbol: str) -> list[_ValPoint]:
            attempts.append("lg")
            raise RuntimeError("lg down")

        def boom_baidu(symbol: str) -> list[_ValPoint]:
            attempts.append("baidu")
            raise RuntimeError("baidu down")

        monkeypatch.setattr(valuation, "_fetch_lg", boom_lg)
        monkeypatch.setattr(valuation, "_fetch_baidu", boom_baidu)
        assert get_valuation("a_stock", "600519") is None
        assert attempts == ["lg", "baidu"]
        # 冷却期内再次调用：不再撞网络，直接 None
        assert get_valuation("a_stock", "600519") is None
        assert attempts == ["lg", "baidu"]
        # 冷却归零后允许重试
        monkeypatch.setattr(valuation, "RETRY_COOLDOWN_SECONDS", 0)
        assert get_valuation("a_stock", "600519") is None
        assert attempts == ["lg", "baidu", "lg", "baidu"]

    def test_non_a_stock_returns_none_without_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def must_not_call(symbol: str) -> list[_ValPoint]:
            raise AssertionError("不应发起网络拉取")

        monkeypatch.setattr(valuation, "_fetch_lg", must_not_call)
        assert get_valuation("etf", "510300") is None
        assert get_valuation("us", "105.MSFT") is None
        assert get_valuation("a_stock", "5103") is None  # 非六位数字

    def test_window_truncates_old_points(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """超出 10 年窗口的远古点不参与分位。"""
        today = date.today()
        points = [
            _ValPoint(day=today - timedelta(days=4000), pe=1.0, pb=0.1),  # 约11年前，剔除
            _ValPoint(day=today - timedelta(days=2000), pe=20.0, pb=2.0),
            _ValPoint(day=today - timedelta(days=1000), pe=30.0, pb=3.0),
            _ValPoint(day=today, pe=25.0, pb=2.5),
        ]

        monkeypatch.setattr(valuation, "_fetch_lg", lambda s: points)
        snap = get_valuation("a_stock", "600519")
        assert snap is not None
        # 窗口内 [20,30,25]：比 25 小的 1 个 → 33.3%；远古点 1.0 不计入
        assert snap.pe_percentile == pytest.approx(1 / 3 * 100)
        assert snap.pb_percentile == pytest.approx(1 / 3 * 100)

    def test_missing_pe_keeps_pb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        today = date.today()
        points = [
            _ValPoint(day=today - timedelta(days=2), pe=None, pb=1.0),
            _ValPoint(day=today - timedelta(days=1), pe=None, pb=1.5),
            _ValPoint(day=today, pe=None, pb=2.0),
        ]
        monkeypatch.setattr(valuation, "_fetch_lg", lambda s: points)
        snap = get_valuation("a_stock", "600519")
        assert snap is not None
        assert snap.pe is None
        assert snap.pe_percentile is None
        assert snap.pb == pytest.approx(2.0)
        # pb 序列 [1.0, 1.5, 2.0]：比 2.0 小的 2 个 → 66.67%
        assert snap.pb_percentile == pytest.approx(2 / 3 * 100)


class TestSnapshotEquivalence:
    def test_snapshot_is_frozen_dataclass(self) -> None:
        snap = ValuationSnapshot(pe=1.0, pb=2.0, pe_percentile=3.0, pb_percentile=4.0, window_years=10)
        with pytest.raises(Exception):
            snap.pe = 9.0  # type: ignore[misc]
