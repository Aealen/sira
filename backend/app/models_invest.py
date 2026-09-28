# -*- coding: utf-8 -*-
"""ORM 模型。M5：定投计划与执行流水。

注意：本模块为独立新增文件，需在 init_db 处追加 `import app.models_invest`
（主会话接线，M5 不修改 app/db.py）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InvestPlan(Base):
    """定投计划。同一标的允许建立多个计划，故不对 (market, code) 做唯一约束。"""

    __tablename__ = "invest_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)  # a_stock/etf/fund/hk/us
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    frequency: Mapped[str] = mapped_column(String(16))  # monthly/biweekly/weekly
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)  # 1-28（仅 monthly 生效）
    amount: Mapped[float] = mapped_column(Float)  # 每期基准金额（元）
    smart_dca: Mapped[bool] = mapped_column(Boolean, default=False)  # 智能加减速
    take_profit_mode: Mapped[str] = mapped_column(String(16), default="none")  # none/target_return/valuation
    take_profit_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # 收益率% 或估值分位
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active/paused
    last_exec_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class InvestExecution(Base):
    """定投执行流水（每期买入一条，amount 含手续费）。"""

    __tablename__ = "invest_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("invest_plans.id", ondelete="CASCADE"), index=True
    )
    exec_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    price: Mapped[float] = mapped_column(Float)  # 成交价 / 当日单位净值
    quantity: Mapped[float] = mapped_column(Float)  # 成交数量（股/份）
    amount: Mapped[float] = mapped_column(Float)  # 本期投入金额（元，含费）
    fee: Mapped[float] = mapped_column(Float, default=0.0)  # 手续费（元）
