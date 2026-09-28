# -*- coding: utf-8 -*-
"""采集器单测：title_hash 去重（同标题二次采集 added=0）、失败容错、初分类规则。"""
from sqlalchemy import select

from app.models_news import News
from app.services.news import collector
from app.services.news.collector import classify_category, collect_once


def _fake_records():
    return [
        {"title": "央行公布最新一期LPR报价", "summary": "1年期与5年期均维持不变", "published_at": None},
        {"title": "贵州茅台发布年度业绩公告", "summary": "", "published_at": None},
    ]


class TestCollectDedup:
    def test_second_collect_adds_zero(self, news_db, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_cls_news", _fake_records)
        assert collect_once(db=news_db) == 2
        assert collect_once(db=news_db) == 0  # 同标题二次采集：title_hash 去重
        assert len(news_db.scalars(select(News)).all()) == 2

    def test_whitespace_only_diff_is_dup(self, news_db, monkeypatch):
        monkeypatch.setattr(
            collector,
            "_fetch_cls_news",
            lambda: [{"title": "  白酒板块走强  ", "summary": "", "published_at": None}],
        )
        assert collect_once(db=news_db) == 1
        monkeypatch.setattr(
            collector,
            "_fetch_cls_news",
            lambda: [{"title": "白酒板块走强", "summary": "", "published_at": None}],
        )
        assert collect_once(db=news_db) == 0  # strip 后同标题视为重复

    def test_category_persisted(self, news_db, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_cls_news", _fake_records)
        collect_once(db=news_db)
        cats = {n.title: n.category for n in news_db.scalars(select(News)).all()}
        assert cats["央行公布最新一期LPR报价"] == "宏观"
        assert cats["贵州茅台发布年度业绩公告"] == "公告"


class TestFailureTolerance:
    def test_network_error_returns_zero_without_raising(self, news_db, monkeypatch):
        def boom():
            raise RuntimeError("network unreachable")

        monkeypatch.setattr(collector, "_fetch_cls_news", boom)
        assert collect_once(db=news_db) == 0  # 采集失败返回 0 不抛


class TestClassifyCategory:
    def test_announcement(self):
        assert classify_category("某公司发布重大资产重组公告") == "公告"

    def test_report(self):
        assert classify_category("机构发布白酒行业最新研报") == "研报"

    def test_macro(self):
        assert classify_category("央行开展2000亿元逆回购操作") == "宏观"

    def test_industry(self):
        assert classify_category("白酒板块午后放量走强") == "行业"

    def test_default_macro(self):
        assert classify_category("某地举办美食文化节") == "宏观"  # 默认宏观
