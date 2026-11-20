"""Consultas do dashboard: totais de saída, previsão por produto e série diária."""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import Session

from giro.forecast import WINDOW_DAYS, Forecast, compute_forecast
from giro.models import (
    MovementKind,
    OrderStatus,
    PaymentMethod,
    Product,
    PurchaseOrder,
    Sale,
    SaleItem,
    SalePayment,
    SaleStatus,
    StockMovement,
)
from giro.pos.money import line_total

YELLOW_DAYS = 7


class StockStatus(StrEnum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


@dataclass(frozen=True)
class ProductRow:
    product: Product
    sold_today: Decimal
    sold_month: Decimal
    forecast: Forecast
    status: StockStatus


@dataclass(frozen=True)
class Overview:
    rows: list[ProductRow]
    below_min: int
    sold_today: Decimal
    sold_month: Decimal
    awaiting_approval: int


def local_today(now: datetime, tz: str) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def _local_day(tz: str):  # type: ignore[no-untyped-def]
    return func.date(func.timezone(tz, StockMovement.occurred_at))


def _net_quantity() -> ColumnElement[Decimal]:
    """Saída líquida: venda soma, cancelamento de venda subtrai."""
    return func.sum(
        case(
            (StockMovement.kind == MovementKind.SALE, StockMovement.quantity),
            else_=-StockMovement.quantity,
        )
    )


_NET_KINDS = (MovementKind.SALE, MovementKind.SALE_CANCEL)


def _sales_by_day(
    s: Session, *, tz: str, since: date, product_ids: Collection[int] | None
) -> dict[int, dict[date, Decimal]]:
    day = _local_day(tz)
    start = datetime.combine(since, time.min, tzinfo=ZoneInfo(tz))
    stmt = (
        select(StockMovement.product_id, day, _net_quantity())
        .where(StockMovement.kind.in_(_NET_KINDS), StockMovement.occurred_at >= start)
        .group_by(StockMovement.product_id, day)
    )
    if product_ids is not None:
        stmt = stmt.where(StockMovement.product_id.in_(product_ids))
    result: dict[int, dict[date, Decimal]] = defaultdict(dict)
    for product_id, d, total in s.execute(stmt):
        result[product_id][d] = total
    return result


def _first_sales(s: Session, *, tz: str, product_ids: Collection[int] | None) -> dict[int, date]:
    stmt = (
        select(StockMovement.product_id, func.min(_local_day(tz)))
        .where(StockMovement.kind == MovementKind.SALE)
        .group_by(StockMovement.product_id)
    )
    if product_ids is not None:
        stmt = stmt.where(StockMovement.product_id.in_(product_ids))
    return {pid: d for pid, d in s.execute(stmt)}


def _forecast(
    product: Product, sales: dict[date, Decimal], first_sale: date | None, today: date
) -> Forecast:
    return compute_forecast(
        current_stock=product.current_stock,
        min_stock=product.min_stock,
        cover_days=product.cover_days,
        unit=product.unit,
        sales_by_day=sales,
        first_sale=first_sale,
        today=today,
    )


def forecast_for(s: Session, product: Product, *, now: datetime, tz: str) -> Forecast:
    today = local_today(now, tz)
    since = today - timedelta(days=WINDOW_DAYS - 1)
    sales = _sales_by_day(s, tz=tz, since=since, product_ids=[product.id])
    first = _first_sales(s, tz=tz, product_ids=[product.id])
    return _forecast(product, sales.get(product.id, {}), first.get(product.id), today)


def _status(product: Product, forecast: Forecast, today: date) -> StockStatus:
    if product.current_stock < product.min_stock:
        return StockStatus.RED
    if forecast.next_purchase is not None and forecast.next_purchase <= today + timedelta(
        days=YELLOW_DAYS
    ):
        return StockStatus.YELLOW
    return StockStatus.GREEN


_STATUS_ORDER = {StockStatus.RED: 0, StockStatus.YELLOW: 1, StockStatus.GREEN: 2}


def overview(s: Session, *, now: datetime, tz: str, search: str | None = None) -> Overview:
    today = local_today(now, tz)
    month_start = today.replace(day=1)
    window_start = today - timedelta(days=WINDOW_DAYS - 1)

    stmt = select(Product).where(Product.active)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern)))
    products = list(s.scalars(stmt))
    ids = [p.id for p in products]

    sales = _sales_by_day(s, tz=tz, since=min(month_start, window_start), product_ids=ids)
    firsts = _first_sales(s, tz=tz, product_ids=ids)

    rows: list[ProductRow] = []
    for product in products:
        by_day = sales.get(product.id, {})
        forecast = _forecast(product, by_day, firsts.get(product.id), today)
        rows.append(
            ProductRow(
                product=product,
                sold_today=by_day.get(today, Decimal(0)),
                sold_month=sum((q for d, q in by_day.items() if d >= month_start), Decimal(0)),
                forecast=forecast,
                status=_status(product, forecast, today),
            )
        )
    rows.sort(
        key=lambda r: (
            _STATUS_ORDER[r.status],
            r.forecast.next_purchase is None,
            r.forecast.next_purchase or today,
            r.product.name,
        )
    )

    awaiting = s.scalar(
        select(func.count())
        .select_from(PurchaseOrder)
        .where(PurchaseOrder.status == OrderStatus.SUGGESTED)
    )
    return Overview(
        rows=rows,
        below_min=sum(1 for r in rows if r.status is StockStatus.RED),
        sold_today=sum((r.sold_today for r in rows), Decimal(0)),
        sold_month=sum((r.sold_month for r in rows), Decimal(0)),
        awaiting_approval=awaiting or 0,
    )


def daily_sales(
    s: Session, *, now: datetime, tz: str, product_id: int | None = None, days: int = 30
) -> list[tuple[date, Decimal]]:
    today = local_today(now, tz)
    since = today - timedelta(days=days - 1)
    day = _local_day(tz)
    start = datetime.combine(since, time.min, tzinfo=ZoneInfo(tz))
    stmt = (
        select(day, _net_quantity())
        .where(StockMovement.kind.in_(_NET_KINDS), StockMovement.occurred_at >= start)
        .group_by(day)
    )
    if product_id is not None:
        stmt = stmt.where(StockMovement.product_id == product_id)
    totals: dict[date, Decimal] = {d: q for d, q in s.execute(stmt)}
    return [
        (since + timedelta(days=i), totals.get(since + timedelta(days=i), Decimal(0)))
        for i in range(days)
    ]


@dataclass(frozen=True)
class SalesKpis:
    revenue_today: Decimal
    revenue_month: Decimal
    sales_month: int
    avg_ticket_month: Decimal | None
    margin_month: Decimal | None
    items_with_cost: int
    items_month: int
    by_method_month: dict[PaymentMethod, Decimal]


_CENT = Decimal("0.01")


def sales_kpis(s: Session, *, now: datetime, tz: str) -> SalesKpis:
    """Indicadores de vendas concluídas (não canceladas), por dia local de `completed_at`."""
    today = local_today(now, tz)
    month_start = today.replace(day=1)
    day = func.date(func.timezone(tz, Sale.completed_at))
    in_month = (Sale.status == SaleStatus.COMPLETED, day >= month_start, day <= today)

    sales = s.execute(select(Sale.total, day).where(*in_month)).all()
    revenue_month = sum((total for total, _ in sales), Decimal(0)).quantize(_CENT)
    revenue_today = sum((total for total, d in sales if d == today), Decimal(0)).quantize(_CENT)
    avg = (revenue_month / len(sales)).quantize(_CENT) if sales else None

    items = s.execute(
        select(SaleItem.quantity, SaleItem.unit_cost, SaleItem.line_total)
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(*in_month)
    ).all()
    with_cost = [(q, c, lt) for q, c, lt in items if c is not None]
    margin = (
        sum((lt - line_total(q, c) for q, c, lt in with_cost), Decimal(0)).quantize(_CENT)
        if with_cost
        else None
    )

    by_method = {m: Decimal("0.00") for m in PaymentMethod}
    for method, amount in s.execute(
        select(SalePayment.method, func.sum(SalePayment.amount))
        .join(Sale, SalePayment.sale_id == Sale.id)
        .where(*in_month)
        .group_by(SalePayment.method)
    ):
        by_method[method] = amount
    change = s.scalar(select(func.sum(Sale.change_given)).where(*in_month)) or Decimal(0)
    by_method[PaymentMethod.CASH] = (by_method[PaymentMethod.CASH] - change).quantize(_CENT)

    return SalesKpis(
        revenue_today=revenue_today,
        revenue_month=revenue_month,
        sales_month=len(sales),
        avg_ticket_month=avg,
        margin_month=margin,
        items_with_cost=len(with_cost),
        items_month=len(items),
        by_method_month=by_method,
    )
