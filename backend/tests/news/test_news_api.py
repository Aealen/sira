# -*- coding: utf-8 -*-
"""TestClient 冒烟：collect → analyze → 列表带影响标签 → 详情 → watchlist-digest 完整链路。

网络不可达时以构造的假新闻数据（monkeypatch 拉取函数）入库测完整链路；
真实采集允许 0 条（失败容错路径由 test_collector 覆盖）。
"""
from app.models import Watchlist
from app.services.news import collector

FAKE_RECORDS = [
    {"title": "贵州茅台：部分产品出厂价上调，利好业绩", "summary": "白酒板块受提振", "published_at": None},
    {"title": "美联储宣布加息25个基点", "summary": "全球流动性收紧", "published_at": None},
    {"title": "某地举办城市马拉松赛事", "summary": "", "published_at": None},
]


def test_full_pipeline(news_client, news_db, monkeypatch):
    monkeypatch.setattr(collector, "_fetch_cls_news", lambda: FAKE_RECORDS)
    news_db.add(Watchlist(market="a_stock", code="600519", name="贵州茅台"))
    news_db.commit()

    # 1. 采集：入库 3 条；二次采集去重 added=0
    r = news_client.post("/api/news/collect")
    assert r.status_code == 200
    assert r.json() == {"added": 3}
    assert news_client.post("/api/news/collect").json() == {"added": 0}

    # 2. 分析：3 条 pending 全部 ready
    r = news_client.post("/api/news/analyze")
    assert r.status_code == 200
    assert r.json() == {"analyzed": 3}

    # 3. 列表：带影响标签简版
    body = news_client.get("/api/news", params={"limit": 10}).json()
    assert body["total"] == 3
    assert len(body["items"]) == 3

    maotai = next(it for it in body["items"] if "茅台" in it["title"])
    assert maotai["analysis_status"] == "ready"
    assert maotai["impacts"], "列表应内嵌影响标签"
    imp = maotai["impacts"][0]
    assert imp["direction"] == "positive"
    assert imp["strength"] in ("strong", "medium", "weak")
    assert imp["horizon"] and imp["logic"]
    hit_targets = [t for t in imp["targets"] if t["hit_watchlist"]]
    assert any(t["code"] == "600519" and t["name"] == "贵州茅台" for t in hit_targets)

    fed = next(it for it in body["items"] if "美联储" in it["title"])
    assert fed["impacts"][0]["direction"] == "negative"  # 加息 → 利空

    marathon = next(it for it in body["items"] if "马拉松" in it["title"])
    assert marathon["impacts"] == []  # 无映射：中性，无标签

    # 4. 详情：impacts 全文（quote/fact/confidence）
    detail = news_client.get(f"/api/news/{maotai['id']}").json()
    d_imp = detail["impacts"][0]
    assert d_imp["quote"].startswith("贵州茅台")  # 原文摘录可回溯
    assert d_imp["fact"]
    assert d_imp["confidence"] == 0.5

    # 5. 今日自选命中聚合
    digest = news_client.get("/api/news/watchlist-digest").json()
    row = next(d for d in digest if d["code"] == "600519")
    assert row["name"] == "贵州茅台"
    assert row["direction"] == "positive"
    assert row["count"] >= 1
    assert "茅台" in row["latest_title"]

    # 6. 过滤与搜索
    assert news_client.get("/api/news", params={"category": "宏观"}).json()["total"] == 3
    assert news_client.get("/api/news", params={"q": "美联储"}).json()["total"] == 1


def test_detail_404(news_client):
    assert news_client.get("/api/news/99999").status_code == 404
