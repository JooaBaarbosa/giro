from datetime import UTC, datetime

from giro.inventory.service import list_products, list_suppliers
from giro.orders.service import suggest_orders
from giro.reports import StockStatus, overview
from giro.seed import seed

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
TZ = "America/Sao_Paulo"


def test_seed_creates_demo_and_is_noop_second_time(session):
    assert seed(session, now=NOW) is True
    products = list_products(session)
    assert len(products) == 25
    assert len(list_suppliers(session)) == 4
    below = [p for p in products if p.current_stock < p.min_stock]
    assert len(below) == 5
    assert len({p.supplier_id for p in below}) >= 2
    assert seed(session, now=NOW) is False


def test_seed_is_deterministic(session):
    seed(session, now=NOW)
    stocks = {p.sku: p.current_stock for p in list_products(session)}
    assert stocks["ARR5"] > 0
    assert all(stock >= 0 for stock in stocks.values())


def test_seed_shows_all_statuses_with_forecast(session):
    seed(session, now=NOW)
    ov = overview(session, now=NOW, tz=TZ)
    statuses = {r.status for r in ov.rows}
    assert statuses == {StockStatus.RED, StockStatus.YELLOW, StockStatus.GREEN}
    assert all(not r.forecast.no_forecast for r in ov.rows)
    assert ov.sold_today > 0


def test_seed_then_suggest_creates_orders(session):
    seed(session, now=NOW)
    assert len(suggest_orders(session, now=NOW, tz=TZ)) >= 2
