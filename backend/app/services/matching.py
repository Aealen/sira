# -*- coding: utf-8 -*-
"""模拟盘撮合引擎（M3）：简化撮合 + 五市场规则拒单 + 费用计算。

撮合边界（见 docs/04-工具与实现/模拟盘撮合规则.md 第三节）：
- 市价单以提交时刻实时快照价直接成交（无排队/滑点模拟）；
- 限价单挂 open 单等待，快照价触及限价即成交（按委托价成交，惰性触发，
  挂在 GET account / 下单前调用 check_open_orders，无后台调度）；
- 场外基金以最新可得单位净值成交（未知价法 15:00 截止简化为统一最新净值）；
- 快照延迟约 3s 如实采用，不假装实时。

所有函数纯同步 + 显式传 Session，费用率取 app/config.py 的 settings，
市场规则取 app/market_rules.py 的 MARKET_RULES。
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.market_rules import get_rule, is_in_session
from app.models_sim import SimAccount, SimInstrument, SimOrder, SimPosition, SimTrade
from app.services.datasource import get_source
from app.services.datasource.base import Quote

logger = logging.getLogger(__name__)


class OrderRejected(Exception):
    """拒单（reason 为面向用户的中文原因；order 为已落库的 rejected 委托，可能为 None）。"""

    def __init__(self, reason: str, order: SimOrder | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.order = order


@dataclass
class FeeDetail:
    """单笔成交费用明细（元）。场内=佣金(+卖出印花税)，场外基金=申购/赎回费。"""

    commission: float = 0.0
    stamp_tax: float = 0.0
    subscription_fee: float = 0.0
    redemption_fee: float = 0.0

    @property
    def total(self) -> float:
        return round(self.commission + self.stamp_tax + self.subscription_fee + self.redemption_fee, 2)


@dataclass
class PlaceResult:
    """下单结果：市价单即时成交（trade 非 None）；限价单挂 open（trade 为 None）。"""

    order: SimOrder
    trade: SimTrade | None


def _now() -> datetime:
    """撮合时钟（本地时间；独立函数便于测试注入）。"""
    return datetime.now()


def _today() -> str:
    """撮合交易日（YYYY-MM-DD；独立函数便于测试注入）。"""
    return date.today().isoformat()


# -- 账户与持仓 ------------------------------------------------------------


def get_account(db: Session) -> SimAccount:
    """取模拟账户（单行 id=1），不存在则按初始资金创建。"""
    acc = db.get(SimAccount, 1)
    if acc is None:
        acc = SimAccount(id=1, cash=settings.initial_cash)
        db.add(acc)
        db.commit()
    return acc


def get_position(db: Session, market: str, code: str) -> SimPosition | None:
    """查询单标的持仓。"""
    return db.scalar(
        select(SimPosition).where(SimPosition.market == market, SimPosition.code == code)
    )


def ensure_new_day(db: Session) -> None:
    """跨日滚动（幂等）：T+1 市场昨日及以前买入量解锁为可卖。

    简化：不维护交易日历，自然日翻页即视为新交易日；把所有持仓的
    available_qty 补齐到 quantity（T+0 市场两者本就同步，无副作用）。
    """
    acc = get_account(db)
    today = _today()
    if (acc.last_roll_date or "") >= today:
        return
    db.execute(update(SimPosition).values(available_qty=SimPosition.quantity))
    acc.last_roll_date = today
    db.commit()


# -- 行情适配 --------------------------------------------------------------


def _cached_name(db: Session, market: str, code: str) -> str | None:
    """从标的名称缓存表取名（无则 None）。"""
    row = db.scalar(
        select(SimInstrument).where(SimInstrument.market == market, SimInstrument.code == code)
    )
    return row.name if row else None


def _cache_instrument(db: Session, market: str, code: str, name: str) -> None:
    """写入/更新标的名称缓存（随外层事务提交）。"""
    row = db.scalar(
        select(SimInstrument).where(SimInstrument.market == market, SimInstrument.code == code)
    )
    if row is None:
        db.add(SimInstrument(market=market, code=code, name=name or code))
        db.flush()
    elif name and row.name != name:
        row.name = name
        db.flush()


def current_quote(db: Session, market: str, code: str) -> Quote:
    """统一取报价：场外基金无实时价，用最新单位净值（get_kline 最新 close）作 price。

    简化：未知价法「15:00 前按当日净值、之后按下一交易日净值」统一为
    最新可得净值（下一日净值未公布时无法预知）；基金名称优先走缓存，
    避免为取名拉取全市场基金名表。
    """
    src = get_source(market)
    if market == "fund":
        bars, _ = src.get_kline(code, 5)
        if not bars:
            raise RuntimeError(f"{market}/{code} 无可用净值数据")
        name = _cached_name(db, market, code)
        if name is None:
            try:
                name = src.get_quote(code).name or code
            except Exception:
                name = code
        return Quote(market=market, code=code, name=name, price=bars[-1].close)
    return src.get_quote(code)


# -- 费用 ------------------------------------------------------------------


def calc_fees(market: str, side: str, amount: float) -> FeeDetail:
    """按市场规则计算费用：场内佣金 max(最低, 额×费率) 双向 + 卖出印花税（A/HK）；
    场外基金按申购/赎回费率，不收佣金。金额均保留 2 位小数。
    """
    rule = get_rule(market)
    if market == "fund":
        if side == "buy":
            return FeeDetail(subscription_fee=round(amount * settings.fund_subscription_rate, 2))
        # 赎回费持有期阶梯待实现，暂按基准费率
        return FeeDetail(redemption_fee=round(amount * settings.fund_redemption_rate, 2))
    commission = round(max(settings.commission_min, amount * settings.commission_rate), 2)
    stamp = (
        round(amount * settings.stamp_tax_rate, 2)
        if side == "sell" and rule.stamp_tax_on_sell
        else 0.0
    )
    return FeeDetail(commission=commission, stamp_tax=stamp)


# -- 撮合核心 --------------------------------------------------------------


def _get_or_create_position(db: Session, market: str, code: str) -> SimPosition:
    """取持仓行，无则建零持仓行（买入前占位）。"""
    pos = get_position(db, market, code)
    if pos is None:
        pos = SimPosition(market=market, code=code, quantity=0, available_qty=0, avg_cost=0.0)
        db.add(pos)
        db.flush()
    return pos


def _precheck_fill(db: Session, order: SimOrder, deal_price: float) -> str | None:
    """成交前校验（资金/可卖量），通过返回 None，否则返回中文拒单原因。"""
    amount = round(deal_price * order.quantity, 2)
    fees = calc_fees(order.market, order.side, amount)
    if order.side == "buy":
        acc = get_account(db)
        need = round(amount + fees.total, 2)
        if acc.cash + 1e-9 < need:
            return f"资金不足：需 {need:.2f} 元（含费用），可用 {acc.cash:.2f} 元"
    else:
        pos = get_position(db, order.market, order.code)
        avail = pos.available_qty if pos else 0
        if avail < order.quantity:
            rule = get_rule(order.market)
            return (
                f"可卖数量不足（{rule.label} T+{rule.t_plus}）：当前可卖 {avail}，委托 {order.quantity}"
            )
    return None


def _apply_fill(db: Session, order: SimOrder, deal_price: float) -> SimTrade:
    """执行成交：更新账户现金、持仓（T+N 可卖量 / 含费加权成本），写成交流水。

    调用前提：_precheck_fill 已通过（本函数无失败分支）。
    """
    rule = get_rule(order.market)
    amount = round(deal_price * order.quantity, 2)
    fees = calc_fees(order.market, order.side, amount)
    acc = get_account(db)
    pos = _get_or_create_position(db, order.market, order.code)

    if order.side == "buy":
        acc.cash = round(acc.cash - amount - fees.total, 2)
        new_qty = pos.quantity + order.quantity
        # 加权平均成本口径：含买入费用（未实现盈亏 = 市值 - 含费成本）
        pos.avg_cost = round((pos.quantity * pos.avg_cost + amount + fees.total) / new_qty, 6)
        pos.quantity = new_qty
        if rule.t_plus == 0:
            pos.available_qty += order.quantity  # T+0 回转：当日即可卖
        # T+1 市场：买入量当日不计入可卖，跨日由 ensure_new_day 解锁
    else:
        acc.cash = round(acc.cash + amount - fees.total, 2)
        pos.quantity -= order.quantity
        pos.available_qty -= order.quantity
        if pos.quantity <= 0:
            db.delete(pos)  # 清仓后删除持仓行，避免残留零持仓

    order.status = "filled"
    order.filled_price = deal_price
    order.filled_at = datetime.now()
    trade = SimTrade(
        order_id=order.id, market=order.market, code=order.code, name=order.name,
        side=order.side, price=deal_price, quantity=order.quantity, amount=amount,
        commission=fees.commission, stamp_tax=fees.stamp_tax,
        subscription_fee=fees.subscription_fee, redemption_fee=fees.redemption_fee,
        total_fee=fees.total,
    )
    db.add(trade)
    db.flush()
    return trade


def place_order(
    db: Session,
    market: str,
    code: str,
    side: str,
    order_type: str,
    quantity: int,
    price: float | None = None,
) -> PlaceResult:
    """提交委托：市价单以快照价即时成交；限价单挂 open 等待触及。

    拒单规则（落库 rejected 单并抛 OrderRejected，reason 为中文）：
    非交易时段 / 行情不可用 / 零股买入 / 委托价超涨跌幅 / 可卖数量不足（T+1）/
    资金不足 / 场外基金不支持限价。参数级错误（市场/方向/类型非法）不落库直接抛。
    """
    try:
        rule = get_rule(market)
    except KeyError as e:
        raise OrderRejected(str(e)) from e
    if side not in ("buy", "sell"):
        raise OrderRejected(f"无效的交易方向: {side}（应为 buy/sell）")
    if order_type not in ("market", "limit"):
        raise OrderRejected(f"无效的委托类型: {order_type}（应为 market/limit）")
    if quantity <= 0:
        raise OrderRejected("委托数量必须大于 0")
    if order_type == "limit" and (price is None or price <= 0):
        raise OrderRejected("限价单必须指定大于 0 的委托价")

    ensure_new_day(db)
    order = SimOrder(
        market=market, code=code, side=side, order_type=order_type,
        price=price if order_type == "limit" else None,
        quantity=quantity, status="open",
    )
    db.add(order)
    db.flush()

    def reject(reason: str) -> OrderRejected:
        """拒单落库并返回待抛异常（复用当前 order 行，保留拒单痕迹）。"""
        order.status = "rejected"
        order.reject_reason = reason
        db.commit()
        return OrderRejected(reason, order)

    # 1. 委托类型能力校验
    if order_type == "limit" and not rule.support_limit:
        raise reject(f"{rule.label} 不支持限价单（场外基金按净值申赎）")
    # 2. 交易时段（9:15 前预埋单简化为直接拒单，见设计文档第三节）
    if not is_in_session(market, _now()):
        raise reject(
            f"非交易时段（{rule.label} 时段：{'、'.join(rule.trade_sessions)}），盘前预埋单暂未支持"
        )
    # 3. 零股买入（零股仅可卖）
    if side == "buy" and rule.min_lot is not None and quantity % rule.min_lot != 0:
        raise reject(f"{rule.label} 买入数量须为 {rule.min_lot} 的整数倍（零股仅可卖出）")
    # 4. 行情（名称缓存随之写入）
    try:
        quote = current_quote(db, market, code)
    except Exception as e:
        raise reject(f"行情不可用：{e}")
    if quote.price <= 0:
        raise reject("无有效报价（价格为 0），暂不能交易")
    order.name = quote.name or code
    _cache_instrument(db, market, code, order.name)
    # 5. 涨跌幅（限价委托价 vs 昨收；市价单以现价成交天然在界内）
    if order_type == "limit" and rule.price_limit_pct is not None and quote.prev_close > 0:
        upper = round(quote.prev_close * (1 + rule.price_limit_pct), 2)
        lower = round(quote.prev_close * (1 - rule.price_limit_pct), 2)
        if price > upper + 1e-9 or price < lower - 1e-9:
            raise reject(
                f"委托价 {price:.2f} 超出涨跌幅限制 [{lower:.2f}, {upper:.2f}]（昨收 {quote.prev_close:.2f}）"
            )
    # 6. 可卖数量（T+1；市价/限价卖单都先查，避免挂注定无法成交的卖单）
    if side == "sell":
        pos = get_position(db, market, code)
        avail = pos.available_qty if pos else 0
        if avail < quantity:
            raise reject(
                f"可卖数量不足（{rule.label} T+{rule.t_plus}）：当前可卖 {avail}，委托 {quantity}"
            )

    if order_type == "market":
        # 市价单：快照价直接成交（先校验资金，不足则拒单落库）
        reason = _precheck_fill(db, order, quote.price)
        if reason is not None:
            raise reject(reason)
        trade = _apply_fill(db, order, quote.price)
        db.commit()
        return PlaceResult(order=order, trade=trade)

    # 限价单：挂单不冻结资金/份额，成交时再校验（见 check_open_orders）
    db.commit()
    return PlaceResult(order=order, trade=None)


def check_open_orders(db: Session) -> list[SimTrade]:
    """遍历挂单：快照价触及限价则按委托价成交（惰性触发，无后台调度）。

    行情获取失败的单跳过（不影响其他挂单）；成交前重校验资金/可卖量，
    不通过的单转 rejected（如挂单期间资金已被占用）。非交易时段不撮合
    排除——快照在闭市时为收盘价，触及限价即成交同样合理。
    """
    ensure_new_day(db)
    trades: list[SimTrade] = []
    orders = (
        db.scalars(select(SimOrder).where(SimOrder.status == "open").order_by(SimOrder.id))
        .all()
    )
    for order in orders:
        try:
            quote = current_quote(db, order.market, order.code)
        except Exception as e:
            logger.warning("挂单 %s 行情获取失败，跳过: %s", order.id, e)
            continue
        if quote.price <= 0:
            continue
        touched = (
            quote.price <= order.price if order.side == "buy" else quote.price >= order.price
        )
        if not touched:
            continue
        reason = _precheck_fill(db, order, order.price)
        if reason is not None:
            order.status = "rejected"
            order.reject_reason = reason
        else:
            trades.append(_apply_fill(db, order, order.price))
    db.commit()
    return trades


def cancel_order(db: Session, order_id: int) -> SimOrder:
    """撤销挂单：不存在抛 LookupError（路由转 404）；非 open 状态抛 OrderRejected（转 409）。"""
    order = db.get(SimOrder, order_id)
    if order is None:
        raise LookupError(f"委托单不存在: {order_id}")
    if order.status != "open":
        raise OrderRejected(f"委托单状态为 {order.status}，仅挂单（open）可撤销", order)
    order.status = "cancelled"
    db.commit()
    return order


def reset_account(db: Session) -> SimAccount:
    """重新开局：清空持仓/委托/流水，现金恢复初始资金（原型警示：不可恢复）。"""
    db.execute(delete(SimTrade))
    db.execute(delete(SimOrder))
    db.execute(delete(SimPosition))
    acc = get_account(db)
    acc.cash = settings.initial_cash
    acc.reset_at = datetime.now()
    acc.last_roll_date = _today()
    db.commit()
    return acc
