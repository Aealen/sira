# -*- coding: utf-8 -*-
"""资讯采集（两段式管道第一段）：财联社电报快讯 → 去重入库 → 待分析队列。

设计要点（docs/04-工具与实现/资讯模块设计.md 第三节）：
- 即时入库、即时展示（带延迟标记），保实时；分析深度由第二段（analyzer）解耦
- 去重：标题 SHA-256 作唯一键（title_hash），重复标题不入库
- 初分类：标题关键词规则映射（宏观/行业/公告/研报，默认宏观）
- 容错：拉取失败记日志返回 0，绝不向上抛（不阻塞调度器与列表接口）

数据源：akshare `stock_info_global_cls()`（财联社电报，分钟级快讯流）。
akshare 列名为中文且随版本可能微调，本模块宽容映射、缺列安全降级。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models_news import News, ensure_tables

logger = logging.getLogger(__name__)

SOURCE_NAME = "财联社电报"

# ---------------------------------------------------------------------------
# 分类规则：标题关键词初分类，优先级 公告 > 研报 > 宏观 > 行业，默认宏观
# ---------------------------------------------------------------------------

CATEGORY_MACRO = "宏观"
CATEGORY_INDUSTRY = "行业"
CATEGORY_ANNOUNCEMENT = "公告"
CATEGORY_REPORT = "研报"

ANNOUNCEMENT_WORDS = ("公告", "披露", "重大事项", "停牌", "复牌", "减持计划", "业绩预告", "中标公告")
REPORT_WORDS = ("研报", "研究报告", "评级", "目标价", "首次覆盖", "维持买入")
MACRO_WORDS = (
    "央行", "美联储", "LPR", "降准", "降息", "加息", "GDP", "CPI", "PPI", "PMI",
    "货币政策", "财政", "国债", "汇率", "国常会", "证监会", "宏观", "逆回购", "MLF",
)
INDUSTRY_WORDS = (
    "白酒", "新能源", "动力电池", "锂电池", "光伏", "半导体", "芯片", "人工智能",
    "消费电子", "券商", "银行", "保险", "房地产", "医药", "黄金", "原油", "石油",
    "煤炭", "钢铁", "军工", "汽车", "电力", "农业", "传媒", "游戏", "旅游", "航空",
    "化工", "有色", "机械", "家电", "食品饮料", "互联网",
)


def classify_category(title: str) -> str:
    """标题关键词初分类：公告/研报特征词最强，其次宏观政策词，再行业词，默认宏观。"""
    if any(w in title for w in ANNOUNCEMENT_WORDS):
        return CATEGORY_ANNOUNCEMENT
    if any(w in title for w in REPORT_WORDS):
        return CATEGORY_REPORT
    if any(w in title for w in MACRO_WORDS):
        return CATEGORY_MACRO
    if any(w in title for w in INDUSTRY_WORDS):
        return CATEGORY_INDUSTRY
    return CATEGORY_MACRO


def title_hash(title: str) -> str:
    """标题去重键：strip 后 SHA-256（同标题不同空白视为重复）。"""
    return hashlib.sha256((title or "").strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 拉取与解析（akshare 隔离层：列名宽容映射，坏行跳过）
# ---------------------------------------------------------------------------

_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_published(row) -> datetime | None:
    """组合'发布日期 + 发布时间'为 datetime；解析失败返回 None（展示层回退 fetched_at）。"""
    d = str(row.get("发布日期") or row.get("日期") or "").strip()
    t = str(row.get("发布时间") or row.get("时间") or "").strip()
    for candidate in (f"{d} {t}".strip(), t, d):
        if not candidate:
            continue
        for fmt in _DATETIME_FORMATS:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def _fetch_cls_news() -> list[dict]:
    """拉取财联社电报，归一为 [{title, summary, published_at}]。

    akshare 延迟 import：模块加载零成本（测试 monkeypatch 本函数时完全不触发网络栈）。
    """
    import akshare as ak

    df = ak.stock_info_global_cls()
    records: list[dict] = []
    for _, row in df.iterrows():
        title = str(row.get("标题") or row.get("title") or "").strip()
        if not title or title in ("nan", "None"):
            continue
        summary = str(row.get("内容") or row.get("content") or "").strip()
        records.append(
            {
                "title": title,
                "summary": summary if summary not in ("nan", "None") else "",
                "published_at": _parse_published(row),
            }
        )
    return records


# ---------------------------------------------------------------------------
# 入库与调度
# ---------------------------------------------------------------------------


def _collect(db: Session) -> int:
    try:
        records = _fetch_cls_news()
    except Exception:
        logger.exception("财联社电报拉取失败，本轮采集跳过（返回 0，不抛出）")
        return 0

    # 批次内按 hash 去重（保序），再与库中已有 hash 批量比对，避免逐条 SELECT
    batch: dict[str, dict] = {}
    for rec in records:
        h = title_hash(rec["title"])
        batch.setdefault(h, rec)
    if not batch:
        return 0

    existing = set(
        db.scalars(select(News.title_hash).where(News.title_hash.in_(batch.keys()))).all()
    )

    added = 0
    for h, rec in batch.items():
        if h in existing:
            continue
        db.add(
            News(
                source=SOURCE_NAME,
                category=classify_category(rec["title"]),
                title=rec["title"],
                summary=rec["summary"],
                content_url=None,  # 电报接口无原文链接
                published_at=rec["published_at"],
                title_hash=h,
            )
        )
        added += 1
    if added:
        db.commit()
        logger.info("财联社电报本轮新增 %s 条（源共 %s 条，库中已存在 %s 条）", added, len(batch), len(existing))
    return added


def collect_once(db: Session | None = None) -> int:
    """采集一轮财联社电报，返回本轮新增条数。

    - 失败（网络/解析异常）记日志返回 0，不抛出，保证调度器与列表接口可用性
    - db 缺省时自开短生命周期 Session（供调度器线程调用）；API 层可传入请求级 Session
    """
    ensure_tables()
    if db is not None:
        return _collect(db)
    with SessionLocal() as session:
        return _collect(session)


_scheduler = None  # BackgroundScheduler | None（类型注解放字符串避免顶层重依赖）


def init_scheduler(interval_seconds: int = 60):
    """创建并启动采集调度器（每 interval_seconds 一轮 collect_once，立即先跑一轮）。

    只定义不自动启动——由主会话在 FastAPI startup 中调用一次完成接线：
        from app.services.news import init_scheduler
        init_scheduler()  # 60s 一轮
    幂等：已启动时直接返回既有实例。
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        collect_once,
        "interval",
        seconds=interval_seconds,
        id="news_collect",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(),  # 启动即先采一轮，不等第一个完整间隔
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("资讯采集调度器已启动：每 %ss 一轮财联社电报采集", interval_seconds)
    return scheduler
