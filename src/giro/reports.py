"""Consultas do dashboard: totais de saída, previsão por produto e série diária."""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from giro.forecast import WINDOW_DAYS, Forecast, compute_forecast
from giro.models import MovementKind, OrderStatus, Product, PurchaseOrder, StockMovement

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


def _sales_by_day(
    s: Session, *, tz: str, since: date, product_ids: Collection[int] | None
) -> dict[int, dict[date, Decimal]]:
    day = _local_day(tz)
    start = datetime.combine(since, time.min, tzinfo=ZoneInfo(tz))
    stmt = (
        select(StockMovement.product_id, day, func.sum(StockMovement.quantity))
        .where(StockMovement.kind == MovementKind.SALE, StockMovement.occurred_at >= start)
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
        select(day, func.sum(StockMovement.quantity))
        .where(StockMovement.kind == MovementKind.SALE, StockMovement.occurred_at >= start)
        .group_by(day)
    )
    if product_id is not None:
        stmt = stmt.where(StockMovement.product_id == product_id)
    totals: dict[date, Decimal] = {d: q for d, q in s.execute(stmt)}
    return [
        (since + timedelta(days=i), totals.get(since + timedelta(days=i), Decimal(0)))
        for i in range(days)
    ]
