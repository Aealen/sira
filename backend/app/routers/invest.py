# -*- coding: utf-8 -*-
"""定投与复盘 API：计划 CRUD、立即执行、复盘统计。

实时价获取为阻塞网络调用，统一 asyncio.to_thread 下并发预取，失败时
统计退回最后成交价（assemble_stats 内处理），不阻塞、不报错。
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing_extensions import Literal

from app.db import get_db
from app.models_invest import InvestExecution, InvestPlan
from app.services.invest import executor

router = APIRouter(prefix="/api/invest", tags=["invest"])

Frequency = Literal["monthly", "biweekly", "weekly"]
TakeProfitMode = Literal["none", "target_return", "valuation"]
PlanStatus = Literal["active", "paused"]


# -- 请求/响应模型 --------------------------------------------------------------
class PlanIn(BaseModel):
    market: str = Field(min_length=1, max_length=16)
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    frequency: Frequency
    day_of_month: int = Field(ge=1, le=28)
    amount: float = Field(gt=0)
    smart_dca: bool = False
    take_profit_mode: TakeProfitMode = "none"
    take_profit_value: float | None = None

    @model_validator(mode="after")
    def _check_take_profit(self) -> "PlanIn":
        if self.take_profit_mode != "none":
            if self.take_profit_value is None or self.take_profit_value <= 0:
                raise ValueError("take_profit_value 必须为正数（收益率% 或估值分位）")
            if self.take_profit_mode == "valuation" and self.take_profit_value > 100:
                raise ValueError("估值分位取值 0-100")
        else:
            self.take_profit_value = None
        return self


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    frequency: Frequency | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    amount: float | None = Field(default=None, gt=0)
    smart_dca: bool | None = None
    take_profit_mode: TakeProfitMode | None = None
    take_profit_value: float | None = None
    status: PlanStatus | None = None

    @model_validator(mode="after")
    def _check_take_profit(self) -> "PlanPatch":
        if self.take_profit_mode == "none":
            self.take_profit_value = None
        elif self.take_profit_mode is not None:
            if self.take_profit_value is None or self.take_profit_value <= 0:
                raise ValueError("take_profit_value 必须为正数（收益率% 或估值分位）")
            if self.take_profit_mode == "valuation" and self.take_profit_value > 100:
                raise ValueError("估值分位取值 0-100")
        return self


class StatsOut(BaseModel):
    invested: float
    executions: int
    market_value: float
    pnl_pct: float


class PlanOut(BaseModel):
    id: int
    market: str
    code: str
    name: str
    frequency: str
    day_of_month: int
    amount: float
    smart_dca: bool
    take_profit_mode: str
    take_profit_value: float | None
    status: str
    last_exec_date: str | None
    created_at: str
    stats: StatsOut


class ExecutionOut(BaseModel):
    id: int
    plan_id: int
    exec_date: str
    price: float
    quantity: float
    amount: float
    fee: float


class PlanDetailOut(PlanOut):
    executions: list[ExecutionOut]


def _to_plan_out(plan: InvestPlan, stats: dict) -> PlanOut:
    return PlanOut(
        id=plan.id,
        market=plan.market,
        code=plan.code,
        name=plan.name,
        frequency=plan.frequency,
        day_of_month=plan.day_of_month,
        amount=plan.amount,
        smart_dca=plan.smart_dca,
        take_profit_mode=plan.take_profit_mode,
        take_profit_value=plan.take_profit_value,
        status=plan.status,
        last_exec_date=plan.last_exec_date,
        created_at=plan.created_at.isoformat(sep=" ", timespec="seconds") if plan.created_at else "",
        stats=StatsOut(**stats),
    )


async def _fetch_price(market: str, code: str) -> float | None:
    """并发预取实时价；失败返回 None（统计退回最后成交价）。"""
    try:
        return await asyncio.to_thread(executor.get_current_price, market, code)
    except Exception:
        return None


# -- 计划 CRUD -----------------------------------------------------------------
@router.get("/plans", response_model=list[PlanOut])
async def list_plans(db: Session = Depends(get_db)) -> list[PlanOut]:
    plans = db.scalars(select(InvestPlan).order_by(InvestPlan.id)).all()
    snapshots = {p.id: executor.plan_snapshot(db, p) for p in plans}
    prices = await asyncio.gather(*(_fetch_price(p.market, p.code) for p in plans))
    return [
        _to_plan_out(p, executor.assemble_stats(snapshots[p.id], price))
        for p, price in zip(plans, prices, strict=True)
    ]


@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(payload: PlanIn, db: Session = Depends(get_db)) -> PlanOut:
    plan = InvestPlan(
        market=payload.market,
        code=payload.code,
        name=payload.name,
        frequency=payload.frequency,
        day_of_month=payload.day_of_month,
        amount=payload.amount,
        smart_dca=payload.smart_dca,
        take_profit_mode=payload.take_profit_mode,
        take_profit_value=payload.take_profit_value,
        status="active",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _to_plan_out(plan, executor.assemble_stats(executor.plan_snapshot(db, plan), None))


def _get_plan_or_404(db: Session, plan_id: int) -> InvestPlan:
    plan = db.get(InvestPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"定投计划不存在: {plan_id}")
    return plan


@router.get("/plans/{plan_id}", response_model=PlanDetailOut)
async def get_plan(plan_id: int, db: Session = Depends(get_db)) -> PlanDetailOut:
    plan = _get_plan_or_404(db, plan_id)
    snapshot = executor.plan_snapshot(db, plan)
    price = await _fetch_price(plan.market, plan.code)
    execs = db.scalars(
        select(InvestExecution)
        .where(InvestExecution.plan_id == plan.id)
        .order_by(InvestExecution.exec_date.desc(), InvestExecution.id.desc())
    ).all()
    return PlanDetailOut(
        **_to_plan_out(plan, executor.assemble_stats(snapshot, price)).model_dump(),
        executions=[
            ExecutionOut(
                id=e.id, plan_id=e.plan_id, exec_date=e.exec_date, price=e.price,
                quantity=e.quantity, amount=e.amount, fee=e.fee,
            )
            for e in execs
        ],
    )


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def patch_plan(plan_id: int, payload: PlanPatch, db: Session = Depends(get_db)) -> PlanOut:
    plan = _get_plan_or_404(db, plan_id)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="未提供任何修改字段")
    for field, value in data.items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return _to_plan_out(plan, executor.assemble_stats(executor.plan_snapshot(db, plan), None))


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, db: Session = Depends(get_db)) -> None:
    plan = _get_plan_or_404(db, plan_id)
    db.query(InvestExecution).filter(InvestExecution.plan_id == plan.id).delete()
    db.delete(plan)
    db.commit()


# -- 执行 ----------------------------------------------------------------------
@router.post("/plans/{plan_id}/execute-now")
async def execute_now(plan_id: int, db: Session = Depends(get_db)) -> dict:
    """立即执行一期（调试/手动补扣）。不判断到期与状态；失败返回 ok=False 不抛 5xx。"""
    plan = _get_plan_or_404(db, plan_id)
    result = await asyncio.to_thread(executor.execute_plan, db, plan)
    return {
        "ok": result.ok,
        "plan_id": result.plan_id,
        "message": result.message,
        "execution_id": result.execution_id,
        "price": result.price,
        "quantity": result.quantity,
        "amount": result.amount,
        "fee": result.fee,
    }


# -- 复盘 ----------------------------------------------------------------------
@router.get("/review")
async def review(db: Session = Depends(get_db)) -> dict:
    plans = db.scalars(select(InvestPlan).order_by(InvestPlan.id)).all()
    prices = await asyncio.gather(*(_fetch_price(p.market, p.code) for p in plans))
    price_map = {(p.market, p.code): v for p, v in zip(plans, prices, strict=True)}

    def price_lookup(market: str, code: str) -> float | None:
        return price_map.get((market, code))

    return executor.build_review(db, price_lookup)
