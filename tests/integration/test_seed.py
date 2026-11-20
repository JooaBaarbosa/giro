from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from giro.inventory.service import list_products, list_suppliers
from giro.models import CashSession, Sale, SaleStatus
from giro.orders.service import suggest_orders
from giro.pos.codes import is_valid_gtin
from giro.reports import StockStatus, overview
from giro.seed import PRODUCTS, seed

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


def test_seed_fills_price_cost_and_valid_barcodes(session):
    seed(session, now=NOW)
    products = list_products(session)
    assert all(p.sale_price is not None and p.cost_price is not None for p in products)
    assert all(p.cost_price < p.sale_price for p in products)
    barcodes = [p.barcode for p in products if p.barcode]
    assert len(barcodes) == 6
    assert len(set(barcodes)) == 6
    assert all(is_valid_gtin(b) for b in barcodes)


def test_seed_creates_three_closed_demo_sessions_with_sales(session):
    seed(session, now=NOW)
    sessions = list(session.scalars(select(CashSession)))
    assert len(sessions) == 3
    tz = ZoneInfo(TZ)
    days = set()
    for cs in sessions:
        assert cs.is_demo is True
        assert cs.closed_at is not None
        assert cs.difference == 0
        assert cs.counted_amount == cs.expected_amount
        assert cs.opened_at.astimezone(tz).date() == cs.closed_at.astimezone(tz).date()
        assert cs.opened_at <= cs.closed_at <= NOW
        days.add(cs.opened_at.astimezone(tz).date())
        sales = list(session.scalars(select(Sale).where(Sale.session_id == cs.id)))
        assert len(sales) >= 3
        assert all(x.status is SaleStatus.COMPLETED for x in sales)
    assert len(days) == 3


def test_seed_twice_does_not_duplicate_sessions(session):
    seed(session, now=NOW)
    seed(session, now=NOW)
    assert len(list(session.scalars(select(CashSession)))) == 3


def test_seed_keeps_demo_stock_profiles_after_demo_sales(session):
    seed(session, now=NOW)
    ov = overview(session, now=NOW, tz=TZ)
    by_sku = {r.product.sku: r.status for r in ov.rows}
    expected = {
        "red": StockStatus.RED,
        "yellow": StockStatus.YELLOW,
        "green": StockStatus.GREEN,
    }
    for p in PRODUCTS:
        assert by_sku[p.sku] is expected[p.profile], p.sku
