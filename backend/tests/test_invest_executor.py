# -*- coding: utf-8 -*-
"""执行器单测：执行流水、失败降级、smart_dca 金额调整、调度器构建。

分层隔离策略：
- 纯单测：monkeypatch executor 的 get_current_price / _place_buy / valuation_percentile，
  不触达撮合引擎；
- 集成（TestRealMatchingIntegration）：走真实 app.services.matching.place_order，
  仅 monkeypatch 行情与交易时段。
"""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.services.invest.executor as executor
from app.models_invest import InvestExecution
from tests.conftest import make_plan

TODAY = date(2026, 9, 15)


def _fake_result(quantity: float, fee: float, deal_amount: float) -> SimpleNamespace:
    """模拟 matching.PlaceResult（order 省略，executor 只读 trade）。"""
    return SimpleNamespace(trade=SimpleNamespace(
        quantity=quantity, total_fee=fee, amount=deal_amount))


@pytest.fixture
def fake_market(monkeypatch):
    """固定价格 + 记录撮合调用的假行情/撮合环境。"""
    calls: list[dict] = []

    def fake_price(market, code):
        return 10.0

    def fake_buy(db, market, code, price, amount):
        calls.append({"market": market, "code": code, "price": price, "amount": amount})
        return _fake_result(quantity=99.75, fee=0.25, deal_amount=999.75)

    monkeypatch.setattr(executor, "get_current_price", fake_price)
    monkeypatch.setattr(executor, "_place_buy", fake_buy)
    return calls


class TestRunDuePlans:
    def test_due_plan_executed_and_recorded(self, db_session, fake_market):
        plan = make_plan(db_session, day_of_month=10)
        results = executor.run_due_plans(db_session, today=TODAY)

        assert len(results) == 1
        r = results[0]
        assert r.ok is True and r.plan_id == plan.id
        assert r.quantity == pytest.approx(99.75)

        row = db_session.scalars(select(InvestExecution)).one()
        assert row.plan_id == plan.id
        assert row.exec_date == "2026-09-15"
        assert row.price == pytest.approx(10.0)
        assert row.amount == pytest.approx(1000.0)

        db_session.refresh(plan)
        assert plan.last_exec_date == "2026-09-15"
        # 撮合收到的参数：市价与基准金额
        assert fake_market[0]["price"] == pytest.approx(10.0)
        assert fake_market[0]["amount"] == pytest.approx(1000.0)

    def test_not_due_plan_skipped(self, db_session, fake_market):
        make_plan(db_session, day_of_month=20)  # 9 月 20 日才到期
        results = executor.run_due_plans(db_session, today=TODAY)
        assert results == []
        assert db_session.scalars(select(InvestExecution)).all() == []

    def test_paused_plan_skipped(self, db_session, fake_market):
        make_plan(db_session, day_of_month=10, status="paused")
        assert executor.run_due_plans(db_session, today=TODAY) == []

    def test_insufficient_funds_skipped_without_exception(self, db_session, monkeypatch):
        """资金不足 → 记录失败跳过，不抛异常，不写流水，last_exec_date 不更新（后续自动重试）。"""
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: 10.0)

        def no_cash(db, market, code, price, amount):
            raise RuntimeError("模拟盘资金不足")

        monkeypatch.setattr(executor, "_place_buy", no_cash)
        plan = make_plan(db_session, day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert len(results) == 1
        assert results[0].ok is False
        assert "资金不足" in results[0].message
        assert db_session.scalars(select(InvestExecution)).all() == []
        db_session.refresh(plan)
        assert plan.last_exec_date is None

    def test_quote_failure_skipped(self, db_session, monkeypatch):
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: (_ for _ in ()).throw(LookupError("无数据")))
        monkeypatch.setattr(executor, "_place_buy", lambda *a, **k: _fake_result(1.0, 0.0, 1.0))
        plan = make_plan(db_session, day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert results[0].ok is False
        assert "行情获取失败" in results[0].message
        assert db_session.scalars(select(InvestExecution)).all() == []

    def test_failed_plan_does_not_block_others(self, db_session, monkeypatch):
        monkeypatch.setattr(
            executor, "get_current_price",
            lambda m, c: 10.0 if c == "510300" else (_ for _ in ()).throw(RuntimeError("x")),
        )
        monkeypatch.setattr(
            executor, "_place_buy",
            lambda db, market, code, price, amount: _fake_result(99.75, 0.25, amount - 0.25),
        )
        ok_plan = make_plan(db_session, code="510300", day_of_month=10)
        make_plan(db_session, code="159915", name="创业板ETF", day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert [r.ok for r in results] == [True, False]
        assert db_session.scalars(select(InvestExecution)).one().plan_id == ok_plan.id


class TestSmartDca:
    def _smart_plan(self, db, **kw):
        kw.setdefault("smart_dca", True)
        return make_plan(db, amount=1000.0, **kw)

    def test_off_returns_base_amount(self, db_session, monkeypatch):
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: 5.0)
        plan = self._smart_plan(db_session, smart_dca=False)
        assert executor.smart_amount(plan) == pytest.approx(1000.0)

    def test_missing_valuation_falls_back_to_base(self, db_session, monkeypatch):
        """估值模块缺失/失败 → 降级为基准金额。"""
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: None)
        plan = self._smart_plan(db_session)
        assert executor.smart_amount(plan) == pytest.approx(1000.0)

    def test_low_percentile_multiplies_1_5(self, db_session, monkeypatch):
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: 20.0)
        plan = self._smart_plan(db_session)
        assert executor.smart_amount(plan) == pytest.approx(1500.0)

    def test_high_percentile_multiplies_0_5(self, db_session, monkeypatch):
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: 80.0)
        plan = self._smart_plan(db_session)
        assert executor.smart_amount(plan) == pytest.approx(500.0)

    def test_mid_percentile_keeps_base(self, db_session, monkeypatch):
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: 50.0)
        plan = self._smart_plan(db_session)
        assert executor.smart_amount(plan) == pytest.approx(1000.0)

    def test_smart_amount_flows_into_order(self, db_session, fake_market, monkeypatch):
        """smart_dca 加码金额实际传入撮合。"""
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: 20.0)
        make_plan(db_session, smart_dca=True, day_of_month=10)
        results = executor.run_due_plans(db_session, today=TODAY)

        assert results[0].ok is True
        assert fake_market[0]["amount"] == pytest.approx(1500.0)


class TestValuationImportFallback:
    def test_valuation_module_missing_returns_none(self):
        """真实环境下（无 app.services.valuation）取分位返回 None 而非报错。"""
        assert executor.valuation_percentile("etf", "510300") is None


class TestScheduler:
    def test_init_scheduler_builds_daily_job_without_starting(self):
        scheduler = executor.init_scheduler()
        # 只定义不启动：无后台线程，未启动状态也无需 shutdown
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "invest_dca_daily"
        assert scheduler.running is False


class TestFundNetValue:
    def test_fund_price_uses_latest_kline_close(self, monkeypatch):
        """场外基金：无实时价，用 get_kline 最新 close 作净值。"""
        from app.services.datasource.base import Bar

        class _FakeSource:
            def get_kline(self, code, days=250):
                bars = [
                    Bar(date="2026-09-11", open=1, high=1, low=1, close=1.234),
                    Bar(date="2026-09-12", open=1, high=1, low=1, close=1.250),
                ]
                return bars, False

            def get_quote(self, code):  # 不应被调用
                raise AssertionError("场外基金不应走实时报价")

        monkeypatch.setattr(executor, "get_source", lambda market: _FakeSource())
        price = executor.get_current_price("fund", "110022")
        assert price == pytest.approx(1.250)

    def test_etf_price_uses_quote(self, monkeypatch):
        from app.services.datasource.base import Quote

        class _FakeSource:
            def get_quote(self, code):
                return Quote(market="etf", code=code, name="x", price=4.567)

        monkeypatch.setattr(executor, "get_source", lambda market: _FakeSource())
        assert executor.get_current_price("etf", "510300") == pytest.approx(4.567)


class TestRealMatchingIntegration:
    """走真实撮合引擎（仅 mock 行情与交易时段），验证 place_order 适配。"""

    @pytest.fixture
    def real_matching(self, db_session, monkeypatch):
        from app.services import matching
        from app.services.datasource.base import Quote

        def fake_quote(db, market, code):
            price = 1.25 if market == "fund" else 4.0
            return Quote(market=market, code=code, name="测试标的", price=price)

        monkeypatch.setattr(matching, "current_quote", fake_quote)
        monkeypatch.setattr(matching, "is_in_session", lambda market, now=None: True)
        return matching

    def test_etf_buy_amount_to_lot_quantity(self, db_session, real_matching, monkeypatch):
        """1000 元买 4 元 ETF：预留佣金 5 元 → 995//400=2 手 → 200 份，扣款 805。"""
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: 4.0)
        make_plan(db_session, market="etf", amount=1000.0, day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert results[0].ok is True, results[0].message
        assert results[0].quantity == 200

        trade = db_session.scalars(select(real_matching.SimTrade)).one()
        assert trade.quantity == 200
        assert trade.amount == pytest.approx(800.0)
        assert trade.total_fee == pytest.approx(5.0)  # 最低佣金 5 元

        acc = real_matching.get_account(db_session)
        assert acc.cash == pytest.approx(1_000_000.0 - 805.0)

        row = db_session.scalars(select(InvestExecution)).one()
        assert row.quantity == 200
        assert row.amount == pytest.approx(805.0)  # 含费投入
        assert row.fee == pytest.approx(5.0)
        assert row.price == pytest.approx(4.0)

    def test_fund_buy_by_nav(self, db_session, real_matching, monkeypatch):
        """场外基金：1000 元按净值 1.25 申赎，预留申购费 1 元 → 799 份。"""
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: 1.25)
        plan = make_plan(db_session, market="fund", code="110022", name="沪深300联接",
                         amount=1000.0, day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert results[0].ok is True, results[0].message
        trade = db_session.scalars(select(real_matching.SimTrade)).one()
        assert trade.quantity == 799  # (1000-1)/1.25 = 799.2 → 799 份
        assert trade.total_fee == pytest.approx(round(998.75 * 0.001, 2))  # 申购费 1.0
        row = db_session.scalars(select(InvestExecution)).one()
        assert row.amount == pytest.approx(998.75 + 1.0)

    def test_insufficient_cash_rejected_and_skipped(self, db_session, real_matching, monkeypatch):
        """现金不足 → 撮合拒单（OrderRejected）→ 记录失败跳过，不写流水。"""
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: 4.0)
        acc = real_matching.get_account(db_session)
        acc.cash = 100.0
        db_session.commit()
        plan = make_plan(db_session, market="etf", amount=1000.0, day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert results[0].ok is False
        assert "资金不足" in results[0].message
        assert db_session.scalars(select(InvestExecution)).all() == []
        db_session.refresh(plan)
        assert plan.last_exec_date is None  # 不更新，后续调度自动重试

    def test_amount_below_min_lot_fails(self, db_session, real_matching, monkeypatch):
        """金额不足一手（100 份 × 4 元 = 400 元 + 费用）→ 失败跳过。"""
        monkeypatch.setattr(executor, "get_current_price", lambda m, c: 4.0)
        make_plan(db_session, market="etf", amount=300.0, day_of_month=10)

        results = executor.run_due_plans(db_session, today=TODAY)

        assert results[0].ok is False
        assert "不足以下单" in results[0].message
        assert db_session.scalars(select(InvestExecution)).all() == []
