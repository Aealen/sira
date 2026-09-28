# -*- coding: utf-8 -*-
"""定投执行器：到期判断、按期买入、smart_dca 智能加减速、每日调度、复盘统计。

依赖说明：
- 撮合：app.services.matching.place_order（并行任务开发，惰性导入；签名以实际文件为准，
  适配点集中在 _place_buy / _extract_trade 两处）。
- 估值分位：app.services.valuation（并行任务开发，惰性导入，缺失时 smart_dca 退化为基准金额）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_invest import InvestExecution, InvestPlan
from app.services.datasource import get_source

logger = logging.getLogger(__name__)

# -- 常量（smart_dca 与纪律检查阈值） ------------------------------------------
FUND_MARKET = "fund"        # 场外基金：无实时价，以最新净值成交
VALUATION_LOW = 30.0        # 估值分位低于该值 → 加码 ×1.5
VALUATION_HIGH = 70.0       # 估值分位高于该值 → 减码 ×0.5
SMART_MULT_LOW = 1.5
SMART_MULT_HIGH = 0.5
PAUSED_STALE_DAYS = 30      # 暂停计划距最后执行超过该天数视为“长期暂停”

WEEKLY_DAYS = {"weekly": 7, "biweekly": 14}


@dataclass
class ExecResult:
    """单次执行结果（失败不抛异常，调用方按 ok 字段处理）。"""

    ok: bool
    plan_id: int
    message: str = ""
    execution_id: int | None = None
    price: float = 0.0
    quantity: float = 0.0
    amount: float = 0.0
    fee: float = 0.0


# -- 工具 ----------------------------------------------------------------------
def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()  # noqa: DTZ007（纯日期字符串，无时区语义）


# -- 到期判断 ------------------------------------------------------------------
def is_due(plan: InvestPlan, today: date) -> bool:
    """计划在 today 是否应执行一期。

    - monthly：今天是当月 day_of_month 或之后，且本期（当月）尚未执行。
    - weekly/biweekly：距上次执行满 7/14 天；从未执行过（新计划）视为到期。
    - 暂停中不执行。失败的一期不更新 last_exec_date，会在后续调度日自动重试。
    """
    if plan.status != "active":
        return False
    last = _parse_date(plan.last_exec_date)
    if plan.frequency == "monthly":
        due_day = min(max(int(plan.day_of_month), 1), 28)  # 字段约束 1-28，防御旧数据
        due_date = date(today.year, today.month, due_day)
        if today < due_date:
            return False
        return last is None or last < due_date
    gap = WEEKLY_DAYS.get(plan.frequency)
    if gap is None:
        logger.warning("[invest] 未知定投频率 %s（%s/%s），跳过", plan.frequency, plan.market, plan.code)
        return False
    if last is None:
        return True
    return (today - last).days >= gap


# -- 价格 ----------------------------------------------------------------------
def get_current_price(market: str, code: str) -> float:
    """定投用成交价：场外基金取最新单位净值，其余取实时价。失败抛异常。"""
    src = get_source(market)
    if market == FUND_MARKET:
        bars, _stale = src.get_kline(code, days=30)
        if not bars:
            raise LookupError(f"{market}/{code} 无可用净值数据")
        return float(bars[-1].close)
    quote = src.get_quote(code)
    return float(quote.price)


def valuation_percentile(market: str, code: str) -> float | None:
    """估值分位（0-100）。app.services.valuation 仅支持 a_stock 个股，
    不支持/不可用/失败一律返回 None（smart_dca 降级为基准金额）。"""
    try:
        from app.services.valuation import get_valuation  # noqa: PLC0415  惰性导入
    except Exception:
        return None
    try:
        snapshot = get_valuation(market, code)
    except Exception:
        logger.debug("[invest] 估值分位获取失败 %s/%s", market, code, exc_info=True)
        return None
    if snapshot is None:
        return None
    pct = snapshot.pe_percentile if snapshot.pe_percentile is not None else snapshot.pb_percentile
    return float(pct) if pct is not None else None


def smart_amount(plan: InvestPlan) -> float:
    """smart_dca 智能加减速：<30 分位 ×1.5，>70 分位 ×0.5，取不到分位用基准金额。"""
    if not plan.smart_dca:
        return float(plan.amount)
    pct = valuation_percentile(plan.market, plan.code)
    if pct is None:
        return float(plan.amount)
    if pct < VALUATION_LOW:
        return round(float(plan.amount) * SMART_MULT_LOW, 2)
    if pct > VALUATION_HIGH:
        return round(float(plan.amount) * SMART_MULT_HIGH, 2)
    return float(plan.amount)


# -- 撮合适配（app.services.matching 实际接口） ---------------------------------
def _place_buy(db: Session, market: str, code: str, price: float, amount: float):
    """按金额市价买入。

    matching.place_order 按「数量」受理（quantity: int），定投金额驱动，
    此处换算：预留估算费用后按市场最小买入单位（min_lot）向下取整。
    拒单（含资金不足/非交易时段）抛 OrderRejected，由 execute_plan 统一捕获。
    """
    from app.config import settings  # noqa: PLC0415
    from app.market_rules import get_rule  # noqa: PLC0415
    from app.services.matching import place_order  # noqa: PLC0415

    rule = get_rule(market)
    lot = rule.min_lot or 1
    if market == FUND_MARKET:
        est_fee = round(amount * settings.fund_subscription_rate, 2)
    else:
        est_fee = round(max(settings.commission_min, amount * settings.commission_rate), 2)
    budget = max(amount - est_fee, 0.0)
    quantity = int(budget // (price * lot)) * lot
    if quantity <= 0:
        raise RuntimeError(
            f"定投金额 {amount:.2f} 元不足以下单（{rule.label}最小买入 {lot} 份 × {price:.4f}"
            f" ≈ {price * lot:.2f} 元 + 费用）"
        )
    return place_order(db, market=market, code=code, side="buy",
                       order_type="market", quantity=quantity)


def _extract_trade(result) -> tuple[float, float, float]:
    """从 matching.PlaceResult 提取 (quantity, fee, 含费投入金额)。

    SimTrade.amount 为纯成交额（价×量），现金实际扣减 = amount + total_fee，
    InvestExecution.amount 记含费口径（与投入成本统计一致）。
    """
    trade = getattr(result, "trade", None)
    if trade is None:
        raise RuntimeError("市价单未成交")
    return (
        float(trade.quantity),
        float(trade.total_fee),
        round(float(trade.amount) + float(trade.total_fee), 2),
    )


# -- 执行 ----------------------------------------------------------------------
def execute_plan(db: Session, plan: InvestPlan, today: date | None = None) -> ExecResult:
    """执行计划的一期（不判断是否到期，供调度与 execute-now 共用）。失败不抛异常。"""
    today = today or date.today()
    try:
        price = get_current_price(plan.market, plan.code)
    except Exception as exc:
        logger.warning("[invest] plan %s 行情获取失败: %s", plan.id, exc)
        return ExecResult(False, plan.id, f"行情获取失败: {exc}")

    amount = smart_amount(plan)
    try:
        trade = _place_buy(db, plan.market, plan.code, price, amount)
        quantity, fee, actual_amount = _extract_trade(trade)
    except Exception as exc:  # 资金不足 / 撮合失败 / 模块未就绪：记录后跳过
        logger.warning("[invest] plan %s 本期买入失败（跳过，后续重试）: %s", plan.id, exc)
        return ExecResult(False, plan.id, f"买入失败: {exc}")

    execution = InvestExecution(
        plan_id=plan.id,
        exec_date=today.isoformat(),
        price=float(price),
        quantity=quantity,
        amount=actual_amount if actual_amount > 0 else amount,
        fee=fee,
    )
    db.add(execution)
    plan.last_exec_date = today.isoformat()
    db.commit()
    logger.info("[invest] plan %s 执行成功: %s %s股 @%s", plan.id, today, quantity, price)
    return ExecResult(True, plan.id, "ok", execution_id=execution.id,
                      price=float(price), quantity=quantity,
                      amount=execution.amount, fee=fee)


def run_due_plans(db: Session, today: date | None = None) -> list[ExecResult]:
    """检查全部计划并执行到期者。单计划失败不影响其他计划。"""
    today = today or date.today()
    results: list[ExecResult] = []
    plans = db.scalars(select(InvestPlan).order_by(InvestPlan.id)).all()
    for plan in plans:
        if not is_due(plan, today):
            continue
        results.append(execute_plan(db, plan, today))
    return results


# -- 调度 ----------------------------------------------------------------------
def run_due_plans_job() -> list[ExecResult]:
    """调度器入口：独立开启会话执行（避免复用请求级会话）。"""
    from app.db import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        return run_due_plans(db)
    finally:
        db.close()


def init_scheduler():
    """构建每日 09:35（Asia/Shanghai）的定投调度器。只构建不启动——
    主入口接线时调用 `scheduler = init_scheduler(); scheduler.start()`。"""
    from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415
    from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_due_plans_job,
        CronTrigger(hour=9, minute=35, timezone="Asia/Shanghai"),
        id="invest_dca_daily",
        name="定投到期检查与执行",
        replace_existing=True,
    )
    return scheduler


# -- 统计与复盘 ----------------------------------------------------------------
def plan_snapshot(db: Session, plan: InvestPlan) -> dict:
    """计划执行快照（纯 DB 数据，无网络调用）。"""
    execs = db.scalars(
        select(InvestExecution)
        .where(InvestExecution.plan_id == plan.id)
        .order_by(InvestExecution.exec_date, InvestExecution.id)
    ).all()
    invested = sum(e.amount for e in execs)
    quantity = sum(e.quantity for e in execs)
    return {
        "invested": round(invested, 2),
        "executions": len(execs),
        "quantity": quantity,
        "last_price": float(execs[-1].price) if execs else 0.0,  # 行情失败时的兜底价
    }


def assemble_stats(snapshot: dict, price: float | None) -> dict:
    """结合实时价（None 时退回最后成交价）组装前端展示统计。"""
    used_price = price if price else snapshot["last_price"]
    invested = snapshot["invested"]
    market_value = snapshot["quantity"] * used_price
    pnl_pct = (market_value - invested) / invested * 100 if invested > 0 else 0.0
    return {
        "invested": invested,
        "executions": snapshot["executions"],
        "market_value": round(market_value, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


def _expected_periods(plan: InvestPlan, today: date) -> int:
    """按计划参数推算截至 today 应执行期数（用于纪律检查）。"""
    start = plan.created_at.date() if plan.created_at else today
    if plan.frequency == "monthly":
        due_day = min(max(int(plan.day_of_month), 1), 28)
        # 首期：创建当月的 due_day 若已过（含创建当天），则当月即为一期
        first_month = start if start.day <= due_day else _next_month_start(start)
        count = 0
        cursor = first_month
        while cursor.year * 12 + cursor.month <= today.year * 12 + today.month:
            due_date = date(cursor.year, cursor.month, due_day)
            if due_date <= today:
                count += 1
            cursor = _next_month_start(cursor)
        return count
    gap = WEEKLY_DAYS.get(plan.frequency, 7)
    return max(0, (today - start).days // gap + 1)


def _next_month_start(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, 1)


def build_review(db: Session, price_lookup=None) -> dict:
    """复盘统计：总投入/总市值/总收益率 + 纪律检查。

    price_lookup(market, code) -> float | None：当前价提供函数（通常由路由层
    预取实时价后传入；None 表示取不到，统计退回最后成交价）。
    """
    if price_lookup is None:
        price_lookup = lambda market, code: None  # noqa: E731

    plans = db.scalars(select(InvestPlan).order_by(InvestPlan.id)).all()
    today = date.today()

    total_invested = 0.0
    total_market_value = 0.0
    overdue: list[dict] = []
    paused_stale: list[dict] = []
    take_profit_hits: list[dict] = []
    valuation_unknown: list[str] = []

    for plan in plans:
        snapshot = plan_snapshot(db, plan)
        total_invested += snapshot["invested"]
        try:
            price = price_lookup(plan.market, plan.code)
        except Exception:
            price = None
        stats = assemble_stats(snapshot, price)
        total_market_value += stats["market_value"]

        # 检查1：active 计划是否按期执行
        if plan.status == "active":
            expected = _expected_periods(plan, today)
            missed = max(0, expected - stats["executions"])
            if missed > 0:
                overdue.append({"plan_id": plan.id, "name": plan.name, "market": plan.market,
                                "code": plan.code, "expected": expected,
                                "actual": stats["executions"], "missed": missed})

        # 检查2：长期暂停
        if plan.status == "paused":
            last = _parse_date(plan.last_exec_date)
            if last is None or (today - last).days > PAUSED_STALE_DAYS:
                paused_stale.append({"plan_id": plan.id, "name": plan.name,
                                     "last_exec_date": plan.last_exec_date})

        # 检查3：止盈线
        if plan.take_profit_mode == "target_return" and plan.take_profit_value is not None:
            if stats["invested"] > 0 and stats["pnl_pct"] >= plan.take_profit_value:
                take_profit_hits.append({"plan_id": plan.id, "name": plan.name,
                                         "mode": "target_return",
                                         "value": plan.take_profit_value,
                                         "pnl_pct": stats["pnl_pct"]})
        elif plan.take_profit_mode == "valuation" and plan.take_profit_value is not None:
            pct = valuation_percentile(plan.market, plan.code)
            if pct is None:
                valuation_unknown.append(plan.name)
            elif pct >= plan.take_profit_value:
                take_profit_hits.append({"plan_id": plan.id, "name": plan.name,
                                         "mode": "valuation",
                                         "value": plan.take_profit_value,
                                         "percentile": pct})

    total_pnl_pct = (
        (total_market_value - total_invested) / total_invested * 100 if total_invested > 0 else 0.0
    )
    if take_profit_hits:
        tp_detail: object = take_profit_hits
        if valuation_unknown:
            tp_detail = {"触发计划": take_profit_hits, "无法评估估值分位": valuation_unknown}
    else:
        tp_detail = "无计划触发止盈条件"
        if valuation_unknown:
            tp_detail = (
                f"无计划触发止盈条件；{len(valuation_unknown)} 个计划的估值分位不可用，"
                "无法评估估值止盈线"
            )
    discipline = [
        {
            "check": "全部计划按期执行",
            "passed": not overdue,
            "detail": "所有计划均按期执行" if not overdue else overdue,
        },
        {
            "check": "无长期暂停的计划",
            "passed": not paused_stale,
            "detail": (
                f"无暂停超过 {PAUSED_STALE_DAYS} 天的计划" if not paused_stale else paused_stale
            ),
        },
        {
            "check": "止盈线触发提示",
            "passed": not take_profit_hits,
            "detail": tp_detail,
        },
    ]
    return {
        "total_invested": round(total_invested, 2),
        "total_market_value": round(total_market_value, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "discipline": discipline,
    }
