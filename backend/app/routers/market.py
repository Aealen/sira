# -*- coding: utf-8 -*-
"""行情 API：市场列表 / 跨市场搜索 / 实时报价 / 日K线。"""
import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.schemas import BarOut, KlineOut, QuoteOut, SearchItem
from app.services.datasource import get_source, list_markets
from app.services.datasource.base import Quote

router = APIRouter(prefix="/api/market", tags=["market"])


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
