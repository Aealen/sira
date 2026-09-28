# -*- coding: utf-8 -*-
"""自选管理 API：列表（含实时报价合并）/ 添加 / 移除。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Watchlist
from app.services.datasource import get_source

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchItemIn(BaseModel):
    market: str
    code: str
    name: str


class WatchItemOut(BaseModel):
    market: str
    code: str
    name: str
    price: float = 0.0
    change_pct: float = 0.0
    time: str = ""
    stale: bool = False


async def _quote_of(market: str, code: str, fallback_name: str) -> WatchItemOut:
    """单标的实时报价；行情源故障时降级为仅名称（不阻塞整个列表）。"""
    try:
        q = await asyncio.to_thread(get_source(market).get_quote, code)
        return WatchItemOut(
            market=q.market, code=q.code, name=q.name,
            price=q.price, change_pct=q.change_pct, time=q.time, stale=q.stale,
        )
    except Exception:
        return WatchItemOut(market=market, code=code, name=fallback_name, stale=True)


@router.get("", response_model=list[WatchItemOut])
async def list_watchlist(db: Session = Depends(get_db)) -> list[WatchItemOut]:
    rows = db.scalars(select(Watchlist).order_by(Watchlist.id)).all()
    items = await asyncio.gather(*(_quote_of(r.market, r.code, r.name) for r in rows))
    return list(items)


@router.post("", response_model=WatchItemOut, status_code=201)
def add_watchlist(item: WatchItemIn, db: Session = Depends(get_db)) -> WatchItemOut:
    exists = db.scalar(
        select(Watchlist).where(Watchlist.market == item.market, Watchlist.code == item.code)
    )
    if exists is None:
        db.add(Watchlist(market=item.market, code=item.code, name=item.name))
        db.commit()
    return WatchItemOut(market=item.market, code=item.code, name=item.name)


@router.delete("/{market}/{code}", status_code=204)
def remove_watchlist(market: str, code: str, db: Session = Depends(get_db)) -> None:
    row = db.scalar(
        select(Watchlist).where(Watchlist.market == market, Watchlist.code == code)
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"自选中不存在 {market}/{code}")
    db.delete(row)
    db.commit()
