# -*- coding: utf-8 -*-
"""akshare 数据源实现：A股 / 场内ETF / 场外基金 / 港股 / 美股。

akshare 接口列名为中文且随版本可能微调，所有列名经 ColumnMap 映射、
缺失列安全降级为 0，避免单个字段变化击穿整个服务。
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import KlineCache, SpotCache
from app.services.datasource.base import Bar, MarketDataSource, Quote, SearchResult

logger = logging.getLogger(__name__)


def _num(v) -> float:
    """akshare 返回值常混有 NaN/'-'/字符串，统一安全转 float。"""
    try:
        f = float(v)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 全市场快照提供器：内存 TTL → 网络 → SQLite 兜底
# ---------------------------------------------------------------------------


@dataclass
class ColumnMap:
    code: str
    name: str
    price: str
    prev_close: str | None = None
    open: str | None = None
    high: str | None = None
    low: str | None = None
    change: str | None = None
    change_pct: str | None = None
    volume: str | None = None
    amount: str | None = None
    time: str | None = None


class SpotProvider:
    """全市场快照：报价与搜索共用，一次网络拉取服务全部标的。"""

    def __init__(
        self,
        market: str,
        fetch: Callable[[], pd.DataFrame],
        cols: ColumnMap,
        mem_ttl: int | None = None,
    ) -> None:
        self.market = market
        self._fetch = fetch
        self._cols = cols
        self._mem_ttl = mem_ttl if mem_ttl is not None else settings.spot_mem_ttl
        self._df: pd.DataFrame | None = None
        self._fetched_at: datetime | None = None
        self._mem_fetched_monotonic: float = 0.0
        self._last_attempt_monotonic: float = 0.0

    def snapshot(self, force: bool = False, loose: bool = False) -> tuple[pd.DataFrame, datetime, bool]:
        """返回 (快照, 拉取时间, 是否过期)。拉取失败时回退到最近可用数据。

        loose=True 为宽松模式（搜索场景）：标的清单不需要实时价，任何可用缓存直接返回，
        仅在完全无缓存时才发起网络拉取。
        网络尝试按 settings.spot_retry_interval 节流：失败后的冷却期内直接走缓存。
        """
        now = time.monotonic()
        fresh = (
            self._df is not None
            and self._fetched_at is not None
            and not force
            and now - self._mem_fetched_monotonic < self._mem_ttl
        )
        if fresh:
            return self._df, self._fetched_at, False  # type: ignore[return-value]

        in_cooldown = now - self._last_attempt_monotonic < settings.spot_retry_interval
        if loose or in_cooldown:
            # 宽松/冷却：内存缓存 → SQLite 兜底，均标记 stale（诚实展示延迟）
            if self._df is not None and self._fetched_at is not None:
                stale = (datetime.now() - self._fetched_at).total_seconds() > settings.spot_stale_after
                return self._df, self._fetched_at, stale
            cached = self._load_persisted()
            if cached is not None:
                return cached
            if in_cooldown:
                raise RuntimeError(f"{self.market} 行情快照不可用（网络失败且无缓存）")

        self._last_attempt_monotonic = now
        try:
            df = self._fetch()
            self._df = df
            self._fetched_at = datetime.now()
            self._mem_fetched_monotonic = now
            self._persist(df)
            return df, self._fetched_at, False
        except Exception:
            logger.exception("[%s] 快照拉取失败", self.market)
            # 内存兜底：稍旧但结构一致
            if self._df is not None and self._fetched_at is not None:
                stale = (datetime.now() - self._fetched_at).total_seconds() > settings.spot_stale_after
                return self._df, self._fetched_at, stale
            # SQLite 兜底：跨进程重启后仍能返回最近一次数据
            cached = self._load_persisted()
            if cached is not None:
                return cached
            raise RuntimeError(f"{self.market} 行情快照不可用（网络失败且无缓存）")

    def _persist(self, df: pd.DataFrame) -> None:
        cols = self._cols
        records = df[[c for c in (cols.code, cols.name, cols.price) if c in df.columns]].to_dict("records")
        with SessionLocal() as db:
            row = db.scalar(select(SpotCache).where(SpotCache.market == self.market))
            if row is None:
                row = SpotCache(market=self.market)
                db.add(row)
            row.payload = json.dumps(records, ensure_ascii=False, default=str)
            row.fetched_at = datetime.now()
            db.commit()

    def _load_persisted(self) -> tuple[pd.DataFrame, datetime, bool] | None:
        try:
            with SessionLocal() as db:
                row = db.scalar(select(SpotCache).where(SpotCache.market == self.market))
                if row is None:
                    return None
                df = pd.DataFrame(json.loads(row.payload))
                return df, row.fetched_at, True
        except Exception:
            logger.exception("[%s] 快照兜底缓存读取失败", self.market)
            return None


# ---------------------------------------------------------------------------
# akshare 市场实现
# ---------------------------------------------------------------------------


class AkshareSource(MarketDataSource):
    def __init__(self, provider: SpotProvider, kline_fetch: Callable[[str, str, str], pd.DataFrame]) -> None:
        self.provider = provider
        self._kline_fetch = kline_fetch  # (code, start_date, end_date) -> df
        self.market = provider.market

    # -- 搜索 ---------------------------------------------------------------

    def search(self, keyword: str, limit: int = 20) -> list[SearchResult]:
        keyword = keyword.strip()
        if not keyword:
            return []
        df, _, _ = self.provider.snapshot(loose=True)
        cols = self.provider._cols
        code_col, name_col = cols.code, cols.name
        code_hit = df[code_col].astype(str).str.contains(keyword, case=False, na=False)
        name_hit = df[name_col].astype(str).str.contains(keyword, case=False, na=False)
        # 美股代码形如 "105.MSFT"，允许直接输 MSFT 命中
        suffix_hit = df[code_col].astype(str).str.split(".").str[-1].str.contains(
            keyword, case=False, na=False
        )
        matched = df[code_hit | name_hit | suffix_hit].head(limit)
        return [
            SearchResult(market=self.market, code=str(r[code_col]), name=str(r[name_col]))
            for _, r in matched.iterrows()
        ]

    # -- 报价 ---------------------------------------------------------------

    def get_quote(self, code: str) -> Quote:
        df, fetched_at, stale = self.provider.snapshot()
        cols = self.provider._cols
        row = df[df[cols.code].astype(str) == code]
        if row.empty:
            raise LookupError(f"{self.market} 中未找到标的 {code}")
        r = row.iloc[0]

        def col(name: str | None) -> float:
            return _num(r[name]) if name and name in row.columns else 0.0

        return Quote(
            market=self.market,
            code=str(r[cols.code]),
            name=str(r[cols.name]),
            price=col(cols.price),
            prev_close=col(cols.prev_close),
            open=col(cols.open),
            high=col(cols.high),
            low=col(cols.low),
            change=col(cols.change),
            change_pct=col(cols.change_pct),
            volume=col(cols.volume),
            amount=col(cols.amount),
            time=fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
            stale=stale,
        )

    # -- K 线 ----------------------------------------------------------------

    def get_kline(self, code: str, days: int = 250) -> tuple[list[Bar], bool]:
        cached = self._read_cache(code, days)
        # 缓存视为可直接使用：日期新鲜且根数基本覆盖请求区间
        # （否则短缓存（如冷启动只拉过 30 天）会让"3年最大回撤"只覆盖到 2.5 个月）
        if cached and self._cache_is_fresh(code) and len(cached) >= days * 0.95:
            return cached, False

        start = (date.today() - timedelta(days=int(days * 1.6) + 30)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        try:
            df = self._kline_fetch(code, start, end)
            bars = self._df_to_bars(df)
            if bars:
                self._upsert_cache(code, bars)
                return self._read_cache(code, days) or bars, False
            return (cached, True) if cached else ([], False)
        except Exception:
            logger.exception("[%s/%s] K线拉取失败", self.market, code)
            if cached:
                return cached, True
            raise RuntimeError(f"{self.market}/{code} K线不可用（网络失败且无缓存）")

    def _df_to_bars(self, df: pd.DataFrame) -> list[Bar]:
        date_col = next((c for c in df.columns if "日期" in str(c)), None)
        if date_col is None:
            return []
        bars: list[Bar] = []
        prev_close = 0.0
        for _, r in df.iterrows():
            d = str(r[date_col])[:10]
            o, c = _num(r.get("开盘", 0)), _num(r.get("收盘", 0))
            h = _num(r.get("最高", c))
            low = _num(r.get("最低", c))
            pct = _num(r.get("涨跌幅", 0.0))
            if pct == 0.0 and prev_close:
                pct = (c - prev_close) / prev_close * 100
            bars.append(
                Bar(date=d, open=o or c, high=h or c, low=low or c, close=c,
                    volume=_num(r.get("成交量", 0)), change_pct=pct)
            )
            prev_close = c
        return bars

    def _read_cache(self, code: str, days: int) -> list[Bar] | None:
        with SessionLocal() as db:
            rows = (
                db.scalars(
                    select(KlineCache)
                    .where(KlineCache.market == self.market, KlineCache.code == code)
                    .order_by(KlineCache.date.desc())
                    .limit(days)
                )
                .all()
            )
        if not rows:
            return None
        return [
            Bar(date=r.date, open=r.open, high=r.high, low=r.low, close=r.close,
                volume=r.volume, change_pct=r.change_pct)
            for r in reversed(rows)
        ]

    def _cache_is_fresh(self, code: str) -> bool:
        """缓存最新日期不早于 N 天前视为新鲜（覆盖节假日与盘中未收盘情形）。"""
        with SessionLocal() as db:
            latest = db.scalar(
                select(KlineCache.date)
                .where(KlineCache.market == self.market, KlineCache.code == code)
                .order_by(KlineCache.date.desc())
                .limit(1)
            )
        if not latest:
            return False
        return (date.today() - date.fromisoformat(latest)).days <= settings.kline_fresh_window_days

    def _upsert_cache(self, code: str, bars: list[Bar]) -> None:
        with SessionLocal() as db:
            for b in bars:
                row = db.scalar(
                    select(KlineCache).where(
                        KlineCache.market == self.market,
                        KlineCache.code == code,
                        KlineCache.date == b.date,
                    )
                )
                if row is None:
                    db.add(
                        KlineCache(
                            market=self.market, code=code, date=b.date,
                            open=b.open, high=b.high, low=b.low, close=b.close,
                            volume=b.volume, change_pct=b.change_pct,
                        )
                    )
                else:
                    row.open, row.high, row.low, row.close = b.open, b.high, b.low, b.close
                    row.volume, row.change_pct = b.volume, b.change_pct
            db.commit()


# ---------------------------------------------------------------------------
# 各市场的 akshare 接口绑定
# ---------------------------------------------------------------------------


def _a_stock_kline(code: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")


def _etf_kline(code: str, start: str, end: str) -> pd.DataFrame:
    return ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")


def _hk_kline(code: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_hk_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")


def _us_kline(code: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_us_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")


def _fund_nav(code: str, start: str, end: str) -> pd.DataFrame:
    """场外基金净值走势 → 统一为 K 线结构（open=close=high=low=单位净值）。"""
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    date_col = next((c for c in df.columns if "日期" in str(c)), None)
    out = pd.DataFrame(
        {
            "净值日期": df[date_col].astype(str).str[:10] if date_col is not None else [],
            "开盘": df["单位净值"],
            "收盘": df["单位净值"],
            "最高": df["单位净值"],
            "最低": df["单位净值"],
            "涨跌幅": df.get("日增长率", 0),
        }
    )
    return out


A_STOCK = AkshareSource(
    SpotProvider(
        "a_stock",
        lambda: ak.stock_zh_a_spot_em(),
        ColumnMap(
            code="代码", name="名称", price="最新价", prev_close="昨收", open="今开",
            high="最高", low="最低", change="涨跌额", change_pct="涨跌幅",
            volume="成交量", amount="成交额",
        ),
    ),
    _a_stock_kline,
)

ETF = AkshareSource(
    SpotProvider(
        "etf",
        lambda: ak.fund_etf_spot_em(),
        ColumnMap(
            code="代码", name="名称", price="最新价", prev_close="昨收", open="开盘价",
            high="最高价", low="最低价", change="涨跌额", change_pct="涨跌幅",
            volume="成交量", amount="成交额",
        ),
    ),
    _etf_kline,
)

FUND = AkshareSource(
    SpotProvider(
        "fund",
        lambda: ak.fund_name_em(),
        ColumnMap(
            code="基金代码", name="基金简称", price="单位净值",
            change_pct="日增长率", time="日期",
        ),
        mem_ttl=24 * 3600,  # 场外基金一日一净值，快照缓存一天足够
    ),
    _fund_nav,
)

HK = AkshareSource(
    SpotProvider(
        "hk",
        lambda: ak.stock_hk_spot_em(),
        ColumnMap(
            code="代码", name="名称", price="最新价", prev_close="昨收", open="今开",
            high="最高", low="最低", change="涨跌额", change_pct="涨跌幅",
            volume="成交量", amount="成交额",
        ),
    ),
    _hk_kline,
)

US = AkshareSource(
    SpotProvider(
        "us",
        lambda: ak.stock_us_spot_em(),
        ColumnMap(
            code="代码", name="名称", price="最新价", prev_close="昨收", open="今开",
            high="最高", low="最低", change="涨跌额", change_pct="涨跌幅",
            volume="成交量", amount="成交额",
        ),
        mem_ttl=300,  # 美股全市场快照体积大，放宽内存 TTL
    ),
    _us_kline,
)
