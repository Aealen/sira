# -*- coding: utf-8 -*-
"""模拟盘（M3）测试夹具：独立内存库 + 假行情源 + 撮合时钟固定。

与并行模块（invest/analysis/news）的 tests/conftest.py 隔离：
fixture 命名加 sim_ 前缀，避免与父级同名夹具互相覆盖。
"""
from collections.abc import Callable
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.models_sim import SimAccount, SimInstrument, SimOrder, SimPosition, SimTrade
from app.routers import sim as sim_router
from app.services import matching
from app.services.datasource.base import Bar, Quote, SearchResult

SIM_TABLES = (
    SimAccount.__table__, SimPosition.__table__, SimOrder.__table__,
    SimTrade.__table__, SimInstrument.__table__,
)


@pytest.fixture()
def sim_db() -> Session:
    """每个测试独立的内存 SQLite 会话（仅建模拟盘表，不依赖并行模块模型）。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=SIM_TABLES)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class FakeSource:
    """假行情源：预设 Quote 表（对象可变，测试中途改价触发限价触及）+ 场外基金净值。"""

    def __init__(self, market: str, quotes: dict[str, Quote], nav: float | None = None) -> None:
        self.market = market
        self._quotes = quotes
        self._nav = nav

    def get_quote(self, code: str) -> Quote:
        q = self._quotes.get(code)
        if q is None:
            raise LookupError(f"{self.market} 中未找到标的 {code}")
        return q

    def get_kline(self, code: str, days: int = 250) -> tuple[list[Bar], bool]:
        if self._nav is None:
            return [], False
        bar = Bar(date="2026-09-29", open=self._nav, high=self._nav, low=self._nav, close=self._nav)
        return [bar], False

    def search(self, keyword: str, limit: int = 20) -> list[SearchResult]:
        q = self._quotes.get(keyword)
        name = q.name if q else keyword
        return [SearchResult(market=self.market, code=keyword, name=name)][:limit]


@pytest.fixture()
def fake_market(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeSource]:
    """替换 matching 内的 get_source 为假行情源注册表；返回注册函数（同市场重复注册覆盖旧的）。"""
    fakes: dict[str, FakeSource] = {}

    def register(market: str, quotes: dict[str, Quote] | None = None, nav: float | None = None) -> FakeSource:
        src = FakeSource(market, quotes or {}, nav)
        fakes[market] = src
        return src

    monkeypatch.setattr(matching, "get_source", lambda m: fakes[m])
    return register


@pytest.fixture()
def trading_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """撮合时钟固定在 2026-09-30（周三）10:00（A股交易时段内）。"""
    monkeypatch.setattr(matching, "_now", lambda: datetime(2026, 9, 30, 10, 0))
    monkeypatch.setattr(matching, "_today", lambda: "2026-09-30")


@pytest.fixture()
def sim_client(sim_db: Session, fake_market, trading_now) -> TestClient:
    """挂载 sim 路由的测试应用（依赖注入内存会话；不改 app/main.py）。"""
    test_app = FastAPI()
    test_app.include_router(sim_router.router)
    test_app.dependency_overrides[get_db] = lambda: sim_db
    with TestClient(test_app) as client:
        yield client
