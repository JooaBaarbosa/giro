from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from giro.inventory.schemas import MovementIn
from giro.inventory.service import record_movement
from giro.models import (
    CashSession,
    MovementKind,
    OrderStatus,
    PaymentMethod,
    PurchaseOrder,
    Sale,
    SaleItem,
    SalePayment,
    SaleStatus,
    StockMovement,
)
from giro.reports import StockStatus, daily_sales, overview, sales_kpis

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


def cancel_movement(session, product, qty, at: datetime) -> None:
    session.add(
        StockMovement(
            product_id=product.id,
            kind=MovementKind.SALE_CANCEL,
            quantity=Decimal(qty),
            occurred_at=at,
        )
    )
    session.flush()


def test_cancelled_sale_is_netted_from_outflow(session, make_product):
    p = make_product(current_stock=Decimal(100))
    sell(session, p, 5, datetime(2026, 10, 22, 13, 0, tzinfo=UTC))
    cancel_movement(session, p, 2, datetime(2026, 10, 22, 14, 0, tzinfo=UTC))

    ov = overview(session, now=NOW, tz=TZ)
    assert ov.rows[0].sold_today == 3
    assert ov.sold_month == 3
    assert daily_sales(session, now=NOW, tz=TZ)[-1][1] == 3


def make_sale(
    session,
    cash_session,
    product,
    *,
    at: datetime,
    lines: list[tuple[str, str, str | None]],
    payments: list[tuple[PaymentMethod, str]],
    change: str = "0",
    status: SaleStatus = SaleStatus.COMPLETED,
) -> Sale:
    items = []
    for qty, price, cost in lines:
        q, pr = Decimal(qty), Decimal(price)
        items.append(
            SaleItem(
                product_id=product.id,
                quantity=q,
                unit_price=pr,
                unit_cost=None if cost is None else Decimal(cost),
                line_total=(q * pr).quantize(Decimal("0.01")),
            )
        )
    sale = Sale(
        session_id=cash_session.id,
        status=status,
        total=sum((i.line_total for i in items), Decimal(0)),
        change_given=Decimal(change),
        completed_at=at,
        items=items,
        payments=[SalePayment(method=m, amount=Decimal(a)) for m, a in payments],
    )
    session.add(sale)
    session.flush()
    return sale


def test_sales_kpis(session, make_product):
    p = make_product()
    cs = CashSession(opening_amount=Decimal(0))
    session.add(cs)
    session.flush()
    today = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    # hoje: 2 x 10,00 (custo 6,00) + 1 x 5,00 sem custo = 25,00; paga 30 em dinheiro, troco 5
    make_sale(
        session,
        cs,
        p,
        at=today,
        lines=[("2", "10.00", "6.00"), ("1", "5.00", None)],
        payments=[(PaymentMethod.CASH, "30.00")],
        change="5.00",
    )
    # início do mês: 1 x 15,00 (custo 4,005 arredonda 4,01) pago no pix
    make_sale(
        session,
        cs,
        p,
        at=datetime(2026, 10, 3, 15, 0, tzinfo=UTC),
        lines=[("1", "15.00", "4.01")],
        payments=[(PaymentMethod.PIX, "15.00")],
    )
    # mês passado: fora
    make_sale(
        session,
        cs,
        p,
        at=datetime(2026, 9, 30, 15, 0, tzinfo=UTC),
        lines=[("1", "99.00", "1.00")],
        payments=[(PaymentMethod.CREDIT, "99.00")],
    )
    # cancelada: fora
    make_sale(
        session,
        cs,
        p,
        at=today,
        lines=[("1", "50.00", "1.00")],
        payments=[(PaymentMethod.DEBIT, "50.00")],
        status=SaleStatus.CANCELLED,
    )

    k = sales_kpis(session, now=NOW, tz=TZ)

    assert k.revenue_today == Decimal("25.00")
    assert k.revenue_month == Decimal("40.00")
    assert k.sales_month == 2
    assert k.avg_ticket_month == Decimal("20.00")
    assert k.items_month == 3
    assert k.items_with_cost == 2
    # (20,00 - 12,00) + (15,00 - 4,01)
    assert k.margin_month == Decimal("18.99")
    assert k.by_method_month == {
        PaymentMethod.CASH: Decimal("25.00"),
        PaymentMethod.PIX: Decimal("15.00"),
        PaymentMethod.CREDIT: Decimal("0.00"),
        PaymentMethod.DEBIT: Decimal("0.00"),
    }


def test_sales_kpis_without_sales(session):
    k = sales_kpis(session, now=NOW, tz=TZ)

    assert k.revenue_today == Decimal("0.00")
    assert k.revenue_month == Decimal("0.00")
    assert k.sales_month == 0
    assert k.avg_ticket_month is None
    assert k.margin_month is None
    assert k.items_with_cost == 0
    assert k.items_month == 0
    assert set(k.by_method_month) == set(PaymentMethod)
    assert all(v == Decimal("0.00") for v in k.by_method_month.values())


def test_sales_kpis_counts_demo_completed_sales(session, make_product):
    p = make_product()
    real = CashSession(opening_amount=Decimal(0), closed_at=NOW)
    demo = CashSession(opening_amount=Decimal(0), is_demo=True)
    session.add_all([real, demo])
    session.flush()
    at = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    kwargs = dict(at=at, lines=[("1", "10.00", None)], payments=[(PaymentMethod.PIX, "10.00")])
    make_sale(session, real, p, **kwargs)
    make_sale(session, demo, p, **kwargs)
    make_sale(session, demo, p, **kwargs)
    make_sale(session, demo, p, status=SaleStatus.CANCELLED, **kwargs)
    make_sale(session, demo, p, **{**kwargs, "at": datetime(2026, 9, 30, 15, 0, tzinfo=UTC)})

    assert sales_kpis(session, now=NOW, tz=TZ).demo_sales_month == 2
