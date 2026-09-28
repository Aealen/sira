# -*- coding: utf-8 -*-
"""行情 API：市场列表 / 跨市场搜索 / 实时报价 / 日K线 / 主要指数概览。"""
import asyncio
import logging
import math
import time

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import BarOut, KlineOut, QuoteOut, SearchItem
from app.services.datasource import get_source, list_markets
from app.services.datasource.base import Quote

router = APIRouter(prefix="/api/market", tags=["market"])

logger = logging.getLogger(__name__)


@router.get("/markets")
def markets() -> dict[str, str]:
    return list_markets()


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="代码或名称关键字"),
    market: str | None = Query(None, description="限定市场，缺省搜全部"),
    limit: int = Query(20, ge=1, le=50),
) -> list[SearchItem]:
    markets = [market] if market else list(list_markets())
    keyword = q.strip()

    async def search_one(m: str) -> list[SearchItem]:
        try:
            results = await asyncio.to_thread(get_source(m).search, keyword, limit)
            return [SearchItem(market=r.market, code=r.code, name=r.name) for r in results]
        except Exception:
            return []  # 单个市场数据源故障不阻塞其他市场

    grouped = await asyncio.gather(*(search_one(m) for m in markets))
    flat = [item for group in grouped for item in group]
    return flat[:limit]


@router.get("/quote/{market}/{code}", response_model=QuoteOut)
async def quote(market: str, code: str) -> QuoteOut:
    try:
        q: Quote = await asyncio.to_thread(get_source(market).get_quote, code)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"行情源不可用: {e}") from e
    return QuoteOut(**q.__dict__)


@router.get("/kline/{market}/{code}", response_model=KlineOut)
async def kline(
    market: str,
    code: str,
    days: int = Query(250, ge=10, le=3000),
) -> KlineOut:
    src = get_source(market)
    try:
        bars, stale = await asyncio.to_thread(src.get_kline, code, days)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"K线不可用: {e}") from e
    name = code
    try:
        found = await asyncio.to_thread(src.search, code, 1)
        if found:
            name = found[0].name
    except Exception:
        pass
    return KlineOut(
        market=market,
        code=code,
        name=name,
        stale=stale,
        bars=[BarOut(**b.__dict__) for b in bars],
    )


# ---------------------------------------------------------------------------
# 主要指数概览：多数据源容错 + 内存缓存 60s，失败时返回过期缓存或空列表
# ---------------------------------------------------------------------------


class IndexOut(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float


_INDEX_TTL = 60.0             # 成功结果内存缓存时长（秒）
_INDEX_FAIL_COOLDOWN = 120.0  # 单个接口失败后的熔断冷却，避免每次请求都等超时

_index_cache: list[IndexOut] = []
_index_cache_at = 0.0
_index_fail_at: dict[str, float] = {}

_CN_EXACT = ("上证指数", "深证成指", "创业板指")
_HK_EXACT = ("恒生指数",)
_GLOBAL_CONTAINS = "纳斯达克"


def _safe_float(v) -> float:
    """akshare 返回值常混有 NaN/'-'/字符串，统一安全转 float。"""
    try:
        f = float(v)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def _call_source(key: str, fn, *args) -> pd.DataFrame | None:
    """带熔断的接口调用：失败后冷却期内不再尝试，返回 None 由上层回退。"""
    now = time.monotonic()
    if now - _index_fail_at.get(key, 0.0) < _INDEX_FAIL_COOLDOWN:
        return None
    try:
        df = fn(*args)
        return df if df is not None and not df.empty else None
    except Exception as e:
        logger.warning("[indices] %s 拉取失败: %s", key, e)
        _index_fail_at[key] = now
        return None


def _row_to_index(r) -> IndexOut:
    return IndexOut(
        code=str(r.get("代码") or r.get("名称")),
        name=str(r.get("名称")),
        price=_safe_float(r.get("最新价")),
        change_pct=_safe_float(r.get("涨跌幅")),
    )


def _extract(
    df: pd.DataFrame | None,
    exact: tuple[str, ...] = (),
    contains: str = "",
) -> list[IndexOut]:
    """从指数快照中按名称挑出目标指数（同名取第一行，避免沪深300重复）。"""
    out: list[IndexOut] = []
    if df is None or df.empty or "名称" not in df.columns:
        return out
    names = df["名称"].astype(str)
    for label in exact:
        hit = df[names == label]
        if not hit.empty:
            out.append(_row_to_index(hit.iloc[0]))
    if contains:
        hit = df[names.str.contains(contains, na=False)]
        if not hit.empty:
            out.append(_row_to_index(hit.iloc[0]))
    return out


def _load_indices() -> list[IndexOut]:
    import akshare as ak

    out: list[IndexOut] = []
    # 沪深：东财优先，失败回退新浪（探不到的指数自动跳过）
    cn = (
        _call_source("cn_em", ak.stock_zh_index_spot_em, "沪深重要指数")
        or _call_source("cn_sina", ak.stock_zh_index_spot_sina)
    )
    out += _extract(cn, exact=_CN_EXACT)
    # 港股：恒生指数
    hk = _call_source("hk_em", ak.stock_hk_index_spot_em) or _call_source(
        "hk_sina", ak.stock_hk_index_spot_sina
    )
    out += _extract(hk, exact=_HK_EXACT)
    # 全球：纳斯达克（接口不可用时跳过）
    gl = _call_source("global_em", ak.index_global_spot_em)
    out += _extract(gl, contains=_GLOBAL_CONTAINS)
    return out


@router.get("/indices", response_model=list[IndexOut])
async def indices() -> list[IndexOut]:
    global _index_cache, _index_cache_at
    if _index_cache and time.monotonic() - _index_cache_at < _INDEX_TTL:
        return _index_cache
    try:
        data = await asyncio.to_thread(_load_indices)
    except Exception:
        logger.exception("[indices] 概览加载失败")
        data = []
    if data:
        _index_cache = data
        _index_cache_at = time.monotonic()
    return _index_cache  # 失败时返回过期缓存或空列表，不抛异常


# ---------------------------------------------------------------------------
# 当日分时（分钟级价格，用于分析页"分时"视图）
# ---------------------------------------------------------------------------

_INTRADAY_TTL = 60.0  # 秒：分时数据内存缓存
_intraday_cache: dict[tuple[str, str], tuple[float, list[dict], float | None]] = {}


class IntradayOut(BaseModel):
    prev_close: float | None = None  # 昨收（分时基准线）
    bars: list[dict] = []  # [{time: "HH:MM", price, volume}]


@router.get("/intraday/{market}/{code}", response_model=IntradayOut)
async def intraday(market: str, code: str) -> IntradayOut:
    """当日分时（仅 A股/场内ETF；其余市场与数据源失败时返回空 bars）。"""
    if market not in ("a_stock", "etf"):
        return IntradayOut()

    key = (market, code)
    cached = _intraday_cache.get(key)
    if cached and time.monotonic() - cached[0] < _INTRADAY_TTL:
        return IntradayOut(prev_close=cached[2], bars=cached[1])

    def fetch() -> tuple[list[dict], float | None]:
        import akshare as ak  # noqa: PLC0415
        from datetime import date

        # 新浪源需要交易所前缀：6/5/9 开头为沪，0/1/3 为深
        prefix = "sh" if code[0] in "569" else "sz"
        df = ak.stock_zh_a_minute(symbol=f"{prefix}{code}", period="1")
        df = df.rename(columns={"day": "datetime"})
        today = date.today().isoformat()
        is_today = df["datetime"].astype(str).str.startswith(today)
        prev_close: float | None = None
        hist = df[~is_today]
        if not hist.empty:
            prev_close = float(hist.iloc[-1]["close"])
        today_df = df[is_today]
        rows = []
        cum_amount, cum_volume = 0.0, 0.0
        for _, r in today_df.iterrows():
            vol, amt = float(r["volume"]), float(r.get("amount") or 0.0)
            cum_volume += vol
            cum_amount += amt
            rows.append({
                "time": str(r["datetime"])[11:16],
                "price": float(r["close"]),
                "avg": round(cum_amount / cum_volume, 4) if cum_volume else float(r["close"]),  # 均价线
                "volume": vol,
            })
        return rows, prev_close

    try:
        rows, prev_close = await asyncio.to_thread(fetch)
        _intraday_cache[key] = (time.monotonic(), rows, prev_close)
        return IntradayOut(prev_close=prev_close, bars=rows)
    except Exception:
        logger.warning("[%s/%s] 分时拉取失败", market, code, exc_info=True)
        if cached:
            return IntradayOut(prev_close=cached[2], bars=cached[1])
        return IntradayOut()
