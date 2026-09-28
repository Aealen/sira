# -*- coding: utf-8 -*-
"""数据库连接与会话管理（SQLite）。"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

settings.database_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.database_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    import app.models  # noqa: F401  确保模型已注册
    import app.models_analysis  # noqa: F401  风险偏好表（M2）
    import app.models_invest  # noqa: F401  定投计划与执行流水（M5）
    import app.models_news  # noqa: F401  资讯与影响标签（M4）
    import app.models_sim  # noqa: F401  模拟盘账户/持仓/委托/流水（M3）

    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
