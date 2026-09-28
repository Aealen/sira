# -*- coding: utf-8 -*-
"""市场规则表与交易时段判断单测（对应 app/market_rules.py）。"""
from datetime import datetime

import pytest

from app.market_rules import MARKET_RULES, get_rule, is_in_session

WED = datetime(2026, 9, 30, 10, 0)   # 周三
THU = datetime(2026, 10, 1, 2, 0)    # 周四凌晨（美股夜盘延续）
SAT = datetime(2026, 9, 26, 10, 0)   # 周六


def at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute)


def test_rules_cover_five_markets() -> None:
    assert set(MARKET_RULES) == {"a_stock", "etf", "fund", "hk", "us"}


def test_rule_key_fields() -> None:
    assert get_rule("a_stock").t_plus == 1
    assert get_rule("a_stock").price_limit_pct == pytest.approx(0.10)
    assert get_rule("a_stock").min_lot == 100
    assert get_rule("a_stock").stamp_tax_on_sell is True
    assert get_rule("etf").min_lot == 100
    assert get_rule("etf").stamp_tax_on_sell is False
    assert get_rule("fund").min_lot == 1
    assert get_rule("fund").support_limit is False
    assert get_rule("fund").always_open is True
    assert get_rule("hk").t_plus == 0
    assert get_rule("hk").min_lot == 100  # 每手因股而异，简化统一 100
    assert get_rule("us").t_plus == 0
    assert get_rule("us").min_lot == 1
    assert get_rule("us").price_limit_pct is None
    assert get_rule("hk").price_limit_pct is None


def test_get_rule_unknown_market() -> None:
    with pytest.raises(KeyError, match="不支持的市场"):
        get_rule("crypto")


# -- A 股连续竞价时段（含午休与收盘边界，端点左闭右开） ----------------------


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (at(WED, 9, 29), False),   # 盘前（9:15 预埋单简化为直接拒）
        (at(WED, 9, 30), True),
        (at(WED, 11, 29), True),
        (at(WED, 11, 30), False),  # 午休
        (at(WED, 12, 59), False),
        (at(WED, 13, 0), True),
        (at(WED, 14, 59), True),
        (at(WED, 15, 0), False),   # 已收盘
        (SAT, False),              # 周末
    ],
)
def test_a_stock_session(dt: datetime, expected: bool) -> None:
    assert is_in_session("a_stock", dt) is expected
    assert is_in_session("etf", dt) is expected


def test_hk_session() -> None:
    assert is_in_session("hk", at(WED, 9, 29)) is False
    assert is_in_session("hk", at(WED, 10, 0)) is True
    assert is_in_session("hk", at(WED, 12, 0)) is False  # 午休
    assert is_in_session("hk", at(WED, 13, 0)) is True
    assert is_in_session("hk", at(WED, 16, 0)) is False


# -- 美股跨午夜时段（北京时间夏令时 21:30-04:00） ---------------------------


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (at(WED, 10, 0), False),    # 北京时间上午非美股时段
        (at(WED, 21, 29), False),
        (at(WED, 21, 30), True),
        (at(WED, 23, 59), True),
        (at(THU, 0, 0), True),      # 跨午夜延续
        (at(THU, 3, 59), True),
        (at(THU, 4, 0), False),     # 收盘
    ],
)
def test_us_session_cross_midnight(dt: datetime, expected: bool) -> None:
    assert is_in_session("us", dt) is expected


def test_fund_always_open() -> None:
    assert is_in_session("fund", SAT) is True          # 周末也可申赎
    assert is_in_session("fund", at(WED, 3, 0)) is True
