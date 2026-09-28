# -*- coding: utf-8 -*-
"""五市场交易规则表（M3 模拟盘撮合依据；回测引擎 B 档二期复用同一张表）。

初稿值来自 docs/04-工具与实现/模拟盘撮合规则.md（2026-09 归档），
均为设计时记忆值，**实现时须逐一核对最新规则**（印花税率、T+N 交收制度、
最小报价单位等会随监管调整）。
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketRule:
    """单市场撮合规则参数（纯数据，费率另见 app/config.py 的 settings）。"""

    market: str                      # a_stock/etf/fund/hk/us
    label: str                       # 中文名（下单面板规则徽章展示）
    t_plus: int                      # T+N：买入后 N 个交易日可卖（0=T+0 回转交易）
    price_limit_pct: float | None    # 涨跌幅限制（0.10=±10%），None=无限制
    min_lot: int | None              # 最小买入单位（100=整百/整手，1=单份起，None=不限）
    trade_sessions: tuple[str, ...]  # 连续交易时段 "HH:MM-HH:MM"（本地时间，支持跨午夜）
    support_limit: bool              # 是否支持限价单（场外基金按未知价法申赎，不支持）
    stamp_tax_on_sell: bool          # 卖出是否收印花税（费率取 settings.stamp_tax_rate）
    always_open: bool = False        # 全天候受理（场外基金申赎随时提交，按最新净值成交）


MARKET_RULES: dict[str, MarketRule] = {
    "a_stock": MarketRule(
        market="a_stock",
        label="A股",
        t_plus=1,                    # 当日买入次日可卖
        price_limit_pct=0.10,        # 主板 ±10%；创业板/科创板 ±20%、ST ±5% 按标的细分待核对
        min_lot=100,                 # 买入 100 股整数倍，零股仅可卖出
        trade_sessions=("09:30-11:30", "13:00-15:00"),  # 集合竞价 9:15-9:25 未建模，简化为仅连续竞价时段
        support_limit=True,
        stamp_tax_on_sell=True,      # 印花税万5 仅卖出（2023-08 起）
    ),
    "etf": MarketRule(
        market="etf",
        label="场内ETF",
        t_plus=1,                    # 股票型 T+1；跨境/债券/货币 ETF 实际 T+0（如 513100），按标的细分待核对
        price_limit_pct=0.10,        # 同 A 股；跨境 ETF 实际宽幅/无限制，待核对
        min_lot=100,                 # 100 份整数倍
        trade_sessions=("09:30-11:30", "13:00-15:00"),
        support_limit=True,
        stamp_tax_on_sell=False,     # ETF 免印花税
    ),
    "fund": MarketRule(
        market="fund",
        label="场外基金",
        t_plus=1,                    # 申赎 T+1 确认，简化为份额 T+1 可卖
        price_limit_pct=None,        # 净值每日一价，无涨跌幅
        min_lot=1,                   # 1 份起（金额申赎为主流，简化按份额）
        trade_sessions=("00:00-23:59",),  # 全天候受理：15:00 截止的未知价法简化为统一按最新可得净值成交
        support_limit=False,         # 按净值申赎，无挂单概念
        stamp_tax_on_sell=False,
        always_open=True,            # 周末提交同样受理（按下个净值日处理，简化不拒单）
    ),
    "hk": MarketRule(
        market="hk",
        label="港股",
        t_plus=0,                    # T+0 回转交易（T+2 交收，模拟盘不模拟交收）
        price_limit_pct=None,        # 无涨跌幅（有 VCM 市调冷静期，未建模）
        min_lot=100,                 # 每手股数因股而异（100/500/2000 等），模拟盘简化统一 100
        trade_sessions=("09:30-12:00", "13:00-16:00"),  # 未建模开市/收市竞价时段
        support_limit=True,
        stamp_tax_on_sell=True,      # 实际约 0.1% 双边，模拟盘统一用 settings.stamp_tax_rate（实现时核对）
    ),
    "us": MarketRule(
        market="us",
        label="美股",
        t_plus=0,                    # T+0 回转交易（T+1 交收，模拟盘不模拟交收）
        price_limit_pct=None,        # 无涨跌幅（有熔断机制，未建模）
        min_lot=1,                   # 1 股起
        trade_sessions=("21:30-04:00",),  # 北京时间美东夏令时；冬令时顺延为 22:30-05:00（实现时按当前日期核对）
        support_limit=True,
        stamp_tax_on_sell=False,
    ),
}


def get_rule(market: str) -> MarketRule:
    """取市场规则；未注册市场抛 KeyError（信息含可选市场列表）。"""
    rule = MARKET_RULES.get(market)
    if rule is None:
        raise KeyError(f"不支持的市场: {market}，可选: {', '.join(MARKET_RULES)}")
    return rule


def _minutes(hhmm: str) -> int:
    """\"HH:MM\" 转当日分钟数。"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_in_session(market: str, now: datetime | None = None) -> bool:
    """判断 now（默认当前本地时间）是否处于该市场连续交易时段。

    简化边界：
    - 仅按星期几剔除周末，不维护交易日历（法定节假日照常视为开市）；
    - 跨午夜时段（美股北京时间 21:30-04:00）按两段拼接判断；
    - A 股 9:15 前预埋单按 M3 约定直接拒单，不单独建模集合竞价。
    """
    rule = get_rule(market)
    if rule.always_open:
        return True
    now = now or datetime.now()
    if now.weekday() >= 5:  # 周六/周日
        return False
    t = now.hour * 60 + now.minute
    for session in rule.trade_sessions:
        start_s, end_s = session.split("-")
        start, end = _minutes(start_s), _minutes(end_s)
        if start <= end:
            if start <= t < end:
                return True
        elif t >= start or t < end:  # 跨午夜时段（如美股夜盘延续到次日凌晨）
            return True
    return False
