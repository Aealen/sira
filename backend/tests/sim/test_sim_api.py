# -*- coding: utf-8 -*-
"""模拟盘 API 冒烟测试（app/routers/sim.py）：完整流
下单 → 持仓 → 挂单 → 撤单 → 流水 → 重新开局，以及 422 拒单与规则表端点。"""
import pytest

from app.services.datasource.base import Quote

INIT_CASH = 1_000_000.0


def _a_quotes() -> dict[str, Quote]:
    return {
        "600519": Quote(market="a_stock", code="600519", name="贵州茅台", price=33.76, prev_close=33.50),
        "000001": Quote(market="a_stock", code="000001", name="平安银行", price=10.00, prev_close=10.00),
    }


def test_full_flow(sim_client, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes=_a_quotes())

    # 1. 空账户
    r = sim_client.get("/api/sim/account")
    assert r.status_code == 200
    body = r.json()
    assert body["cash"] == INIT_CASH
    assert body["positions"] == []
    assert body["total_asset"] == INIT_CASH
    assert body["total_pnl"] == 0.0

    # 2. 市价买入：201 成交回执（含费用明细）
    r = sim_client.post("/api/sim/order", json={
        "market": "a_stock", "code": "600519", "side": "buy", "order_type": "market", "quantity": 1000,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "filled"
    assert body["trade"]["amount"] == pytest.approx(33760.0)
    assert body["trade"]["commission"] == pytest.approx(8.44)     # 万2.5
    assert body["trade"]["total_fee"] == pytest.approx(8.44)
    assert body["order"]["name"] == "贵州茅台"

    # 3. 账户总览：持仓 + 总资产（浮亏 = 费用损耗）
    r = sim_client.get("/api/sim/account")
    body = r.json()
    assert body["cash"] == pytest.approx(966231.56)
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["market"] == "a_stock" and pos["code"] == "600519"
    assert pos["quantity"] == 1000 and pos["available_qty"] == 0  # T+1
    assert pos["market_value"] == pytest.approx(33760.0)
    assert pos["unrealized_pnl"] == pytest.approx(-8.44)
    assert body["total_asset"] == pytest.approx(999991.56)
    assert body["total_pnl"] == pytest.approx(-8.44)
    assert body["recent_trades"][0]["commission"] == pytest.approx(8.44)

    # 4. 限价挂单 → 撤单
    r = sim_client.post("/api/sim/order", json={
        "market": "a_stock", "code": "000001", "side": "buy",
        "order_type": "limit", "price": 9.90, "quantity": 100,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "open" and body["trade"] is None
    order_id = body["order"]["id"]

    r = sim_client.get("/api/sim/account")
    assert len(r.json()["open_orders"]) == 1

    r = sim_client.delete(f"/api/sim/order/{order_id}")
    assert r.status_code == 204
    r = sim_client.get("/api/sim/account")
    assert r.json()["open_orders"] == []

    # 撤已撤的单 → 409
    r = sim_client.delete(f"/api/sim/order/{order_id}")
    assert r.status_code == 409
    # 撤不存在的单 → 404
    r = sim_client.delete("/api/sim/order/9999")
    assert r.status_code == 404

    # 5. 成交流水
    r = sim_client.get("/api/sim/trades")
    assert r.status_code == 200
    trades = r.json()
    assert len(trades) == 1 and trades[0]["code"] == "600519"

    # 6. 重新开局：confirm 非 true 拒绝
    r = sim_client.post("/api/sim/reset", json={"confirm": False})
    assert r.status_code == 422 and "不可恢复" in r.json()["detail"]
    r = sim_client.post("/api/sim/reset", json={"confirm": True})
    assert r.status_code == 200 and r.json()["cash"] == INIT_CASH

    r = sim_client.get("/api/sim/account")
    body = r.json()
    assert body["positions"] == [] and body["open_orders"] == []
    assert body["total_asset"] == INIT_CASH and body["total_pnl"] == 0.0


def test_limit_order_lazily_filled_via_account(sim_client, fake_market, trading_now) -> None:
    """挂单在 GET account 时惰性撮合（无后台调度）。"""
    src = fake_market("a_stock", quotes=_a_quotes())
    r = sim_client.post("/api/sim/order", json={
        "market": "a_stock", "code": "000001", "side": "buy",
        "order_type": "limit", "price": 9.90, "quantity": 100,
    })
    assert r.json()["status"] == "open"

    r = sim_client.get("/api/sim/account")     # 现价 10.00 未触及 9.90，仍挂单
    assert len(r.json()["open_orders"]) == 1
    assert r.json()["open_orders"][0]["code"] == "000001"

    src._quotes["000001"].price = 9.88          # 触及限价
    r = sim_client.get("/api/sim/account")
    body = r.json()
    assert body["open_orders"] == []
    assert len(body["positions"]) == 1 and body["positions"][0]["code"] == "000001"


def test_rejected_order_returns_422_with_chinese_reason(sim_client, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes=_a_quotes())
    r = sim_client.post("/api/sim/order", json={
        "market": "a_stock", "code": "000001", "side": "buy",
        "order_type": "limit", "price": 11.10, "quantity": 100,  # 昨收 10.00，超 +10%（涨停价 11.00 内合法）
    })
    assert r.status_code == 422
    assert "涨跌幅" in r.json()["detail"]

    # 零股买入
    r = sim_client.post("/api/sim/order", json={
        "market": "a_stock", "code": "600519", "side": "buy", "order_type": "market", "quantity": 150,
    })
    assert r.status_code == 422 and "整数倍" in r.json()["detail"]

def test_rules_endpoint(sim_client) -> None:
    r = sim_client.get("/api/sim/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["initial_cash"] == INIT_CASH
    assert body["fees"]["commission_rate"] == pytest.approx(0.00025)
    assert body["fees"]["commission_min"] == pytest.approx(5.0)
    assert set(body["markets"]) == {"a_stock", "etf", "fund", "hk", "us"}
    assert body["markets"]["a_stock"]["t_plus"] == 1
    assert body["markets"]["fund"]["support_limit"] is False
    assert body["markets"]["us"]["trade_sessions"] == ["21:30-04:00"]
