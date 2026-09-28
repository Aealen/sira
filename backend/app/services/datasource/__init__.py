# -*- coding: utf-8 -*-
"""数据源层：以适配器模式隔离 akshare 的不稳定性。

设计（对应 docs/04-工具与实现/M1 设计文档）：
- 每个市场一个 MarketDataSource 实现；
- 实时报价/搜索基于"全市场快照"，一次拉取服务多只标的；
- 三级容错：内存缓存(TTL) → akshare 网络拉取 → SQLite 持久缓存兜底（返回 stale 标记）。
"""
from app.services.datasource.base import Bar, MarketDataSource, Quote
from app.services.datasource.registry import get_source, list_markets

__all__ = ["Bar", "MarketDataSource", "Quote", "get_source", "list_markets"]
