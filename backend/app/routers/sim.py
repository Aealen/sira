# -*- coding: utf-8 -*-
"""模拟盘 API：账户总览（惰性撮合挂单）/ 下单 / 撤单 / 成交流水 / 重新开局 / 市场规则表。"""
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base, get_db
from app.market_rules import MARKET_RULES
from app.models_sim import SimAccount, SimInstrument, SimOrder, SimPosition, SimTrade
from app.services.matching import (
    OrderRejected,
    cancel_order,
    check_open_orders,
    current_quote,
    get_account,
    place_order,
    reset_account,
)

router = APIRouter(prefix="/api/sim", tags=["sim"])

_SIM_TABLES = (
    SimAccount.__table__, SimPosition.__table__, SimOrder.__table__,
    SimTrade.__table__, SimInstrument.__table__,
)


def _ensure_tables(db: Session) -> None:
    """惰性建模拟盘表（幂等）。M3 不改 app/db.py 的 init_db，首个请求时按当次会话的库补建。"""
    Base.metadata.create_all(db.get_bind(), tables=_SIM_TABLES)


# -- 出入参模型 ------------------------------------------------------------


class OrderIn(BaseModel):
    market: str
    code: str
    side: str                    # buy/sell
    order_type: str              # market/limit
    price: float | None = None   # 限价委托价
    quantity: int


class OrderOut(BaseModel):
    id: int
    market: str
    code: str
    name: str
    side: str
    order_type: str
    price: float | None
    quantity: int
    status: str
    filled_price: float | None
    filled_at: datetime | None
    reject_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeOut(BaseModel):
    id: int
    order_id: int
    market: str
    code: str
    name: str
    side: str
    price: float
    quantity: int
    amount: float
    commission: float
    stamp_tax: float
    subscription_fee: float
    redemption_fee: float
    total_fee: float
    created_at: datetime

    model_config = {"from_attributes": True}


class OrderResultOut(BaseModel):
    status: str                # filled=已成交 / open=已挂单
    order: OrderOut
    trade: TradeOut | None     # 成交回执（含费用明细）；挂单为 null


class PositionOut(BaseModel):
    market: str
    code: str
    name: str
    quantity: int
    available_qty: int
    avg_cost: float
    price: float               # 现价（场外基金为最新净值；行情失败时降级为 avg_cost）
    market_value: float
    unrealized_pnl: float      # 市值 - 含费成本
    unrealized_pnl_pct: float  # 百分比
    stale: bool = False        # 行情降级标记


class AccountOut(BaseModel):
    cash: float
    positions: list[PositionOut]
    open_orders: list[OrderOut]
    recent_trades: list[TradeOut]   # 最近 10 笔
    total_asset: float
    total_pnl: float                # total_asset - 初始资金


class ResetIn(BaseModel):
    confirm: bool = False


class ResetOut(BaseModel):
    cash: float
    reset_at: datetime


class RuleOut(BaseModel):
    market: str
    label: str
    t_plus: int
    price_limit_pct: float | None
    min_lot: int | None
    trade_sessions: list[str]
    support_limit: bool
    stamp_tax_on_sell: bool
    always_open: bool


class RulesOut(BaseModel):
    initial_cash: float
    fees: dict[str, float]
    markets: dict[str, RuleOut]


# -- 路由 ------------------------------------------------------------------


@router.get("/account", response_model=AccountOut)
def get_sim_account(db: Session = Depends(get_db)) -> AccountOut:
    """账户总览：查询时惰性触发挂单撮合（M3 无后台调度）。"""
    _ensure_tables(db)
    check_open_orders(db)

    acc = get_account(db)
    name_map = {
        (i.market, i.code): i.name
        for i in db.scalars(select(SimInstrument)).all()
    }
    positions: list[PositionOut] = []
    market_value_sum = 0.0
    for p in db.scalars(select(SimPosition).order_by(SimPosition.id)).all():
        price, stale = p.avg_cost, True  # 行情失败降级：用持仓均价估值并标记 stale
        try:
            q = current_quote(db, p.market, p.code)
            if q.price > 0:
                price, stale = q.price, q.stale
        except Exception:
            pass
        mv = round(price * p.quantity, 2)
        cost = round(p.avg_cost * p.quantity, 2)
        pnl = round(mv - cost, 2)
        positions.append(PositionOut(
            market=p.market, code=p.code, name=name_map.get((p.market, p.code), p.code),
            quantity=p.quantity, available_qty=p.available_qty, avg_cost=p.avg_cost,
            price=price, market_value=mv, unrealized_pnl=pnl,
            unrealized_pnl_pct=round(pnl / cost * 100, 2) if cost > 0 else 0.0,
            stale=stale,
        ))
        market_value_sum += mv

    open_orders = [
        OrderOut.model_validate(o)
        for o in db.scalars(select(SimOrder).where(SimOrder.status == "open").order_by(SimOrder.id)).all()
    ]
    recent_trades = [
        TradeOut.model_validate(t)
        for t in db.scalars(select(SimTrade).order_by(SimTrade.id.desc()).limit(10)).all()
    ]
    total_asset = round(acc.cash + market_value_sum, 2)
    return AccountOut(
        cash=acc.cash, positions=positions, open_orders=open_orders,
        recent_trades=recent_trades, total_asset=total_asset,
        total_pnl=round(total_asset - settings.initial_cash, 2),
    )


@router.post("/order", response_model=OrderResultOut, status_code=201)
def submit_order(body: OrderIn, db: Session = Depends(get_db)) -> OrderResultOut:
    """下单：市价单即时成交回执（含费用明细）或限价挂单确认；拒单 422（detail 为中文原因）。"""
    _ensure_tables(db)
    check_open_orders(db)  # 下单前顺带撮合，保证快照一致
    try:
        result = place_order(
            db, body.market, body.code, body.side, body.order_type,
            body.quantity, body.price,
        )
    except OrderRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return OrderResultOut(
        status=result.order.status,
        order=OrderOut.model_validate(result.order),
        trade=TradeOut.model_validate(result.trade) if result.trade is not None else None,
    )


@router.delete("/order/{order_id}", status_code=204)
def cancel(order_id: int, db: Session = Depends(get_db)) -> None:
    """撤单：仅 open 挂单可撤；不存在 404，状态不可撤 409。"""
    _ensure_tables(db)
    try:
        cancel_order(db, order_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except OrderRejected as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[TradeOut]:
    """成交流水（倒序，默认 50 条）。"""
    _ensure_tables(db)
    rows = db.scalars(select(SimTrade).order_by(SimTrade.id.desc()).limit(limit)).all()
    return [TradeOut.model_validate(t) for t in rows]


@router.post("/reset", response_model=ResetOut)
def reset(body: ResetIn, db: Session = Depends(get_db)) -> ResetOut:
    """重新开局：清空全部持仓/委托/流水，现金恢复初始资金（不可恢复，需 confirm=true）。"""
    _ensure_tables(db)
    if body.confirm is not True:
        raise HTTPException(
            status_code=422,
            detail="重新开局将清空全部持仓/委托/流水且不可恢复，需传 confirm=true 确认",
        )
    acc = reset_account(db)
    return ResetOut(cash=acc.cash, reset_at=acc.reset_at or acc.created_at)


@router.get("/rules", response_model=RulesOut)
def rules() -> RulesOut:
    """五市场规则表 + 模拟盘费率（下单面板规则徽章与费用说明数据源）。"""
    return RulesOut(
        initial_cash=settings.initial_cash,
        fees={
            "commission_rate": settings.commission_rate,
            "commission_min": settings.commission_min,
            "stamp_tax_rate": settings.stamp_tax_rate,
            "fund_subscription_rate": settings.fund_subscription_rate,
            "fund_redemption_rate": settings.fund_redemption_rate,
        },
        markets={
            m: RuleOut(**asdict(r)) for m, r in MARKET_RULES.items()
        },
    )
