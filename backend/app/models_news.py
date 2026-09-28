# -*- coding: utf-8 -*-
"""M4 资讯模块 ORM 模型：News / NewsImpact / NewsImpactTarget。

对应设计文档：docs/04-工具与实现/资讯模块设计.md 第二节（三层影响标签）与第四节（数据模型）。

三层结构：
- News          资讯本体（两段式管道第一段：采集即时入库，analysis_status 跟踪第二段）
- NewsImpact    信息点层（quote 原文摘录 / fact 归纳事实）+ 影响描述层（direction/strength/
                horizon/logic/confidence）
- NewsImpactTarget  影响对象层（type/name/code + hit_watchlist/hit_portfolio 命中冗余存储）

方向语义遵循全站"红涨绿跌"惯例：positive=利好、negative=利空。
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, engine

# ---------------------------------------------------------------------------
# schema 常量（分析/采集/展示共用，避免魔法字符串散落）
# ---------------------------------------------------------------------------

# 分析状态
STATUS_PENDING = "pending"  # 已入库待分析
STATUS_READY = "ready"      # 分析完成（含"无影响映射"的中性结果）
STATUS_FAILED = "failed"    # 分析异常（后期可手动重试）

# 影响方向（positive=利好，红涨；negative=利空，绿跌）
DIRECTION_POSITIVE = "positive"
DIRECTION_NEGATIVE = "negative"
DIRECTION_NEUTRAL = "neutral"

# 影响强度
STRENGTH_STRONG = "strong"
STRENGTH_MEDIUM = "medium"
STRENGTH_WEAK = "weak"

# 影响时限（中文枚举，直接对齐产品文案）
HORIZON_SHORT = "短期情绪"
HORIZON_MIDDLE = "中期基本面"
HORIZON_LONG = "长期格局"


class News(Base):
    """资讯本体。title_hash（标题 SHA-256）为唯一去重键，重复标题不入库。"""

    __tablename__ = "news"
    __table_args__ = (UniqueConstraint("title_hash", name="uq_news_title_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # 财联社电报 / 公告 / 研报精选
    category: Mapped[str] = mapped_column(String(16), index=True)  # 宏观/行业/公告/研报
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text, default="")
    content_url: Mapped[str | None] = mapped_column(String(512), default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # 源发布时间，解析失败可空
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 本地抓取时间
    analysis_status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, index=True)
    title_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    impacts: Mapped[list["NewsImpact"]] = relationship(
        back_populates="news", cascade="all, delete-orphan", passive_deletes=True
    )


class NewsImpact(Base):
    """影响标签：一条 News 可挂多条（一对多）。quote 强制摘录，防幻觉可回溯。"""

    __tablename__ = "news_impact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    news_id: Mapped[int] = mapped_column(
        ForeignKey("news.id", ondelete="CASCADE"), index=True
    )
    quote: Mapped[str] = mapped_column(Text, default="")  # 原文摘录（判断依据，强制）
    fact: Mapped[str] = mapped_column(Text, default="")  # 归纳事实（一句话）
    direction: Mapped[str] = mapped_column(String(16), index=True)  # positive/negative/neutral
    strength: Mapped[str] = mapped_column(String(16))  # strong/medium/weak
    horizon: Mapped[str] = mapped_column(String(16))  # 短期情绪/中期基本面/长期格局
    logic: Mapped[str] = mapped_column(Text, default="")  # 传导链一句话
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1，低于阈值按中性处理
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    news: Mapped["News"] = relationship(back_populates="impacts")
    targets: Mapped[list["NewsImpactTarget"]] = relationship(
        back_populates="impact", cascade="all, delete-orphan", passive_deletes=True
    )


class NewsImpactTarget(Base):
    """影响对象层：一条影响可挂多个对象（行业/个股/ETF/基金）。

    命中标记（hit_watchlist/hit_portfolio）在分析时冗余写入，避免展示时反查
    （设计文档第四节）；hit_portfolio 对接模拟盘持仓，二期接线。
    """

    __tablename__ = "news_impact_target"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    impact_id: Mapped[int] = mapped_column(
        ForeignKey("news_impact.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(16))  # industry/stock/etf/fund
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(32), default="")  # 行业类对象无代码，留空
    hit_watchlist: Mapped[bool] = mapped_column(Boolean, default=False)  # 命中自选（前端高亮 ✦）
    hit_portfolio: Mapped[bool] = mapped_column(Boolean, default=False)  # 命中模拟盘持仓（二期）

    impact: Mapped["NewsImpact"] = relationship(back_populates="targets")


_ensured = False


def ensure_tables() -> None:
    """幂等建表（checkfirst）。

    主会话接线（init_db 前 import 本模块）后此函数为冗余安全调用；
    独立调用路径（调试 API / 测试）不依赖 app.main 也能自建表。
    """
    global _ensured
    if _ensured:
        return
    Base.metadata.create_all(
        engine,
        tables=[News.__table__, NewsImpact.__table__, NewsImpactTarget.__table__],
    )
    _ensured = True
