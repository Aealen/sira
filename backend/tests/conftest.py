# -*- coding: utf-8 -*-
"""测试夹具：独立内存库 + 挂载 invest 路由的测试专用 FastAPI 应用。

不修改 app/main.py（M5 硬性约束），路由接线由主会话完成；
测试中通过独立 app + dependency_overrides 注入内存会话。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  注册 M1 表
import app.models_invest  # noqa: F401  注册 M5 表
try:
    import app.models_sim  # noqa: F401  并行模块就绪则一并注册
except ImportError:
    pass

from app.db import Base, get_db
from app.models_invest import InvestExecution, InvestPlan
from app.routers import invest


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    test_app = FastAPI()
    test_app.include_router(invest.router)
    test_app.dependency_overrides[get_db] = override_get_db
    return TestClient(test_app)


# -- 造数工具 -------------------------------------------------------------------
def make_plan(db, **overrides) -> InvestPlan:
    """创建一个定投计划（默认：monthly/每月 1 日/1000 元/active/创建于 30 天前）。"""
    from datetime import datetime, timedelta

    defaults = dict(
        market="etf",
        code="510300",
        name="沪深300ETF",
        frequency="monthly",
        day_of_month=1,
        amount=1000.0,
        smart_dca=False,
        take_profit_mode="none",
        take_profit_value=None,
        status="active",
        last_exec_date=None,
        created_at=datetime.now() - timedelta(days=30),
    )
    defaults.update(overrides)
    plan = InvestPlan(**defaults)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def make_exec(db, plan: InvestPlan, exec_date: str, price=10.0, quantity=100.0,
              amount=1000.0, fee=0.0) -> InvestExecution:
    row = InvestExecution(
        plan_id=plan.id, exec_date=exec_date, price=price,
        quantity=quantity, amount=amount, fee=fee,
    )
    db.add(row)
    db.commit()
    return row
