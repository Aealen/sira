# -*- coding: utf-8 -*-
"""规则分析器单测：方向/强度/对象映射/依据摘录/中性处理 + analyze_pending 入库与自选命中。"""
from sqlalchemy import select

from app.models import Watchlist
from app.models_news import News
from app.services.news.analyzer import RuleBasedAnalyzer, analyze_pending


def _news(title: str, summary: str = "") -> News:
    """构造 News 内存对象（title_hash 以标题区分，可直接入库）。"""
    return News(
        source="财联社电报",
        category="宏观",
        title=title,
        summary=summary,
        title_hash=f"test-{title}",
    )


class TestRuleBasedAnalyzer:
    def test_maotai_price_up_positive(self):
        drafts = RuleBasedAnalyzer().analyze(_news("贵州茅台：核心产品出厂价上调，业绩超预期"))
        assert len(drafts) == 1
        d = drafts[0]
        assert d.direction == "positive"
        assert d.strength == "medium"  # 非中性且无强词
        assert d.confidence == 0.5
        assert d.quote == "贵州茅台：核心产品出厂价上调，业绩超预期"  # 依据=标题原文
        pairs = {(t.type, t.name) for t in d.targets}
        assert ("stock", "贵州茅台") in pairs
        assert ("industry", "白酒") in pairs
        assert "600519" in {t.code for t in d.targets}

    def test_fed_rate_hike_negative(self):
        d = RuleBasedAnalyzer().analyze(_news("美联储宣布加息25个基点"))[0]
        assert d.direction == "negative"
        codes = {t.code for t in d.targets}
        assert "513100" in codes  # 纳指ETF
        assert "科技" in {t.name for t in d.targets}

    def test_rrr_cut_positive_broker(self):
        d = RuleBasedAnalyzer().analyze(_news("央行宣布降准0.5个百分点"))[0]
        assert d.direction == "positive"
        assert "券商" in {t.name for t in d.targets}

    def test_strong_word_upgrades_strength(self):
        d = RuleBasedAnalyzer().analyze(_news("芯片板块重磅利好：国产替代加速"))[0]
        assert d.direction == "positive"
        assert d.strength == "strong"  # 命中强词"重磅"

    def test_neutral_when_no_direction_words(self):
        d = RuleBasedAnalyzer().analyze(_news("白酒行业年度峰会即将召开"))[0]
        assert d.direction == "neutral"
        assert d.strength == "weak"  # 不确定即中性：强度降为 weak
        assert d.horizon == "短期情绪"

    def test_direction_conflict_is_neutral(self):
        d = RuleBasedAnalyzer().analyze(_news("白酒板块利好与利空因素交织"))[0]
        assert d.direction == "neutral"  # 正负信号冲突 → 中性

    def test_direction_from_summary_quote_fallback(self):
        n = _news("白酒行业最新动态", summary="龙头企业提价，利好板块整体")
        d = RuleBasedAnalyzer().analyze(n)[0]
        assert d.direction == "positive"
        assert d.quote.startswith("龙头企业提价")  # 命中词不在标题 → 依据回退到摘要原文

    def test_no_mapping_returns_empty(self):
        assert RuleBasedAnalyzer().analyze(_news("某地举办城市马拉松赛事")) == []

    def test_multi_mapping_targets_merged_dedup(self):
        d = RuleBasedAnalyzer().analyze(_news("新能源汽车销量大增，动力电池排产上修"))[0]
        names = {t.name for t in d.targets}
        assert {"新能源汽车", "比亚迪", "动力电池", "宁德时代"} <= names
        keys = [(t.type, t.code, t.name) for t in d.targets]
        assert len(keys) == len(set(keys))  # 对象去重，无重复行

    def test_horizon_fundamental(self):
        d = RuleBasedAnalyzer().analyze(_news("白酒龙头年报业绩预增"))[0]
        assert d.horizon == "中期基本面"  # 命中基本面词"业绩/年报"

    def test_llm_analyzer_not_implemented(self):
        from app.services.news.analyzer import LLMAnalyzer

        try:
            LLMAnalyzer().analyze(_news("任意标题"))
        except NotImplementedError:
            pass
        else:
            raise AssertionError("LLMAnalyzer 骨架应抛 NotImplementedError")


class TestAnalyzePending:
    def test_hit_watchlist_resolution(self, news_db):
        news_db.add(Watchlist(market="a_stock", code="600519", name="贵州茅台"))
        news_db.add(_news("贵州茅台：产品提价"))
        news_db.commit()

        assert analyze_pending(db=news_db) == 1
        news = news_db.scalars(select(News)).first()
        assert news.analysis_status == "ready"
        assert len(news.impacts) == 1
        imp = news.impacts[0]
        assert imp.direction == "positive"
        hit = [t for t in imp.targets if t.code == "600519"]
        assert hit and hit[0].hit_watchlist is True
        assert all(t.hit_watchlist is False for t in imp.targets if t.code != "600519")

    def test_hit_watchlist_by_name_fallback(self, news_db):
        # 行业类对象无 code，按 name 与自选比对（自选无行业名 → 不命中）
        news_db.add(Watchlist(market="a_stock", code="600519", name="贵州茅台"))
        news_db.add(_news("军工板块召开年度工作会议"))
        news_db.commit()
        analyze_pending(db=news_db)
        imp = news_db.scalars(select(News)).first().impacts[0]
        assert imp.direction == "neutral"
        assert all(t.hit_watchlist is False for t in imp.targets)

    def test_no_mapping_ready_without_impacts(self, news_db):
        news_db.add(_news("某地举办城市马拉松赛事"))
        news_db.commit()
        assert analyze_pending(db=news_db) == 1
        news = news_db.scalars(select(News)).first()
        assert news.analysis_status == "ready"  # 无映射也置 ready（中性），不阻塞队列
        assert news.impacts == []

    def test_limit_batching(self, news_db):
        for i in range(5):
            news_db.add(_news(f"白酒行业动态汇总第{i}期"))
        news_db.commit()
        assert analyze_pending(db=news_db, limit=3) == 3
        assert analyze_pending(db=news_db, limit=3) == 2  # 剩余 2 条
        assert analyze_pending(db=news_db, limit=3) == 0  # 队列清空
