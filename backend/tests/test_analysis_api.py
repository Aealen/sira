# -*- coding: utf-8 -*-
"""analysis / profile API 单测：内存 SQLite + mock 数据源，验证契约结构与 ref_position 计算。"""
from __future__ import annotations

import math
from collections.abc import Generator
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.analysis as analysis_mod
from app.db import Base, get_db
from app.models import Watchlist  # noqa: F401  触发注册，保证 create_all 建齐表
from app.models_analysis import RiskProfile  # noqa: F401
from app.routers.analysis import router
from app.services.datasource.base import Bar, SearchResult
from app.services.indicators import max_drawdown


# ---------------------------------------------------------------------------
# 测试环境：内存库 + 假行情源
# ---------------------------------------------------------------------------


def make_bars(n: int = 800) -> list[Bar]:
    """确定性正弦+趋势序列：约 3 年+ 的日K，天然含多轮回撤。"""
    d0 = date(2023, 1, 1)
    return [
        Bar(
            date=(d0 + timedelta(days=i)).isoformat(),
            open=0.0, high=0.0, low=0.0,
            close=100 + 30 * math.sin(i / 40) + i * 0.01,
            volume=0.0, change_pct=0.0,
        )
        for i in range(n)
    ]


BARS = make_bars()
ETF_NAME = "测试沪深300ETF"


class FakeSource:
    market = "etf"

    def search(self, keyword: str, limit: int = 20) -> list[SearchResult]:
        return [SearchResult(market="etf", code=keyword, name=ETF_NAME)]

    def get_quote(self, code: str):  # pragma: no cover - analysis 不调用
        raise NotImplementedError

    def get_kline(self, code: str, days: int = 250) -> tuple[list[Bar], bool]:
        return BARS[-days:], False


def fake_get_source(market: str) -> FakeSource:
    if market != "etf":
        raise KeyError(f"不支持的市场: {market}")
    return FakeSource()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(analysis_mod, "get_source", fake_get_source)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/analysis/{market}/{code}
# ---------------------------------------------------------------------------


class TestAnalysisEndpoint:
    def test_structure_and_indicators(self, client: TestClient) -> None:
        r = client.get("/api/analysis/etf/510300", params={"days": 250})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["market"] == "etf"
        assert body["code"] == "510300"
        assert body["name"] == ETF_NAME
        assert len(body["bars"]) == 250
        assert set(body["bars"][0]) == {"date", "open", "high", "low", "close", "volume", "change_pct"}

        ind = body["indicators"]
        for key in (
            "ann_volatility", "max_drawdown", "max_drawdown_start", "max_drawdown_end",
            "sharpe", "sortino", "daily_mean", "daily_std", "worst_daily",
        ):
            assert key in ind and ind[key] is not None, f"{key} 应有值"
        assert ind["max_drawdown"] < 0
        assert ind["worst_daily"] < 0
        assert ind["max_drawdown_start"] <= ind["max_drawdown_end"]

        # etf 无估值：M2 明确返回 null
        assert body["valuation"] is None

    def test_ref_position_matches_handoff_formula(self, client: TestClient) -> None:
        r = client.get("/api/analysis/etf/510300", params={"days": 250})
        body = r.json()
        ref = body["ref_position"]
        assert ref["tolerance"] == pytest.approx(0.15)  # RiskProfile 默认

        dd = max_drawdown(BARS)  # 全量（>=750 根 ≈ 3年）窗口
        assert dd is not None
        assert ref["hist_max_drawdown"] == pytest.approx(dd.magnitude)
        expected = min(0.15 / abs(dd.magnitude) * 100, 100.0)
        assert ref["suggested_max_pct"] == pytest.approx(expected)
        assert 0 < ref["suggested_max_pct"] <= 100

    def test_days_window_respected(self, client: TestClient) -> None:
        r = client.get("/api/analysis/etf/510300", params={"days": 10})
        assert r.status_code == 200
        assert len(r.json()["bars"]) == 10

    def test_days_validation(self, client: TestClient) -> None:
        assert client.get("/api/analysis/etf/510300", params={"days": 5}).status_code == 422
        assert client.get("/api/analysis/etf/510300", params={"days": 9999}).status_code == 422

    def test_unknown_market_404(self, client: TestClient) -> None:
        r = client.get("/api/analysis/xxx/510300")
        assert r.status_code == 404
        assert "不支持的市场" in r.text

    def test_a_stock_valuation_passed_through(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.valuation import ValuationSnapshot

        # 让 a_stock 走假行情源，并注入假估值快照
        class AStockSource(FakeSource):
            market = "a_stock"

        monkeypatch.setattr(
            analysis_mod, "get_source",
            lambda m: AStockSource() if m == "a_stock" else fake_get_source(m),
        )
        monkeypatch.setattr(
            analysis_mod, "get_valuation",
            lambda market, code: ValuationSnapshot(
                pe=18.9, pb=3.2, pe_percentile=25.0, pb_percentile=60.0, window_years=10,
            ),
        )
        r = client.get("/api/analysis/a_stock/600519")
        assert r.status_code == 200, r.text
        val = r.json()["valuation"]
        assert val == {"pe": 18.9, "pb": 3.2, "pe_percentile": 25.0, "pb_percentile": 60.0, "window_years": 10}


# ---------------------------------------------------------------------------
# GET/PUT /api/profile
# ---------------------------------------------------------------------------


class TestProfileEndpoint:
    def test_get_defaults(self, client: TestClient) -> None:
        r = client.get("/api/profile")
        assert r.status_code == 200
        body = r.json()
        assert body["max_drawdown_tolerance"] == pytest.approx(0.15)
        assert body["horizon"] == "3y"
        assert body["target_return"] == pytest.approx(0.08)
        assert body["attitude"] == "balanced"
        assert body["updated_at"] is None  # 未落库的默认行无时间戳

    def test_put_full_then_get(self, client: TestClient) -> None:
        payload = {
            "max_drawdown_tolerance": 0.2,
            "horizon": "5y",
            "target_return": 0.1,
            "attitude": "aggressive",
        }
        r = client.put("/api/profile", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_drawdown_tolerance"] == pytest.approx(0.2)
        assert body["attitude"] == "aggressive"
        assert body["updated_at"] is not None

        g = client.get("/api/profile").json()
        assert g["horizon"] == "5y"
        assert g["target_return"] == pytest.approx(0.1)

    def test_put_partial_update_keeps_rest(self, client: TestClient) -> None:
        client.put("/api/profile", json={"max_drawdown_tolerance": 0.3, "attitude": "conservative"})
        r = client.put("/api/profile", json={"horizon": "1y"})
        assert r.status_code == 200
        body = r.json()
        assert body["horizon"] == "1y"
        assert body["max_drawdown_tolerance"] == pytest.approx(0.3)  # 保留
        assert body["attitude"] == "conservative"  # 保留

    def test_put_validation(self, client: TestClient) -> None:
        assert client.put("/api/profile", json={"max_drawdown_tolerance": 1.5}).status_code == 422
        assert client.put("/api/profile", json={"attitude": "yolo"}).status_code == 422
        assert client.put("/api/profile", json={"horizon": "2y"}).status_code == 422
