# -*- coding: utf-8 -*-
"""数据源抽象接口与数据结构。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Quote:
    market: str
    code: str
    name: str
    price: float
    prev_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    time: str = ""
    stale: bool = False


@dataclass
class Bar:
    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    change_pct: float = 0.0


@dataclass
class SearchResult:
    market: str
    code: str
    name: str
    extra: dict = field(default_factory=dict)


class MarketDataSource(ABC):
    """单一市场的数据源。所有实现必须自行处理容错与缓存。"""

    market: str

    @abstractmethod
    def search(self, keyword: str, limit: int = 20) -> list[SearchResult]:
        """按代码或名称模糊搜索。"""

    @abstractmethod
    def get_quote(self, code: str) -> Quote:
        """实时报价（免费数据源约 3 秒延迟）。"""

    @abstractmethod
    def get_kline(self, code: str, days: int = 250) -> tuple[list[Bar], bool]:
        """日 K 线（场外基金为净值序列），返回 (bars, stale)。"""
