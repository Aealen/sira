# -*- coding: utf-8 -*-
"""模拟盘 ORM 模型（M3）：账户、持仓、委托、成交流水、标的名称轻量缓存。"""
from datetime import date, datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base


class SimAccount(Base):
    """模拟账户（单行，id 恒为 1）。last_roll_date 记录 T+1 解锁最近滚动日。"""

    __tablename__ = "sim_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash: Mapped[float] = mapped_column(Float, default=settings.initial_cash)
    # T+1 滚动日（YYYY-MM-DD）：跨日后历史买入量解锁为可卖，见 matching.ensure_new_day
    last_roll_date: Mapped[str] = mapped_column(String(10), default=lambda: date.today().isoformat())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SimPosition(Base):
    """模拟持仓（market + code 唯一）。available_qty 为 T+1 口径下的可卖数量。"""

    __tablename__ = "sim_position"
    __table_args__ = (UniqueConstraint("market", "code", name="uq_sim_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)  # a_stock/etf/fund/hk/us
    code: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)              # 持仓数量（股/份）
    available_qty: Mapped[int] = mapped_column(Integer)         # 可卖数量（T+1：昨日及以前买入量）
    avg_cost: Mapped[float] = mapped_column(Float)              # 加权平均成本（含买入费用）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class SimOrder(Base):
    """委托单：open 挂单 / filled 已成交 / cancelled 已撤单 / rejected 已拒单。"""

    __tablename__ = "sim_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")  # 下单时缓存标的名（回执展示）
    side: Mapped[str] = mapped_column(String(8))                # buy/sell
    order_type: Mapped[str] = mapped_column(String(8))          # market/limit
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 限价委托价（市价单为空）
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 成交价
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)  # 拒单原因（中文，面向用户）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SimTrade(Base):
    """成交流水（含费用明细，费用四项互斥组合：场内=佣金(+印花税)，场外=申购/赎回费）。"""

    __tablename__ = "sim_trade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    side: Mapped[str] = mapped_column(String(8))    # buy/sell
    price: Mapped[float] = mapped_column(Float)     # 成交价（场外基金为成交净值）
    quantity: Mapped[int] = mapped_column(Integer)  # 成交数量（股/份）
    amount: Mapped[float] = mapped_column(Float)    # 成交金额 = price × quantity
    commission: Mapped[float] = mapped_column(Float, default=0.0)          # 佣金（万2.5 最低5，场外基金为 0）
    stamp_tax: Mapped[float] = mapped_column(Float, default=0.0)           # 印花税（A 股/港股 卖出）
    subscription_fee: Mapped[float] = mapped_column(Float, default=0.0)    # 场外基金申购费
    redemption_fee: Mapped[float] = mapped_column(Float, default=0.0)      # 场外基金赎回费（持有期阶梯待实现）
    total_fee: Mapped[float] = mapped_column(Float, default=0.0)           # 费用合计
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SimInstrument(Base):
    """标的名称轻量缓存（下单/挂单时写入，持仓与回执展示用，避免每次走行情搜索）。"""

    __tablename__ = "sim_instrument"
    __table_args__ = (UniqueConstraint("market", "code", name="uq_sim_instrument"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
