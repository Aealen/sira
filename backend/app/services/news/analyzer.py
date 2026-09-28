# -*- coding: utf-8 -*-
"""影响分析（两段式管道第二段）：News(pending) → 结构化影响标签（NewsImpact/Target）。

Analyzer 协议 + 两实现：
- RuleBasedAnalyzer  纯规则：关键词→影响对象映射表 + 方向/强度词表。零成本、即时、
                     可离线，但覆盖有限、无语义理解（confidence 固定 0.5）
- LLMAnalyzer        LLM 接口骨架（二期接入 API key 后实现，见类 docstring prompt 要点）

分析纪律（设计文档第五节，两类分析器共同遵守）：
1. 依据强制摘录：direction != neutral 的判断必须附原文 quote（规则分析器取标题/摘要原文）
2. 不确定即中性：方向信号冲突或缺失时 direction=neutral、strength 降为 weak
3. 输出按三层标签 schema 落库；无映射命中的新闻置 ready 但不产生 impacts（中性，不阻塞队列）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Watchlist
from app.models_news import (
    DIRECTION_NEGATIVE,
    DIRECTION_NEUTRAL,
    DIRECTION_POSITIVE,
    HORIZON_LONG,
    HORIZON_MIDDLE,
    HORIZON_SHORT,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    STRENGTH_MEDIUM,
    STRENGTH_STRONG,
    STRENGTH_WEAK,
    News,
    NewsImpact,
    NewsImpactTarget,
    ensure_tables,
)

logger = logging.getLogger(__name__)

RULE_CONFIDENCE = 0.5  # 规则分析器固定置信度：低于 LLM 纪律阈值 0.6，方向词缺失即中性


# ---------------------------------------------------------------------------
# 草稿类型：分析器产出（不直接落库），由 analyze_pending 统一入库并解析命中
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetDraft:
    """影响对象草稿：type 见 NewsImpactTarget.type；行业类对象 code 留空。"""

    type: str  # industry/stock/etf/fund
    name: str
    code: str = ""


@dataclass
class NewsImpactDraft:
    """影响标签草稿（三层 schema 的内存形态）。"""

    quote: str
    fact: str
    direction: str  # positive/negative/neutral
    strength: str  # strong/medium/weak
    horizon: str  # 短期情绪/中期基本面/长期格局
    logic: str
    confidence: float
    targets: list[TargetDraft] = field(default_factory=list)


class Analyzer(Protocol):
    """影响分析器协议：一条 News → 一组影响草稿（可能为空=无映射中性）。"""

    def analyze(self, news: News) -> list[NewsImpactDraft]: ...


# ---------------------------------------------------------------------------
# 规则分析器：映射表 + 方向/强度/时限词表
# ---------------------------------------------------------------------------

_T = TargetDraft

# 关键词 → 影响对象映射（命中即关联；方向由全局方向词表统一判定，见文首纪律）
MAPPINGS: tuple[tuple[tuple[str, ...], tuple[TargetDraft, ...]], ...] = (
    # 白酒
    (("茅台",), (_T("stock", "贵州茅台", "600519"), _T("industry", "白酒"))),
    (("五粮液",), (_T("stock", "五粮液", "000858"), _T("industry", "白酒"))),
    (("白酒",), (_T("industry", "白酒"),)),
    # 新能源车产业链
    (("宁德时代", "宁德"), (_T("stock", "宁德时代", "300750"), _T("industry", "动力电池"))),
    (("动力电池", "锂电池"), (_T("industry", "动力电池"), _T("stock", "宁德时代", "300750"))),
    (("新能源",), (_T("industry", "新能源"), _T("stock", "宁德时代", "300750"))),
    (("光伏", "隆基"), (_T("industry", "光伏"), _T("stock", "隆基绿能", "601012"))),
    (("比亚迪",), (_T("stock", "比亚迪", "002594"), _T("industry", "新能源汽车"))),
    (("新能源汽车", "电动车"), (_T("industry", "新能源汽车"), _T("stock", "比亚迪", "002594"))),
    # 科技
    (("芯片", "半导体", "晶圆"), (_T("industry", "半导体"), _T("stock", "中芯国际", "688981"))),
    (("人工智能", "AI", "大模型"), (_T("industry", "人工智能"), _T("etf", "人工智能ETF", "515380"))),
    (("消费电子", "苹果", "iPhone"), (_T("industry", "消费电子"), _T("stock", "苹果", "AAPL"))),
    (("纳指", "纳斯达克"), (_T("etf", "纳指ETF", "513100"),)),
    (("特斯拉", "Tesla"), (_T("stock", "特斯拉", "TSLA"), _T("industry", "新能源汽车"))),
    (("腾讯",), (_T("stock", "腾讯控股", "00700"), _T("industry", "互联网"))),
    # 金融地产
    (("券商", "证券公司"), (_T("industry", "券商"), _T("etf", "券商ETF", "512000"))),
    (("银行",), (_T("industry", "银行"),)),
    (("保险",), (_T("industry", "保险"),)),
    (("降准", "降息", "LPR"), (_T("industry", "券商"), _T("etf", "沪深300ETF", "510300"))),
    (("美联储", "加息", "缩表"), (_T("etf", "纳指ETF", "513100"), _T("industry", "科技"), _T("etf", "沪深300ETF", "510300"))),
    (("房地产", "地产"), (_T("industry", "房地产"),)),
    # 消费医药
    (("医药", "创新药", "集采"), (_T("industry", "医药"), _T("stock", "恒瑞医药", "600276"))),
    (("消费", "食品饮料"), (_T("industry", "消费"),)),
    # 资源周期
    (("黄金", "金价"), (_T("etf", "黄金ETF", "518880"), _T("industry", "黄金"))),
    (("原油", "油价", "石油"), (_T("industry", "石油石化"),)),
    (("煤炭",), (_T("industry", "煤炭"),)),
    (("钢铁",), (_T("industry", "钢铁"),)),
    (("军工", "国防"), (_T("industry", "军工"),)),
)

# 方向词表（全局，作用于标题+摘要；正负同时命中视为信号冲突 → 中性）
POSITIVE_WORDS = (
    "利好", "提价", "涨价", "超预期", "预增", "增长", "大增", "新高", "回暖", "复苏",
    "中标", "获批", "补贴", "减税", "降准", "降息", "宽松", "刺激", "回购", "增持",
    "提振", "走强", "放量上涨", "突破", "扩产", "订单",
)
NEGATIVE_WORDS = (
    "利空", "降价", "不及预期", "预减", "下滑", "大跌", "新低", "亏损", "制裁", "罚款",
    "调查", "加息", "收紧", "缩表", "减持", "退市", "违规", "走弱", "暴跌", "跌停",
    "下跌", "警告", "下调", "停产",
)

# 强度词表：标题/摘要命中即升 strong（仅对非中性方向生效）
STRONG_WORDS = ("暴涨", "暴跌", "大涨", "大跌", "历史新高", "历史新低", "重磅", "重大", "紧急", "闪崩", "熔断", "涨停")

# 时限词表：基本面词优先于格局词，默认短期情绪
FUNDAMENTAL_WORDS = ("业绩", "营收", "净利润", "产能", "订单", "销量", "季报", "年报", "财报", "盈利")
STRUCTURE_WORDS = ("政策", "规划", "战略", "长期", "改革", "五年", "格局", "转型")


def _dedup_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


class RuleBasedAnalyzer:
    """纯规则影响分析器。

    - 对象：标题/摘要命中映射关键词 → 合并去重所有 targets，产出一条影响草稿
    - 方向：全局方向词表判定；正负信号冲突或缺失 → 中性（不确定即中性）
    - 强度：strong 词命中且非中性 → strong；非中性 → medium；中性 → weak
    - 时限：基本面词 → 中期基本面；格局词 → 长期格局；默认短期情绪
    - quote：命中依据所在原文（标题优先，标题无命中词时回退摘要前 160 字），保证可回溯
    """

    def analyze(self, news: News) -> list[NewsImpactDraft]:
        title = news.title or ""
        summary = news.summary or ""
        matched = self._match(title, summary)
        if not matched:
            return []  # 无映射：中性，不产生 impacts（由 analyze_pending 置 ready）
        return [self._build_draft(title, summary, matched)]

    # -- 内部 -------------------------------------------------------------

    @staticmethod
    def _match(title: str, summary: str) -> list[tuple[str, bool]]:
        """返回 [(命中关键词, 是否命中于标题)]，跨映射合并。"""
        matched: list[tuple[str, bool]] = []
        for keywords, _targets in MAPPINGS:
            for kw in keywords:
                if kw in title:
                    matched.append((kw, True))
                elif kw in summary:
                    matched.append((kw, False))
        return matched

    def _build_draft(
        self, title: str, summary: str, matched: list[tuple[str, bool]]
    ) -> NewsImpactDraft:
        # 对象合并去重（不同映射的 targets 取并集，如"新能源汽车"同时命中两条映射）
        targets: list[TargetDraft] = []
        seen: set[tuple[str, str, str]] = set()
        hit_kw_set = {kw for kw, _ in matched}
        hit_keywords: list[str] = []
        for keywords, mapping_targets in MAPPINGS:
            if not hit_kw_set.intersection(keywords):
                continue
            hit_keywords.extend(kw for kw in keywords if kw in hit_kw_set)
            for t in mapping_targets:
                key = (t.type, t.code, t.name)
                if key not in seen:
                    seen.add(key)
                    targets.append(t)

        # 方向：标题词优先统计，摘要词兜底；正负冲突 → 中性
        pos_title = [w for w in POSITIVE_WORDS if w in title]
        pos_summary = [w for w in POSITIVE_WORDS if w in summary and w not in pos_title]
        neg_title = [w for w in NEGATIVE_WORDS if w in title]
        neg_summary = [w for w in NEGATIVE_WORDS if w in summary and w not in neg_title]
        pos_hits = _dedup_keep_order(pos_title + pos_summary)
        neg_hits = _dedup_keep_order(neg_title + neg_summary)
        if pos_hits and not neg_hits:
            direction = DIRECTION_POSITIVE
        elif neg_hits and not pos_hits:
            direction = DIRECTION_NEGATIVE
        else:
            direction = DIRECTION_NEUTRAL

        # 强度与时限
        text = title + summary
        if direction == DIRECTION_NEUTRAL:
            strength = STRENGTH_WEAK
        else:
            strength = STRENGTH_STRONG if any(w in text for w in STRONG_WORDS) else STRENGTH_MEDIUM
        if any(w in text for w in FUNDAMENTAL_WORDS):
            horizon = HORIZON_MIDDLE
        elif any(w in text for w in STRUCTURE_WORDS):
            horizon = HORIZON_LONG
        else:
            horizon = HORIZON_SHORT

        # 依据摘录（纪律：direction != neutral 的判断必须可回溯到原文）：
        # - 有方向时，方向词出处优先（标题无方向词 → 摘要前 160 字）
        # - 中性时，对象词出处（标题无命中词 → 摘要；均无 → 标题兜底）
        if direction != DIRECTION_NEUTRAL:
            quote = title if (pos_title or neg_title) else summary[:160]
        else:
            quote = title if any(in_title for _, in_title in matched) else (summary or title)[:160]

        kw_text = "、".join(_dedup_keep_order(hit_keywords))[:60]
        names_text = "、".join(dict.fromkeys(t.name for t in targets))[:120]
        if direction == DIRECTION_POSITIVE:
            head = f"「{kw_text}」相关消息现利好信号（{('、'.join(pos_hits))[:40]}）"
        elif direction == DIRECTION_NEGATIVE:
            head = f"「{kw_text}」相关消息现利空信号（{('、'.join(neg_hits))[:40]}）"
        else:
            head = f"「{kw_text}」相关消息方向信号不明确"

        return NewsImpactDraft(
            quote=quote,
            fact=f"资讯提及「{kw_text}」，关联对象：{names_text}",
            direction=direction,
            strength=strength,
            horizon=horizon,
            logic=f"{head} → 市场调整对 {names_text} 的预期 → {horizon}层面影响",
            confidence=RULE_CONFIDENCE,
            targets=targets,
        )


class LLMAnalyzer:
    """LLM 影响分析接口骨架（二期：API key 接入后实现，本期内仅占位）。

    实现时的 prompt 要点（严格对齐设计文档第五节 LLM 纪律）：
    1. 依据强制摘录：要求模型对每个 direction != neutral 的判断摘录原文 quote，
       无原文依据不得给出方向（防幻觉）；
    2. 不确定即中性：confidence < 0.6 或影响链牵强时，输出 direction=neutral
       并降低 strength；
    3. 输出 JSON Schema：严格按三层标签结构返回——
       impacts: [{quote, fact, direction(positive/negative/neutral),
       strength(strong/medium/weak), horizon(短期情绪/中期基本面/长期格局),
       logic, confidence(0-1), targets: [{type(industry/stock/etf/fund),
       name, code}]}]；解析失败或字段缺失时该条 News 置 failed
       （由 analyze_pending 统一处理，不在分析器内吞错）；
    4. 免责标注随行由展示层固定携带"AI 生成 · 仅供参考"，分析层不处理。
    """

    def __init__(self, api_key: str | None = None, model: str = "") -> None:
        self.api_key = api_key
        self.model = model

    def analyze(self, news: News) -> list[NewsImpactDraft]:
        raise NotImplementedError(
            "LLM 分析器二期接入（API key 配置后实现），prompt 要点见类 docstring"
        )


# ---------------------------------------------------------------------------
# 队列消费：pending → 分析 → 入库（含自选命中解析）
# ---------------------------------------------------------------------------


def _analyze(db: Session, limit: int, analyzer: Analyzer) -> int:
    a = analyzer or RuleBasedAnalyzer()
    # 自选命中比对基准：code 精确匹配优先，name 兜底（跨市场 code 冲突初版可接受）
    watch_codes = set(db.scalars(select(Watchlist.code)).all())
    watch_names = set(db.scalars(select(Watchlist.name)).all())

    rows = (
        db.scalars(
            select(News)
            .where(News.analysis_status == STATUS_PENDING)
            .order_by(News.id)
            .limit(limit)
        )
        .all()
    )

    analyzed = 0
    for news in rows:
        try:
            drafts = a.analyze(news)
        except NotImplementedError:
            raise  # 骨架被误用应显式失败，而非静默标记 failed
        except Exception:
            logger.exception("新闻 id=%s 分析异常，标记 failed", news.id)
            news.analysis_status = STATUS_FAILED
            continue

        for d in drafts:
            targets = [
                NewsImpactTarget(
                    type=t.type,
                    name=t.name,
                    code=t.code or "",
                    hit_watchlist=(t.code in watch_codes) if t.code else (t.name in watch_names),
                    hit_portfolio=False,  # 模拟盘持仓命中二期接线
                )
                for t in d.targets
            ]
            db.add(
                NewsImpact(
                    news_id=news.id,
                    quote=d.quote,
                    fact=d.fact,
                    direction=d.direction,
                    strength=d.strength,
                    horizon=d.horizon,
                    logic=d.logic,
                    confidence=d.confidence,
                    targets=targets,  # relationship 级联插入，自动回填 impact_id
                )
            )
        news.analysis_status = STATUS_READY  # 无映射的也置 ready（中性，不阻塞队列）
        analyzed += 1

    db.commit()
    return analyzed


def analyze_pending(
    db: Session | None = None, limit: int = 20, analyzer: Analyzer | None = None
) -> int:
    """把 pending 状态新闻逐条跑影响分析并入库，返回本轮完成分析（置 ready）的条数。

    - 单条分析异常置 failed 并继续后续条目
    - db 缺省时自开短生命周期 Session（供调度器/脚本调用）
    """
    ensure_tables()
    if db is not None:
        return _analyze(db, limit, analyzer)
    with SessionLocal() as session:
        return _analyze(session, limit, analyzer)
