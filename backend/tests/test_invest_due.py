# -*- coding: utf-8 -*-
"""计划到期判断（is_due）单测。"""
from datetime import date

from app.services.invest.executor import is_due


def _plan(**kw):
    class _Fake:
        pass

    # 直接构造轻量对象，避免依赖数据库
    p = _Fake()
    p.market, p.code, p.name = "etf", "510300", "沪深300ETF"
    p.frequency = kw.get("frequency", "monthly")
    p.day_of_month = kw.get("day_of_month", 10)
    p.amount = kw.get("amount", 1000.0)
    p.status = kw.get("status", "active")
    p.last_exec_date = kw.get("last_exec_date")
    return p


TODAY = date(2026, 9, 15)


class TestMonthly:
    def test_before_day_of_month_not_due(self):
        assert is_due(_plan(day_of_month=20), TODAY) is False

    def test_on_day_of_month_due(self):
        assert is_due(_plan(day_of_month=15), TODAY) is True

    def test_after_day_of_month_catch_up_due(self):
        # 10 号应执行，今天 15 号仍未执行 → 补扣
        assert is_due(_plan(day_of_month=10), TODAY) is True

    def test_already_executed_this_month_not_due(self):
        assert is_due(_plan(day_of_month=10, last_exec_date="2026-09-10"), TODAY) is False
        assert is_due(_plan(day_of_month=10, last_exec_date="2026-09-15"), TODAY) is False

    def test_executed_last_month_due_again(self):
        assert is_due(_plan(day_of_month=10, last_exec_date="2026-08-10"), TODAY) is True

    def test_day_28_boundary(self):
        # TODAY=9/15，28 号尚未到 → 不到期；day_of_month 最大 28 保证任何月份都存在
        assert is_due(_plan(day_of_month=28), TODAY) is False
        assert is_due(_plan(day_of_month=14), TODAY) is True

    def test_paused_not_due(self):
        assert is_due(_plan(day_of_month=10, status="paused"), TODAY) is False


class TestIntervalFrequencies:
    def test_weekly_never_executed_due(self):
        assert is_due(_plan(frequency="weekly"), TODAY) is True

    def test_weekly_within_gap_not_due(self):
        assert is_due(_plan(frequency="weekly", last_exec_date="2026-09-10"), TODAY) is False

    def test_weekly_gap_reached_due(self):
        assert is_due(_plan(frequency="weekly", last_exec_date="2026-09-08"), TODAY) is True

    def test_biweekly_within_gap_not_due(self):
        assert is_due(_plan(frequency="biweekly", last_exec_date="2026-09-05"), TODAY) is False

    def test_biweekly_gap_reached_due(self):
        assert is_due(_plan(frequency="biweekly", last_exec_date="2026-09-01"), TODAY) is True

    def test_unknown_frequency_not_due(self):
        assert is_due(_plan(frequency="daily"), TODAY) is False
