from datetime import date, timedelta
from decimal import Decimal

from giro.forecast import compute_forecast
from giro.models import Unit

TODAY = date(2026, 10, 22)


def daily(qty: str | int, days: int, end: date = TODAY) -> dict[date, Decimal]:
    return {end - timedelta(d): Decimal(qty) for d in range(days)}


def test_regular_consumption():
    f = compute_forecast(
        current_stock=Decimal(50),
        min_stock=Decimal(10),
        cover_days=15,
        unit=Unit.UN,
        sales_by_day=daily(3, 30),
        first_sale=TODAY - timedelta(60),
        today=TODAY,
    )
    assert f.avg_daily == 3
    assert f.next_purchase == TODAY + timedelta(13)
    assert f.suggested_qty == 5
    assert not f.low_history
    assert not f.no_forecast


def test_below_minimum_buys_today():
    f = compute_forecast(
        current_stock=Decimal(5),
        min_stock=Decimal(10),
        cover_days=15,
        unit=Unit.UN,
        sales_by_day=daily(3, 30),
        first_sale=TODAY - timedelta(60),
        today=TODAY,
    )
    assert f.next_purchase == TODAY
    assert f.suggested_qty == 50


def test_no_sales_below_min():
    f = compute_forecast(
        current_stock=Decimal(4),
        min_stock=Decimal(10),
        cover_days=15,
        unit=Unit.UN,
        sales_by_day={},
        first_sale=None,
        today=TODAY,
    )
    assert f.no_forecast
    assert f.next_purchase is None
    assert f.days_left is None
    assert f.suggested_qty == 6


def test_no_sales_above_min_suggests_at_least_one():
    f = compute_forecast(
        current_stock=Decimal(20),
        min_stock=Decimal(10),
        cover_days=15,
        unit=Unit.UN,
        sales_by_day={},
        first_sale=None,
        today=TODAY,
    )
    assert f.suggested_qty == 1


def test_low_history_window():
    f = compute_forecast(
        current_stock=Decimal(20),
        min_stock=Decimal(5),
        cover_days=10,
        unit=Unit.UN,
        sales_by_day=daily(2, 3),
        first_sale=TODAY - timedelta(2),
        today=TODAY,
    )
    assert f.low_history
    assert f.avg_daily == 2


def test_kg_rounds_up_one_decimal():
    f = compute_forecast(
        current_stock=Decimal("2.0"),
        min_stock=Decimal(3),
        cover_days=15,
        unit=Unit.KG,
        sales_by_day=daily("1.25", 30),
        first_sale=TODAY - timedelta(40),
        today=TODAY,
    )
    assert f.suggested_qty == Decimal("19.8")


def test_sales_older_than_window_ignored():
    sales = daily(3, 30) | {TODAY - timedelta(45): Decimal(1000)}
    f = compute_forecast(
        current_stock=Decimal(50),
        min_stock=Decimal(10),
        cover_days=15,
        unit=Unit.UN,
        sales_by_day=sales,
        first_sale=TODAY - timedelta(60),
        today=TODAY,
    )
    assert f.avg_daily == 3
