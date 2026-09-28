# -*- coding: utf-8 -*-
"""市场注册表：按 market 名取数据源实例。"""
from app.services.datasource.akshare_source import A_STOCK, ETF, FUND, HK, US
from app.services.datasource.base import MarketDataSource

_SOURCES: dict[str, MarketDataSource] = {
    s.market: s for s in (A_STOCK, ETF, FUND, HK, US)
}

MARKET_LABELS: dict[str, str] = {
    "a_stock": "A股",
    "etf": "场内ETF",
    "fund": "场外基金",
    "hk": "港股",
    "us": "美股",
}


def get_source(market: str) -> MarketDataSource:
    src = _SOURCES.get(market)
    if src is None:
        raise KeyError(f"不支持的市场: {market}，可选: {', '.join(_SOURCES)}")
    return src


def list_markets() -> dict[str, str]:
    return dict(MARKET_LABELS)
