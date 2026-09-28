# -*- coding: utf-8 -*-
"""复盘统计单测：应执行期数、纪律检查、总收益计算。"""
from datetime import date, datetime, timedelta

import pytest

import app.services.invest.executor as executor
from tests.conftest import make_exec, make_plan


def _months_ago(n: int, day: int = 1) -> datetime:
    """n 个月前的 datetime（粗略回退，足够构造测试数据）。"""
    d = date.today()
    total = d.year * 12 + d.month - 1 - n
    return datetime(total // 12, total % 12 + 1, day)


class TestExpectedPeriods:
    def test_monthly_created_after_due_day_starts_next_month(self):
        class P:
            frequency = "monthly"
            day_of_month = 5
            created_at = datetime(2026, 1, 20)  # 20 号创建，1 月 5 日已过 → 首期 2 月

        assert executor._expected_periods(P(), date(2026, 3, 10)) == 2  # 2 月、3 月

    def test_monthly_created_on_due_day_includes_current_month(self):
        class P:
            frequency = "monthly"
            day_of_month = 5
            created_at = datetime(2026, 1, 5)

        assert executor._expected_periods(P(), date(2026, 3, 10)) == 3  # 1/2/3 月

    def test_monthly_before_this_month_due_day(self):
        class P:
            frequency = "monthly"
            day_of_month = 25
            created_at = datetime(2026, 1, 1)

        assert executor._expected_periods(P(), date(2026, 3, 10)) == 2  # 1/2 月，3 月 25 未到

    def test_weekly_periods(self):
        class P:
            frequency = "weekly"
            day_of_month = 1
            created_at = datetime(2026, 1, 1)

        assert executor._expected_periods(P(), date(2026, 1, 15)) == 3  # 1/8/15

    def test_biweekly_periods(self):
        class P:
            frequency = "biweekly"
            day_of_month = 1
            created_at = datetime(2026, 1, 1)

        assert executor._expected_periods(P(), date(2026, 1, 29)) == 3  # 1/15/29


class TestBuildReview:
    def test_totals_and_pnl(self, db_session):
        plan = make_plan(db_session, day_of_month=1, created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(1, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0, fee=0.0)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 12.0)
        assert review["total_invested"] == pytest.approx(1000.0)
        assert review["total_market_value"] == pytest.approx(1200.0)
        assert review["total_pnl_pct"] == pytest.approx(20.0)

    def test_price_lookup_failure_falls_back_to_last_price(self, db_session):
        plan = make_plan(db_session, created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date="2026-08-01", price=8.0, quantity=100.0, amount=800.0)
        review = executor.build_review(db_session, price_lookup=lambda m, c: None)
        # 取不到实时价 → 退回最后成交价 8.0，市值 800，不赚不亏
        assert review["total_market_value"] == pytest.approx(800.0)
        assert review["total_pnl_pct"] == pytest.approx(0.0)

    def test_discipline_all_on_time(self, db_session):
        # 创建于 1 个月前、每月 1 日，上月已执行一期；本月 1 日是否已执行取决于今天日期
        plan = make_plan(db_session, day_of_month=1, created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(1, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)
        # 本月一期：无论今天几号，把本月也补上 → 一定不逾期
        if date.today().day >= 1:
            make_exec(db_session, plan, exec_date=date.today().replace(day=1).isoformat(),
                      price=10.0, quantity=100.0, amount=1000.0)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 10.0)
        check1 = review["discipline"][0]
        assert check1["passed"] is True

    def test_discipline_overdue_detected(self, db_session):
        # 创建于 3 个月前、每月 1 日，只执行了 1 期 → 缺 2 期以上
        plan = make_plan(db_session, day_of_month=1, created_at=_months_ago(3, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(3, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 10.0)
        check1 = review["discipline"][0]
        assert check1["passed"] is False
        detail = check1["detail"][0]
        assert detail["plan_id"] == plan.id
        assert detail["missed"] >= 2

    def test_discipline_long_paused_detected(self, db_session):
        long_ago = (date.today() - timedelta(days=60)).isoformat()
        make_plan(db_session, status="paused", day_of_month=1, last_exec_date=long_ago,
                  created_at=_months_ago(3, day=1))

        review = executor.build_review(db_session, price_lookup=lambda m, c: 10.0)
        check2 = review["discipline"][1]
        assert check2["passed"] is False
        assert check2["detail"][0]["last_exec_date"] == long_ago

    def test_discipline_recently_paused_ok(self, db_session):
        recent = (date.today() - timedelta(days=5)).isoformat()
        make_plan(db_session, status="paused", day_of_month=1, last_exec_date=recent,
                  created_at=_months_ago(2, day=1))
        review = executor.build_review(db_session, price_lookup=lambda m, c: 10.0)
        assert review["discipline"][1]["passed"] is True

    def test_take_profit_target_return_triggered(self, db_session):
        # 投入 1000，市值 1300（+30%）→ 触发 15% 止盈线
        plan = make_plan(db_session, take_profit_mode="target_return", take_profit_value=15.0,
                         created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(1, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 13.0)
        check3 = review["discipline"][2]
        assert check3["passed"] is False
        hit = check3["detail"][0]
        assert hit["mode"] == "target_return" and hit["plan_id"] == plan.id

    def test_take_profit_not_triggered_below_line(self, db_session):
        plan = make_plan(db_session, take_profit_mode="target_return", take_profit_value=50.0,
                         created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(1, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 11.0)  # +10%
        assert review["discipline"][2]["passed"] is True

    def test_take_profit_valuation_mode(self, db_session, monkeypatch):
        plan = make_plan(db_session, take_profit_mode="valuation", take_profit_value=80.0,
                         created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(1, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: 85.0)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 10.0)
        check3 = review["discipline"][2]
        assert check3["passed"] is False
        assert check3["detail"][0]["mode"] == "valuation"

    def test_take_profit_valuation_unavailable_noted(self, db_session, monkeypatch):
        plan = make_plan(db_session, take_profit_mode="valuation", take_profit_value=80.0,
                         created_at=_months_ago(1, day=1))
        make_exec(db_session, plan, exec_date=_months_ago(1, day=1).date().isoformat(),
                  price=10.0, quantity=100.0, amount=1000.0)
        monkeypatch.setattr(executor, "valuation_percentile", lambda m, c: None)

        review = executor.build_review(db_session, price_lookup=lambda m, c: 10.0)
        check3 = review["discipline"][2]
        assert check3["passed"] is True  # 未触发，但备注无法评估
        assert "估值分位不可用" in check3["detail"]

    def test_empty_plans_review(self, db_session):
        review = executor.build_review(db_session)
        assert review["total_invested"] == 0.0
        assert review["total_pnl_pct"] == 0.0
        assert all(c["passed"] is True for c in review["discipline"])


class TestPlanStats:
    def test_snapshot_and_stats(self, db_session):
        plan = make_plan(db_session, created_at=_months_ago(2, day=1))
        make_exec(db_session, plan, exec_date="2026-07-01", price=10.0, quantity=100.0, amount=1000.0)
        make_exec(db_session, plan, exec_date="2026-08-01", price=8.0, quantity=125.0, amount=1000.0)

        snapshot = executor.plan_snapshot(db_session, plan)
        assert snapshot["invested"] == pytest.approx(2000.0)
        assert snapshot["executions"] == 2
        assert snapshot["quantity"] == pytest.approx(225.0)
        assert snapshot["last_price"] == pytest.approx(8.0)

        stats = executor.assemble_stats(snapshot, price=12.0)
        assert stats["market_value"] == pytest.approx(2700.0)
        assert stats["pnl_pct"] == pytest.approx(35.0)

        # 行情缺失 → 退回最后成交价
        fallback = executor.assemble_stats(snapshot, price=None)
        assert fallback["market_value"] == pytest.approx(1800.0)
