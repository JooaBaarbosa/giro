import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from giro.config import Settings
from giro.errors import EmptyOrder, InvalidInput, InvalidTransition
from giro.inventory.service import list_movements
from giro.models import OrderStatus, PurchaseOrder, Unit
from giro.orders.schemas import FailedIn, ItemQtyIn, SentIn
from giro.orders.service import (
    TIMEOUT_ERROR,
    approve,
    cancel,
    claim,
    get_order,
    mark_failed,
    mark_sent,
    receive,
    remove_item,
    retry,
    set_item_quantity,
    suggest_orders,
    to_out,
    to_suggested_out,
)

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
TZ = "America/Sao_Paulo"
SETTINGS = Settings(
    database_url="unused", service_token="t", app_base_url="http://app", store_name="Loja"
)


@pytest.fixture
def two_suppliers(make_supplier, make_product):
    a = make_supplier(name="Dist A", whatsapp="+5511987654321")
    b = make_supplier(name="Dist B")
    make_product(name="Arroz", supplier=a, current_stock=Decimal(2), min_stock=Decimal(10))
    make_product(name="Feijão", supplier=a, current_stock=Decimal(1), min_stock=Decimal(5))
    make_product(name="Óleo", supplier=b, current_stock=Decimal(0), min_stock=Decimal(4))
    make_product(name="Sal", supplier=b, current_stock=Decimal(50), min_stock=Decimal(4))
    return a, b


def suggested(session) -> PurchaseOrder:
    return suggest_orders(session, now=NOW, tz=TZ)[0]


def test_suggest_groups_by_supplier_and_is_idempotent(session, two_suppliers):
    first = suggest_orders(session, now=NOW, tz=TZ)
    assert len(first) == 2
    assert sorted(len(o.items) for o in first) == [1, 2]
    assert all(o.status is OrderStatus.SUGGESTED for o in first)
    assert suggest_orders(session, now=NOW, tz=TZ) == []


def test_suggest_quantity_without_sales_covers_minimum(session, two_suppliers):
    orders = {o.supplier.name: o for o in suggest_orders(session, now=NOW, tz=TZ)}
    items = {i.product.name: i.quantity for i in orders["Dist A"].items}
    assert items == {"Arroz": 8, "Feijão": 4}


def test_suggest_skips_inactive_supplier_and_product(session, make_supplier, make_product):
    inactive = make_supplier(active=False)
    make_product(supplier=inactive, current_stock=Decimal(0))
    make_product(active=False, current_stock=Decimal(0))
    assert suggest_orders(session, now=NOW, tz=TZ) == []


def test_suggest_skips_product_in_failed_order(session, two_suppliers):
    for order in suggest_orders(session, now=NOW, tz=TZ):
        order.status = OrderStatus.FAILED
    session.flush()
    assert suggest_orders(session, now=NOW, tz=TZ) == []


def test_suggest_creates_new_order_after_cancelled(session, two_suppliers):
    for order in suggest_orders(session, now=NOW, tz=TZ):
        order.status = OrderStatus.CANCELLED
    session.flush()
    assert len(suggest_orders(session, now=NOW, tz=TZ)) == 2


def test_stuck_sending_becomes_failed(session, two_suppliers):
    old, recent = suggest_orders(session, now=NOW, tz=TZ)
    for order, minutes in ((old, 11), (recent, 9)):
        order.status = OrderStatus.SENDING
        order.claimed_at = NOW - timedelta(minutes=minutes)
    session.flush()

    suggest_orders(session, now=NOW, tz=TZ)
    session.expire_all()

    assert get_order(session, old.id).status is OrderStatus.FAILED
    assert get_order(session, old.id).error == TIMEOUT_ERROR
    assert get_order(session, recent.id).status is OrderStatus.SENDING


def test_approve_empty_refused(session, make_product):
    make_product(current_stock=Decimal(0))
    order = suggested(session)
    remove_item(session, order.id, order.items[0].id)
    with pytest.raises(EmptyOrder, match="Pedido sem itens"):
        approve(session, order.id, now=NOW)


def test_claim_only_once(engine, session, two_suppliers):
    order = suggested(session)
    approve(session, order.id, now=NOW)
    session.commit()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker() -> None:
        with Session(engine) as s:
            barrier.wait()
            try:
                claim(s, order.id, now=NOW)
                s.commit()
                results.append("ok")
            except InvalidTransition:
                s.rollback()
                results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"]


def test_full_flow_receive_adds_stock(session, two_suppliers):
    order = next(o for o in suggest_orders(session, now=NOW, tz=TZ) if len(o.items) == 2)
    approve(session, order.id, now=NOW)
    claim(session, order.id, now=NOW)
    mark_sent(session, order.id, SentIn(message_text="msg", dry_run=True), now=NOW)
    receive(session, order.id, now=NOW)

    assert order.status is OrderStatus.RECEIVED
    stocks = {i.product.name: i.product.current_stock for i in order.items}
    assert stocks == {"Arroz": 10, "Feijão": 5}
    movements = list_movements(session, order.items[0].product_id)
    assert movements[0].purchase_order_id == order.id


def test_mark_failed_then_retry_clears_error(session, two_suppliers):
    order = suggested(session)
    approve(session, order.id, now=NOW)
    claim(session, order.id, now=NOW)
    mark_failed(session, order.id, FailedIn(error="token expirado"))
    assert order.status is OrderStatus.FAILED and order.error == "token expirado"
    retry(session, order.id, now=NOW)
    assert order.status is OrderStatus.APPROVED and order.error is None


def test_cancel_sent_refused(session, two_suppliers):
    order = suggested(session)
    approve(session, order.id, now=NOW)
    claim(session, order.id, now=NOW)
    mark_sent(session, order.id, SentIn(message_text="m", dry_run=False), now=NOW)
    with pytest.raises(InvalidTransition):
        cancel(session, order.id)


@pytest.mark.parametrize("qty", [Decimal(0), Decimal(-1)])
def test_item_qty_must_be_positive(qty):
    with pytest.raises(ValidationError):
        ItemQtyIn(quantity=qty)


def test_set_item_quantity(session, two_suppliers):
    order = suggested(session)
    set_item_quantity(session, order.id, order.items[0].id, Decimal(30))
    assert order.items[0].quantity == 30


def test_item_qty_fractional_un_refused(session, two_suppliers):
    order = suggested(session)
    item = order.items[0]
    before = item.quantity
    with pytest.raises(InvalidInput, match="inteira"):
        set_item_quantity(session, order.id, item.id, Decimal("2.5"))
    assert item.quantity == before


def test_edit_items_only_when_suggested(session, two_suppliers):
    order = suggested(session)
    approve(session, order.id, now=NOW)
    with pytest.raises(InvalidTransition, match="sugeridos"):
        set_item_quantity(session, order.id, order.items[0].id, Decimal(3))


def test_to_out_builds_preview_and_params(session, two_suppliers):
    order = next(o for o in suggest_orders(session, now=NOW, tz=TZ) if o.supplier.name == "Dist A")
    out = to_out(order, settings=SETTINGS)
    assert out.supplier.whatsapp_digits == "5511987654321"
    assert out.template_params == ["Dist A", "Loja", "Arroz: 8 un · Feijão: 4 un"]
    assert out.message_preview.startswith("Olá Dist A, aqui é da Loja.")
    assert out.link == f"http://app/orders/{order.id}"
    alert = to_suggested_out(order, settings=SETTINGS).alert_params
    assert alert == ["2", "Arroz, Feijão", "Dist A", f"http://app/orders/{order.id}"]


def test_kg_item_keeps_decimal(session, make_product):
    make_product(name="Queijo", unit=Unit.KG, current_stock=Decimal("0.5"), min_stock=Decimal(2))
    order = suggested(session)
    assert to_out(order, settings=SETTINGS).items[0].quantity == Decimal("1.5")


def test_retry_only_from_failed(session, two_suppliers):
    order = suggested(session)
    with pytest.raises(InvalidTransition, match="falharam"):
        retry(session, order.id, now=NOW)
