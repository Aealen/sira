# -*- coding: utf-8 -*-
"""资讯 API：列表/详情（结构化影响标签）、采集与分析调试入口、自选命中聚合。

契约（前端原型：屏4·资讯 z3Hq0C / 二级·资讯阅读 eoMub）：
- GET  /api/news                    列表：过滤 category、模糊 q，items 内嵌影响标签简版
- GET  /api/news/{id}               详情：impacts 全文（quote/fact/confidence）
- POST /api/news/collect            调试入口：手动触发一轮采集，返回新增条数
- POST /api/news/analyze            调试入口：手动触发一轮 pending 分析
- GET  /api/news/watchlist-digest   今日命中自选的影响聚合（"今日影响你自选"卡片）

方向语义：positive=利好（红涨）、negative=利空（绿跌），与全站涨跌语义同向。
免责纪律：AI/规则生成的分析仅供参考，展示层固定携带免责标注。
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models_news import News, NewsImpact, NewsImpactTarget, ensure_tables
from app.services.news import analyze_pending, collect_once

router = APIRouter(prefix="/api/news", tags=["news"])

_MAX_LIMIT = 100


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class TargetOut(BaseModel):
    type: str  # industry/stock/etf/fund
    name: str
    code: str
    hit_watchlist: bool


class ImpactBriefOut(BaseModel):
    """列表用影响标签简版（详情才带 quote/fact/confidence）。"""

    direction: str
    strength: str
    horizon: str
    logic: str
    targets: list[TargetOut]


class ImpactFullOut(ImpactBriefOut):
    """详情用全文：含信息点层（quote/fact）与置信度。"""

    quote: str
    fact: str
    confidence: float


class NewsItemOut(BaseModel):
    id: int
    source: str
    category: str
    title: str
    summary: str
    published_at: datetime | None = None
    analysis_status: str  # pending/ready/failed
    impacts: list[ImpactBriefOut] = []


class NewsListOut(BaseModel):
    total: int
    items: list[NewsItemOut]


class NewsDetailOut(NewsItemOut):
    content_url: str | None = None
    fetched_at: datetime | None = None
    impacts: list[ImpactFullOut] = []


class DigestOut(BaseModel):
    code: str
    name: str
    direction: str
    count: int
    latest_title: str


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------


def _targets_out(impact: NewsImpact) -> list[TargetOut]:
    return [
        TargetOut(type=t.type, name=t.name, code=t.code or "", hit_watchlist=bool(t.hit_watchlist))
        for t in impact.targets
    ]


def _impacts_brief(news: News) -> list[ImpactBriefOut]:
    return [
        ImpactBriefOut(
            direction=imp.direction,
            strength=imp.strength,
            horizon=imp.horizon,
            logic=imp.logic,
            targets=_targets_out(imp),
        )
        for imp in news.impacts
    ]


def _impacts_full(news: News) -> list[ImpactFullOut]:
    return [
        ImpactFullOut(
            direction=imp.direction,
            strength=imp.strength,
            horizon=imp.horizon,
            logic=imp.logic,
            targets=_targets_out(imp),
            quote=imp.quote,
            fact=imp.fact,
            confidence=imp.confidence,
        )
        for imp in news.impacts
    ]


# ---------------------------------------------------------------------------
# 端点（注意：/watchlist-digest 必须先于 /{news_id} 注册，避免被路径参数吞掉）
# ---------------------------------------------------------------------------


@router.get("/watchlist-digest", response_model=list[DigestOut])
def watchlist_digest(db: Session = Depends(get_db)) -> list[DigestOut]:
    """今日命中自选的影响聚合：按 (code, name, direction) 分组计数，附最新一条标题。

    "今日"按源发布时间 published_at 判定，缺失时回退本地抓取时间 fetched_at。
    """
    ensure_tables()
    today = date.today()
    stmt = (
        select(NewsImpactTarget, NewsImpact, News)
        .join(NewsImpact, NewsImpactTarget.impact_id == NewsImpact.id)
        .join(News, NewsImpact.news_id == News.id)
        .where(
            NewsImpactTarget.hit_watchlist.is_(True),
            func.date(func.coalesce(News.published_at, News.fetched_at)) == today,
        )
        .order_by(News.id.desc())  # 每组首见即最新
    )
    agg: dict[tuple[str, str, str], dict] = {}
    for tgt, imp, n in db.execute(stmt):
        key = (tgt.code or "", tgt.name, imp.direction)
        if key not in agg:
            agg[key] = {
                "code": tgt.code or "",
                "name": tgt.name,
                "direction": imp.direction,
                "count": 0,
                "latest_title": n.title,
            }
        agg[key]["count"] += 1
    return [DigestOut(**v) for v in agg.values()]


@router.get("", response_model=NewsListOut)
def list_news(
    category: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> NewsListOut:
    """资讯列表（新→旧），内嵌影响标签简版；total 为过滤后总数（分页用）。"""
    ensure_tables()
    conditions = []
    if category:
        conditions.append(News.category == category)
    if q:
        like = f"%{q}%"
        conditions.append(or_(News.title.like(like), News.summary.like(like)))
    base = select(News)
    if conditions:
        base = base.where(*conditions)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        db.scalars(
            base.options(selectinload(News.impacts).selectinload(NewsImpact.targets))
            .order_by(News.id.desc())
            .offset(max(offset, 0))
            .limit(max(min(limit, _MAX_LIMIT), 1))
        )
        .all()
    )
    items = [
        NewsItemOut(
            id=n.id,
            source=n.source,
            category=n.category,
            title=n.title,
            summary=n.summary,
            published_at=n.published_at,
            analysis_status=n.analysis_status,
            impacts=_impacts_brief(n),
        )
        for n in rows
    ]
    return NewsListOut(total=total, items=items)


@router.get("/{news_id}", response_model=NewsDetailOut)
def get_news(news_id: int, db: Session = Depends(get_db)) -> NewsDetailOut:
    """资讯详情：impacts 全文（原文摘录 quote / 归纳事实 fact / 置信度 confidence）。"""
    ensure_tables()
    n = db.scalars(
        select(News)
        .options(selectinload(News.impacts).selectinload(NewsImpact.targets))
        .where(News.id == news_id)
    ).first()
    if n is None:
        raise HTTPException(status_code=404, detail=f"资讯不存在：id={news_id}")
    return NewsDetailOut(
        id=n.id,
        source=n.source,
        category=n.category,
        title=n.title,
        summary=n.summary,
        published_at=n.published_at,
        analysis_status=n.analysis_status,
        content_url=n.content_url,
        fetched_at=n.fetched_at,
        impacts=_impacts_full(n),
    )


@router.post("/collect")
def trigger_collect(db: Session = Depends(get_db)) -> dict:
    """调试入口：手动触发一轮采集（真实链路同调度器 collect_once）。"""
    return {"added": collect_once(db=db)}


@router.post("/analyze")
def trigger_analyze(limit: int = 20, db: Session = Depends(get_db)) -> dict:
    """调试入口：手动触发一轮 pending 新闻影响分析。"""
    return {"analyzed": analyze_pending(db=db, limit=limit)}
