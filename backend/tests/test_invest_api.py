# -*- coding: utf-8 -*-
"""API 冒烟测试（TestClient）：计划 CRUD、execute-now、review。

撮合与行情通过 monkeypatch executor 模块函数隔离（_place_buy 返回
matching.PlaceResult 同构对象），不触达真实撮合。
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.services.invest.executor as executor
from app.models_invest import InvestExecution
from tests.conftest import make_exec, make_plan


@pytest.fixture
def fake_market(monkeypatch):
    calls: list[dict] = []

    def fake_price(market, code):
        return 10.0

    def fake_buy(db, market, code, price, amount):
        calls.append({"price": price, "amount": amount})
        qty = round(amount / price, 4)
        return SimpleNamespace(trade=SimpleNamespace(
            quantity=qty, total_fee=0.25, amount=round(amount - 0.25, 2)))

    monkeypatch.setattr(executor, "get_current_price", fake_price)
    monkeypatch.setattr(executor, "_place_buy", fake_buy)
    return calls


PLAN_BODY = {
    "market": "etf",
    "code": "510300",
    "name": "沪深300ETF",
    "frequency": "monthly",
    "day_of_month": 10,
    "amount": 1000.0,
    "smart_dca": False,
    "take_profit_mode": "none",
    "take_profit_value": None,
}


class TestPlanCrud:
    def test_create_plan_201(self, client, db_session, fake_market):
        resp = client.post("/api/invest/plans", json=PLAN_BODY)
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] > 0
        assert body["status"] == "active"
        assert body["stats"] == {"invested": 0.0, "executions": 0, "market_value": 0.0, "pnl_pct": 0.0}

    def test_create_plan_validation_errors(self, client, fake_market):
        assert client.post("/api/invest/plans", json={**PLAN_BODY, "day_of_month": 30}).status_code == 422
        assert client.post("/api/invest/plans", json={**PLAN_BODY, "amount": -1}).status_code == 422
        assert client.post("/api/invest/plans", json={**PLAN_BODY, "frequency": "daily"}).status_code == 422
        # 止盈模式非 none 时必须给 value
        bad = {**PLAN_BODY, "take_profit_mode": "target_return", "take_profit_value": None}
        assert client.post("/api/invest/plans", json=bad).status_code == 422
        # 估值分位上限 100
        bad2 = {**PLAN_BODY, "take_profit_mode": "valuation", "take_profit_value": 120}
        assert client.post("/api/invest/plans", json=bad2).status_code == 422

    def test_list_plans_with_stats(self, client, db_session, fake_market):
        plan = make_plan(db_session, amount=1000.0, day_of_month=1,
                         created_at=datetime.now() - timedelta(days=40))
        make_exec(db_session, plan, exec_date=(date.today() - timedelta(days=30)).isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)

        resp = client.get("/api/invest/plans")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        stats = items[0]["stats"]
        assert stats["invested"] == pytest.approx(1000.0)
        assert stats["executions"] == 1
        assert stats["market_value"] == pytest.approx(1000.0)  # fake price 10.0 × 100 股

    def test_get_plan_detail_with_executions(self, client, db_session, fake_market):
        plan = make_plan(db_session)
        make_exec(db_session, plan, exec_date="2026-08-01", price=9.0, quantity=110.0, amount=1000.0, fee=1.0)
        make_exec(db_session, plan, exec_date="2026-09-01", price=10.0, quantity=100.0, amount=1000.0, fee=1.0)

        resp = client.get(f"/api/invest/plans/{plan.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == plan.id
        assert len(body["executions"]) == 2
        assert body["executions"][0]["exec_date"] == "2026-09-01"  # 倒序（最新在前）
        assert body["stats"]["executions"] == 2

    def test_get_missing_plan_404(self, client, fake_market):
        assert client.get("/api/invest/plans/9999").status_code == 404

    def test_patch_plan_edit_and_pause_resume(self, client, db_session, fake_market):
        plan = make_plan(db_session, amount=1000.0)

        # 编辑参数
        resp = client.patch(f"/api/invest/plans/{plan.id}", json={"amount": 1500.0, "day_of_month": 5})
        assert resp.status_code == 200
        assert resp.json()["amount"] == pytest.approx(1500.0)
        assert resp.json()["day_of_month"] == 5

        # 暂停
        resp = client.patch(f"/api/invest/plans/{plan.id}", json={"status": "paused"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

        # 恢复
        resp = client.patch(f"/api/invest/plans/{plan.id}", json={"status": "active"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        # 非法状态与空 body
        assert client.patch(f"/api/invest/plans/{plan.id}", json={"status": "stopped"}).status_code == 422
        assert client.patch(f"/api/invest/plans/{plan.id}", json={}).status_code == 400

    def test_patch_missing_plan_404(self, client, fake_market):
        assert client.patch("/api/invest/plans/9999", json={"amount": 100}).status_code == 404

    def test_delete_plan_cascades_executions(self, client, db_session, fake_market):
        plan = make_plan(db_session)
        make_exec(db_session, plan, exec_date="2026-09-01", price=10.0, quantity=100.0, amount=1000.0)

        assert client.delete(f"/api/invest/plans/{plan.id}").status_code == 204
        assert client.get(f"/api/invest/plans/{plan.id}").status_code == 404
        assert db_session.scalars(select(InvestExecution)).all() == []

        assert client.delete(f"/api/invest/plans/{plan.id}").status_code == 404


class TestExecuteNow:
    def test_execute_now_success(self, client, db_session, fake_market):
        plan = make_plan(db_session, amount=1000.0)

        resp = client.post(f"/api/invest/plans/{plan.id}/execute-now")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["quantity"] == pytest.approx(100.0)
        assert fake_market[0]["amount"] == pytest.approx(1000.0)

        row = db_session.scalars(select(InvestExecution)).one()
        assert row.plan_id == plan.id
        db_session.refresh(plan)
        assert plan.last_exec_date == date.today().isoformat()

        # 详情里能看到这条流水
        detail = client.get(f"/api/invest/plans/{plan.id}").json()
        assert len(detail["executions"]) == 1

    def test_execute_now_failure_returns_ok_false(self, client, db_session, monkeypatch):
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: 10.0)

        def no_cash(db, market, code, price, amount):
            raise RuntimeError("模拟盘资金不足")

        monkeypatch.setattr(executor, "_place_buy", no_cash)
        plan = make_plan(db_session)

        resp = client.post(f"/api/invest/plans/{plan.id}/execute-now")
        assert resp.status_code == 200  # 失败不抛 5xx
        body = resp.json()
        assert body["ok"] is False
        assert "资金不足" in body["message"]
        assert db_session.scalars(select(InvestExecution)).all() == []

    def test_execute_now_missing_plan_404(self, client, fake_market):
        assert client.post("/api/invest/plans/9999/execute-now").status_code == 404


class TestReview:
    def test_review_endpoint(self, client, db_session, fake_market):
        plan = make_plan(db_session, take_profit_mode="target_return", take_profit_value=5.0,
                         created_at=datetime.now() - timedelta(days=40))
        make_exec(db_session, plan, exec_date=(date.today() - timedelta(days=30)).isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)

        resp = client.get("/api/invest/review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_invested"] == pytest.approx(1000.0)
        assert body["total_market_value"] == pytest.approx(1000.0)  # 100 股 × fake 价 10
        assert set(body) == {"total_invested", "total_market_value", "total_pnl_pct", "discipline"}
        assert len(body["discipline"]) == 3
        assert all("check" in c and "passed" in c and "detail" in c for c in body["discipline"])

    def test_review_empty(self, client, db_session, fake_market):
        resp = client.get("/api/invest/review")
        assert resp.status_code == 200
        assert resp.json()["total_invested"] == 0.0
