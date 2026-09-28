# -*- coding: utf-8 -*-
"""M2 分析模块 ORM 模型：用户风险偏好（单行）。风格与 app/models.py 一致。"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RiskProfile(Base):
    """风险偏好档案（全局单行，id=1）。

    对应知识库《风险衡量指标》第六节"用最大回撤倒推仓位"：
    仓位上限 ≈ 可承受回撤(max_drawdown_tolerance) ÷ 标的历史最大回撤。
    """

    __tablename__ = "risk_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_drawdown_tolerance: Mapped[float] = mapped_column(Float, default=0.15)  # 能承受的最大回撤（0.15=亏15%睡不着）
    horizon: Mapped[str] = mapped_column(String(8), default="3y")  # 投资期限：1y/3y/5y/10y
    target_return: Mapped[float] = mapped_column(Float, default=0.08)  # 目标年化收益
    attitude: Mapped[str] = mapped_column(String(16), default="balanced")  # conservative/balanced/aggressive
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
