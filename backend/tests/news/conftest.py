# -*- coding: utf-8 -*-
"""M4 资讯模块测试夹具：独立内存库 + 仅挂载 news 路由的 TestClient。

与顶层 tests/conftest.py（M5 投资模块会话）互不干扰：
- fixture 独立命名（news_db / news_client），不覆盖父级同名能力；
- 内存库 + StaticPool，绝不触碰 data/app.db；
- 屏蔽 ensure_tables() 对真实库的建表副作用（内存表由本夹具 create_all）。
"""
import sys
from collections.abc import Generator
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  注册 M1 表（watchlist 等）
import app.models_news  # noqa: F401  注册 M4 资讯表
from app.db import Base, get_db
from app.routers import news as news_router_module


@pytest.fixture()
def news_db() -> Generator[Session, None, None]:
    """干净的内存库会话：全部表结构就绪，每个测试相互隔离。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    # 本测试进程内视为"表已建好"，避免 ensure_tables 落到真实 data/app.db
    original_ensured = app.models_news._ensured
    app.models_news._ensured = True
    try:
        yield session
    finally:
        app.models_news._ensured = original_ensured
        session.close()
        engine.dispose()


@pytest.fixture()
def news_client(news_db: Session) -> Generator[TestClient, None, None]:
    """仅挂载 news 路由的独立应用；依赖注入覆盖为内存会话。"""
    api = FastAPI()
    api.include_router(news_router_module.router)

    def _override_get_db() -> Generator[Session, None, None]:
        yield news_db

    api.dependency_overrides[get_db] = _override_get_db
    with TestClient(api) as client:
        yield client
