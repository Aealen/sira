# -*- coding: utf-8 -*-
"""API 出入参模型。"""
from pydantic import BaseModel


class SearchItem(BaseModel):
    market: str
    code: str
    name: str


class QuoteOut(BaseModel):
    market: str
    code: str
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    change: float
    change_pct: float
    volume: float
    amount: float
    time: str
    stale: bool


class BarOut(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    change_pct: float


class KlineOut(BaseModel):
    market: str
    code: str
    name: str
    stale: bool
    bars: list[BarOut]
