# -*- coding: utf-8 -*-
"""M4 资讯服务包：collector（采集，两段式第一段）+ analyzer（影响分析，第二段）。"""
from app.services.news.analyzer import (
    Analyzer,
    LLMAnalyzer,
    NewsImpactDraft,
    RuleBasedAnalyzer,
    TargetDraft,
    analyze_pending,
)
from app.services.news.collector import collect_once, init_scheduler

__all__ = [
    "Analyzer",
    "LLMAnalyzer",
    "NewsImpactDraft",
    "RuleBasedAnalyzer",
    "TargetDraft",
    "analyze_pending",
    "collect_once",
    "init_scheduler",
]
