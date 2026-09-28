# -*- coding: utf-8 -*-
"""ORM 模型。M1：标的字典、快照缓存、K 线/净值缓存；后续阶段逐步扩展。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Instrument(Base):
    """标的字典（market + code 唯一）。"""

    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("market", "code", name="uq_instrument"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)  # a_stock/etf/fund/hk/us
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SpotCache(Base):
    """全市场快照的持久化兜底缓存（akshare 拉取失败时返回最近一次数据）。"""

    __tablename__ = "spot_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    payload: Mapped[str] = mapped_column(Text)  # JSON: [[code,name,price,...],...]
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class KlineCache(Base):
    """日 K 线缓存（含场外基金净值，open=close=high=low=单位净值）。"""

    __tablename__ = "kline_cache"
    __table_args__ = (UniqueConstraint("market", "code", "date", name="uq_kline"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)
