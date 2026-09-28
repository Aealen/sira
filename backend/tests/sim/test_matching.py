# -*- coding: utf-8 -*-
"""撮合引擎单测（app/services/matching.py）：假行情 + 固定时钟验证
市价成交 / 费用计算 / T+1 / 涨跌幅 / 零股 / 限价触及 / 撤单 / 重新开局。

费用示例对齐设计文档口径：佣金万 2.5 最低 5 元；
买入 33760 元佣金 8.44，卖出另加印花税 16.88（万 5）。
"""
from datetime import datetime

import pytest
from sqlalchemy import select

import app.services.matching as matching
from app.models_sim import SimOrder, SimPosition
from app.services.datasource.base import Quote
from app.services.matching import (
    OrderRejected,
    calc_fees,
    cancel_order,
    check_open_orders,
    ensure_new_day,
    get_account,
    get_position,
    place_order,
    reset_account,
)

INIT_CASH = 1_000_000.0
WED_10 = datetime(2026, 9, 30, 10, 0)


def a_quote(code: str = "600519", price: float = 33.76, prev_close: float = 33.50, name: str = "贵州茅台") -> Quote:
    return Quote(market="a_stock", code=code, name=name, price=price, prev_close=prev_close)


# -- 费用计算（纯函数） ------------------------------------------------------


def test_calc_fees_buy_33760_commission_8_44() -> None:
    """设计文档示例：买入 33760 元 → 佣金 8.44（万2.5，高于最低 5）。"""
    fees = calc_fees("a_stock", "buy", 33760.0)
    assert fees.commission == pytest.approx(8.44)
    assert fees.stamp_tax == 0.0
    assert fees.total == pytest.approx(8.44)


def test_calc_fees_sell_adds_stamp_tax_16_88() -> None:
    """卖出 33760 元 → 佣金 8.44 + 印花税 16.88（万5）。"""
    fees = calc_fees("a_stock", "sell", 33760.0)
    assert fees.commission == pytest.approx(8.44)
    assert fees.stamp_tax == pytest.approx(16.88)
    assert fees.total == pytest.approx(25.32)


def test_calc_fees_min_commission_5() -> None:
    """小额成交按最低佣金 5 元。"""
    assert calc_fees("a_stock", "buy", 2000.0).commission == pytest.approx(5.0)
    assert calc_fees("us", "buy", 100.0).commission == pytest.approx(5.0)


def test_calc_fees_etf_no_stamp_fund_subscription_redemption() -> None:
    assert calc_fees("etf", "sell", 10000.0).stamp_tax == 0.0
    assert calc_fees("fund", "buy", 15000.0).subscription_fee == pytest.approx(15.0)   # 0.1%
    assert calc_fees("fund", "buy", 15000.0).commission == 0.0
    assert calc_fees("fund", "sell", 15000.0).redemption_fee == pytest.approx(75.0)    # 0.5%
    assert calc_fees("hk", "sell", 30000.0).stamp_tax == pytest.approx(15.0)


# -- 市价买入成交 ------------------------------------------------------------


def test_market_buy_fills_with_fees(sim_db, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes={"600519": a_quote()})
    result = place_order(sim_db, "a_stock", "600519", "buy", "market", 1000)

    assert result.order.status == "filled"
    assert result.trade is not None
    assert result.trade.amount == pytest.approx(33760.0)
    assert result.trade.commission == pytest.approx(8.44)          # 万2.5
    assert result.trade.stamp_tax == 0.0                            # 买入无印花税
    assert result.trade.total_fee == pytest.approx(8.44)

    acc = get_account(sim_db)
    assert acc.cash == pytest.approx(966231.56)                     # 1000000 - 33760 - 8.44

    pos = get_position(sim_db, "a_stock", "600519")
    assert pos is not None
    assert pos.quantity == 1000
    assert pos.available_qty == 0                                   # T+1：当日买入不可卖
    assert pos.avg_cost == pytest.approx(33.7684, abs=1e-4)         # 含费加权成本


def test_market_buy_min_commission(sim_db, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes={"600519": a_quote(price=20.0, prev_close=20.0, name="测试")})
    result = place_order(sim_db, "a_stock", "600519", "buy", "market", 100)
    assert result.trade is not None
    assert result.trade.amount == pytest.approx(2000.0)
    assert result.trade.commission == pytest.approx(5.0)            # 最低佣金


def test_market_buy_insufficient_cash_rejected(sim_db, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes={"600519": a_quote()})
    with pytest.raises(OrderRejected, match="资金不足"):
        place_order(sim_db, "a_stock", "600519", "buy", "market", 100_000)  # 337.6 万 >> 100 万

    rejected = sim_db.scalars(select(SimOrder).where(SimOrder.status == "rejected")).all()
    assert len(rejected) == 1 and "资金不足" in rejected[0].reject_reason


# -- T+1 规则 ---------------------------------------------------------------


def test_t1_sell_same_day_rejected(sim_db, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes={"600519": a_quote()})
    place_order(sim_db, "a_stock", "600519", "buy", "market", 1000)

    with pytest.raises(OrderRejected, match="可卖数量不足"):
        place_order(sim_db, "a_stock", "600519", "sell", "market", 1000)

    rejected = sim_db.scalars(select(SimOrder).where(SimOrder.status == "rejected")).all()
    assert len(rejected) == 1
    assert "T+1" in rejected[0].reject_reason


def test_t1_unlock_and_sell_next_day(sim_db, fake_market, trading_now, monkeypatch) -> None:
    src = fake_market("a_stock", quotes={"600519": a_quote()})
    place_order(sim_db, "a_stock", "600519", "buy", "market", 1000)

    # 次日：跨日滚动解锁可卖量
    monkeypatch.setattr(matching, "_today", lambda: "2026-10-01")
    ensure_new_day(sim_db)
    pos = get_position(sim_db, "a_stock", "600519")
    assert pos is not None and pos.available_qty == 1000

    result = place_order(sim_db, "a_stock", "600519", "sell", "market", 1000)
    assert result.trade is not None
    assert result.trade.commission == pytest.approx(8.44)
    assert result.trade.stamp_tax == pytest.approx(16.88)           # 卖出印花税万5
    assert get_account(sim_db).cash == pytest.approx(999966.24)     # 966231.56 + 33760 - 25.32
    assert get_position(sim_db, "a_stock", "600519") is None        # 清仓删行


def test_hk_t0_buy_sell_same_day(sim_db, fake_market, trading_now) -> None:
    """港股 T+0 回转：当日买入当日可卖，卖出收印花税。"""
    q = Quote(market="hk", code="00700", name="腾讯控股", price=300.0, prev_close=300.0)
    fake_market("hk", quotes={"00700": q})
    place_order(sim_db, "hk", "00700", "buy", "market", 100)
    assert get_account(sim_db).cash == pytest.approx(969992.5)      # 100 万 - 30000 - 7.5

    result = place_order(sim_db, "hk", "00700", "sell", "market", 100)
    assert result.trade is not None
    assert result.trade.commission == pytest.approx(7.5)
    assert result.trade.stamp_tax == pytest.approx(15.0)
    assert get_account(sim_db).cash == pytest.approx(999970.0)


def test_odd_lot_sell_allowed(sim_db, fake_market, trading_now) -> None:
    """A 股零股仅可卖：非整百持仓卖出放行。"""
    fake_market("a_stock", quotes={"600519": a_quote()})
    sim_db.add(SimPosition(market="a_stock", code="600519", quantity=50, available_qty=50, avg_cost=10.0))
    sim_db.commit()

    result = place_order(sim_db, "a_stock", "600519", "sell", "market", 50)
    assert result.order.status == "filled"
    assert get_position(sim_db, "a_stock", "600519") is None


# -- 拒单规则 ---------------------------------------------------------------


def test_odd_lot_buy_rejected(sim_db, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes={"600519": a_quote()})
    with pytest.raises(OrderRejected, match="整数倍"):
        place_order(sim_db, "a_stock", "600519", "buy", "market", 150)


def test_price_limit_rejected(sim_db, fake_market, trading_now) -> None:
    """委托价超 ±10% 涨跌幅拒单（昨收 100 → 合法区间 [90, 110]）。"""
    q = a_quote(code="000001", price=100.0, prev_close=100.0, name="平安银行")
    fake_market("a_stock", quotes={"000001": q})
    with pytest.raises(OrderRejected, match="涨跌幅"):
        place_order(sim_db, "a_stock", "000001", "buy", "limit", 100, 111.0)
    with pytest.raises(OrderRejected, match="涨跌幅"):
        place_order(sim_db, "a_stock", "000001", "sell", "limit", 100, 89.0)


def test_price_limit_boundary_accepted(sim_db, fake_market, trading_now) -> None:
    """恰好挂在涨跌停价（±10%）不拒单。"""
    q = a_quote(code="000001", price=100.0, prev_close=100.0, name="平安银行")
    fake_market("a_stock", quotes={"000001": q})
    result = place_order(sim_db, "a_stock", "000001", "buy", "limit", 100, 110.0)
    assert result.order.status == "open"


def test_off_session_rejected(sim_db, fake_market, trading_now, monkeypatch) -> None:
    """非交易时段拒单：周末 / A 股午休 / 美股白天（北京时间）。"""
    fake_market(
        "a_stock", quotes={"600519": a_quote()}
    )
    fake_market("us", quotes={"105.MSFT": Quote(market="us", code="105.MSFT", name="微软", price=400.0, prev_close=400.0)})

    monkeypatch.setattr(matching, "_now", lambda: datetime(2026, 9, 26, 10, 0))  # 周六
    with pytest.raises(OrderRejected, match="非交易时段"):
        place_order(sim_db, "a_stock", "600519", "buy", "market", 100)

    monkeypatch.setattr(matching, "_now", lambda: datetime(2026, 9, 30, 12, 0))  # 周三午休
    with pytest.raises(OrderRejected, match="非交易时段"):
        place_order(sim_db, "a_stock", "600519", "buy", "market", 100)

    monkeypatch.setattr(matching, "_now", lambda: datetime(2026, 9, 30, 10, 0))  # 北京时间上午非美股时段
    with pytest.raises(OrderRejected, match="非交易时段"):
        place_order(sim_db, "us", "105.MSFT", "buy", "market", 1)


def test_unknown_market_and_bad_params_rejected(sim_db, fake_market, trading_now) -> None:
    with pytest.raises(OrderRejected, match="不支持的市场"):
        place_order(sim_db, "crypto", "BTC", "buy", "market", 1)
    with pytest.raises(OrderRejected, match="交易方向"):
        place_order(sim_db, "a_stock", "600519", "hold", "market", 100)
    with pytest.raises(OrderRejected, match="委托数量"):
        place_order(sim_db, "a_stock", "600519", "buy", "market", 0)


# -- 场外基金 ---------------------------------------------------------------


def test_fund_subscription_and_redemption(sim_db, fake_market, trading_now, monkeypatch) -> None:
    """场外基金：净值成交（get_quote 无价 → get_kline 最新 close）、申购/赎回费、T+1。"""
    src = fake_market(
        "fund",
        quotes={"000001": Quote(market="fund", code="000001", name="华夏成长混合", price=0.0)},
        nav=1.5,
    )
    result = place_order(sim_db, "fund", "000001", "buy", "market", 10_000)
    assert result.trade is not None
    assert result.trade.price == pytest.approx(1.5)                 # 净价成交
    assert result.trade.amount == pytest.approx(15000.0)
    assert result.trade.subscription_fee == pytest.approx(15.0)     # 申购费 0.1%
    assert result.trade.commission == 0.0                           # 场外不收佣金
    assert get_account(sim_db).cash == pytest.approx(984985.0)
    assert get_position(sim_db, "fund", "000001").available_qty == 0  # 申赎 T+1

    with pytest.raises(OrderRejected, match="可卖数量不足"):
        place_order(sim_db, "fund", "000001", "sell", "market", 10_000)

    monkeypatch.setattr(matching, "_today", lambda: "2026-10-01")
    ensure_new_day(sim_db)
    result = place_order(sim_db, "fund", "000001", "sell", "market", 10_000)
    assert result.trade is not None
    assert result.trade.redemption_fee == pytest.approx(75.0)       # 赎回费 0.5%
    assert get_account(sim_db).cash == pytest.approx(999910.0)


def test_fund_rejects_limit_order(sim_db, fake_market, trading_now) -> None:
    fake_market("fund", quotes={"000001": Quote(market="fund", code="000001", name="华夏成长混合", price=0.0)}, nav=1.5)
    with pytest.raises(OrderRejected, match="不支持限价单"):
        place_order(sim_db, "fund", "000001", "buy", "limit", 10_000, 1.4)


def test_fund_weekend_accepted(sim_db, fake_market, trading_now, monkeypatch) -> None:
    """场外基金全天候受理：周末申赎不拒单。"""
    fake_market("fund", quotes={"000001": Quote(market="fund", code="000001", name="华夏成长混合", price=0.0)}, nav=1.5)
    monkeypatch.setattr(matching, "_now", lambda: datetime(2026, 9, 26, 10, 0))  # 周六
    result = place_order(sim_db, "fund", "000001", "buy", "market", 1000)
    assert result.order.status == "filled"


# -- 限价单：挂单 / 触及成交 / 撤单 ------------------------------------------


def test_limit_order_pends_until_touched(sim_db, fake_market, trading_now) -> None:
    q = a_quote(code="000001", price=10.00, prev_close=10.00, name="平安银行")
    fake_market("a_stock", quotes={"000001": q})

    result = place_order(sim_db, "a_stock", "000001", "buy", "limit", 100, 9.90)
    assert result.order.status == "open" and result.trade is None

    trades = check_open_orders(sim_db)                              # 现价 10.00 未触及 9.90
    assert trades == []
    assert get_position(sim_db, "a_stock", "000001") is None

    q.price = 9.88                                                   # 快照价触及限价
    trades = check_open_orders(sim_db)
    assert len(trades) == 1
    assert trades[0].price == pytest.approx(9.90)                   # 按委托价成交
    assert trades[0].amount == pytest.approx(990.0)
    assert trades[0].commission == pytest.approx(5.0)               # 最低佣金
    assert get_account(sim_db).cash == pytest.approx(999_005.0)     # 100 万 - 990 - 5


def test_limit_sell_triggers_when_price_rises(sim_db, fake_market, trading_now, monkeypatch) -> None:
    q = a_quote(price=10.00, prev_close=10.00, name="测试")
    fake_market("a_stock", quotes={"600519": q})
    place_order(sim_db, "a_stock", "600519", "buy", "market", 1000)

    monkeypatch.setattr(matching, "_today", lambda: "2026-10-01")
    ensure_new_day(sim_db)
    result = place_order(sim_db, "a_stock", "600519", "sell", "limit", 1000, 10.50)
    assert result.order.status == "open"

    q.price = 10.50
    trades = check_open_orders(sim_db)
    assert len(trades) == 1
    assert trades[0].price == pytest.approx(10.50)
    assert trades[0].stamp_tax == pytest.approx(5.25)               # 10500 × 万5
    assert get_position(sim_db, "a_stock", "600519") is None


def test_cancel_order_lifecycle(sim_db, fake_market, trading_now) -> None:
    q = a_quote(code="000001", price=10.00, prev_close=10.00, name="平安银行")
    fake_market("a_stock", quotes={"000001": q})
    result = place_order(sim_db, "a_stock", "000001", "buy", "limit", 100, 9.90)

    order = cancel_order(sim_db, result.order.id)
    assert order.status == "cancelled"

    with pytest.raises(OrderRejected, match="仅挂单"):
        cancel_order(sim_db, result.order.id)                       # 重复撤单
    with pytest.raises(LookupError):
        cancel_order(sim_db, 9999)                                  # 不存在


def test_open_sell_rejected_at_fill_when_shares_gone(
    sim_db, fake_market, trading_now, monkeypatch
) -> None:
    """挂单期间可卖量被占用：触及限价时转 rejected 而非成交。"""
    q = a_quote(price=10.00, prev_close=10.00, name="测试")
    fake_market("a_stock", quotes={"600519": q})
    place_order(sim_db, "a_stock", "600519", "buy", "market", 200)

    monkeypatch.setattr(matching, "_today", lambda: "2026-10-01")
    ensure_new_day(sim_db)
    # 两笔限价卖单各 200，但持仓只有 200（挂单不冻结份额）
    first = place_order(sim_db, "a_stock", "600519", "sell", "limit", 200, 10.20)
    second = place_order(sim_db, "a_stock", "600519", "sell", "limit", 200, 10.30)

    q.price = 10.40                                                 # 两单同时触及
    check_open_orders(sim_db)
    sim_db.expire_all()
    assert first.order.status == "filled"
    assert second.order.status == "rejected"
    assert "可卖数量不足" in second.order.reject_reason


# -- 重新开局 ---------------------------------------------------------------


def test_reset_account(sim_db, fake_market, trading_now) -> None:
    fake_market("a_stock", quotes={"600519": a_quote()})
    place_order(sim_db, "a_stock", "600519", "buy", "market", 1000)

    acc = reset_account(sim_db)
    assert acc.cash == pytest.approx(INIT_CASH)
    assert acc.reset_at is not None
    assert get_position(sim_db, "a_stock", "600519") is None
    assert sim_db.scalars(select(SimOrder)).all() == []
    assert len(sim_db.scalars(select(SimPosition)).all()) == 0

    # 重开后可正常再交易
    result = place_order(sim_db, "a_stock", "600519", "buy", "market", 100)
    assert result.order.status == "filled"
