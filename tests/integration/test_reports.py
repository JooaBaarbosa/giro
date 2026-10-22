from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from giro.inventory.schemas import MovementIn
from giro.inventory.service import record_movement
from giro.models import OrderStatus, PurchaseOrder
from giro.reports import StockStatus, daily_sales, overview

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)  # 12:00 em São Paulo
TZ = "America/Sao_Paulo"


def sell(session, product, qty, at: datetime) -> None:
    record_movement(
        session,
        MovementIn(product_id=product.id, kind="sale", quantity=Decimal(qty), occurred_at=at),
    )


def sell_daily(session, product, qty, days: int = 30) -> None:
    for d in range(days):
        sell(session, product, qty, NOW - timedelta(days=d))


def test_today_and_month_totals(session, make_product):
    p = make_product(current_stock=Decimal(100))
    sell(session, p, 2, datetime(2026, 10, 22, 13, 0, tzinfo=UTC))
    sell(session, p, 3, datetime(2026, 10, 5, 15, 0, tzinfo=UTC))
    sell(session, p, 4, datetime(2026, 9, 28, 15, 0, tzinfo=UTC))

    ov = overview(session, now=NOW, tz=TZ)

    row = ov.rows[0]
    assert row.sold_today == 2
    assert row.sold_month == 5
    assert ov.sold_today == 2
    assert ov.sold_month == 5


def test_midnight_boundary_uses_store_tz(session, make_product):
    p = make_product(current_stock=Decimal(100))
    sell(session, p, 1, datetime(2026, 10, 22, 2, 30, tzinfo=UTC))  # 21/10 23:30 em SP

    ov = overview(session, now=NOW, tz=TZ)
    assert ov.rows[0].sold_today == 0

    series = dict(daily_sales(session, now=NOW, tz=TZ))
    assert series[date(2026, 10, 21)] == 1
    assert series[date(2026, 10, 22)] == 0


def test_status_and_ordering(session, make_product):
    green = make_product(name="Folgado", current_stock=Decimal(130), min_stock=Decimal(10))
    yellow = make_product(name="Acabando", current_stock=Decimal(115), min_stock=Decimal(10))
    red = make_product(name="Faltando", current_stock=Decimal(5), min_stock=Decimal(10))
    sell_daily(session, green, 1)  # sobra 100 → 90 dias
    sell_daily(session, yellow, 3)  # sobra 25 → 5 dias

    ov = overview(session, now=NOW, tz=TZ)

    assert [r.product.name for r in ov.rows] == ["Faltando", "Acabando", "Folgado"]
    assert [r.status for r in ov.rows] == [StockStatus.RED, StockStatus.YELLOW, StockStatus.GREEN]
    assert ov.below_min == 1
    assert ov.rows[1].forecast.next_purchase == date(2026, 10, 27)
    assert red.id  # sem vendas: sem previsão, mas vermelho por estar abaixo do mínimo
    assert ov.rows[0].forecast.no_forecast


def test_awaiting_approval_counts_suggested(session, make_product):
    p = make_product()
    session.add_all(
        [
            PurchaseOrder(supplier_id=p.supplier_id, status=OrderStatus.SUGGESTED),
            PurchaseOrder(supplier_id=p.supplier_id, status=OrderStatus.SENT),
        ]
    )
    session.flush()
    assert overview(session, now=NOW, tz=TZ).awaiting_approval == 1


def test_daily_sales_fills_gaps(session, make_product):
    p = make_product(current_stock=Decimal(100))
    sell(session, p, 2, NOW)
    sell(session, p, 5, NOW - timedelta(days=10))

    series = daily_sales(session, now=NOW, tz=TZ)

    assert len(series) == 30
    assert series[0][0] == date(2026, 9, 23)
    assert series[-1] == (date(2026, 10, 22), Decimal(2))
    assert sum(q for _, q in series) == 7


def test_daily_sales_per_product(session, make_product):
    a = make_product(current_stock=Decimal(100))
    b = make_product(current_stock=Decimal(100))
    sell(session, a, 2, NOW)
    sell(session, b, 9, NOW)
    series = daily_sales(session, now=NOW, tz=TZ, product_id=a.id)
    assert series[-1][1] == 2


def test_search_and_inactive_excluded(session, make_product):
    make_product(name="Arroz 5kg", sku="ARR5")
    make_product(name="Óleo 900ml", sku="OLE9")
    make_product(name="Arroz integral", active=False)

    names = [r.product.name for r in overview(session, now=NOW, tz=TZ, search="arr").rows]
    assert names == ["Arroz 5kg"]
    names = [r.product.name for r in overview(session, now=NOW, tz=TZ, search="ole9").rows]
    assert names == ["Óleo 900ml"]
