import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from giro.errors import InvalidInput, InvalidTransition, NoOpenSession, NotFound
from giro.models import Sale, SaleItem, SaleStatus, Unit
from giro.pos.schemas import AddItemIn, ItemQtyIn, OpenSessionIn
from giro.pos.service import (
    add_item,
    current_sale,
    discard_sale,
    open_session,
    over_stock,
    remove_item,
    set_item_quantity,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
D = Decimal


@pytest.fixture
def cash(session: Session):
    return open_session(session, OpenSessionIn(opening_amount=D("100")), now=NOW)


def _add(s: Session, code: str, qty: str | None = None):
    quantity = D(qty) if qty is not None else None
    return add_item(s, AddItemIn(code=code, quantity=quantity), now=NOW)


def test_add_by_barcode_creates_open_sale(session, cash, make_product):
    p = make_product(sku="A1", barcode="7891000100103", sale_price=D("4.50"))
    item = _add(session, "7891000100103")
    sale = current_sale(session)
    assert sale is not None
    assert sale.status is SaleStatus.OPEN
    assert sale.session_id == cash.id
    assert sale.created_at == NOW
    assert item.product_id == p.id
    assert item.quantity == D("1")
    assert item.line_total == D("4.50")
    assert sale.total == D("4.50")


def test_same_product_merges_line(session, cash, make_product):
    make_product(sku="ARR5", sale_price=D("5.00"))
    _add(session, "ARR5")
    item = _add(session, "ARR5", "2")
    sale = current_sale(session)
    assert len(sale.items) == 1
    assert item.quantity == D("3")
    assert item.line_total == D("15.00")
    assert sale.total == D("15.00")


def test_price_frozen_on_line(session, cash, make_product):
    p = make_product(sku="ARR5", sale_price=D("5.00"), cost_price=D("2.00"))
    _add(session, "ARR5")
    p.sale_price = D("9.00")
    p.cost_price = D("3.00")
    item = _add(session, "ARR5")
    assert item.unit_price == D("5.00")
    assert item.unit_cost == D("2.00")
    assert current_sale(session).total == D("10.00")


def test_kg_requires_weight(session, cash, make_product):
    make_product(sku="QMU", name="Queijo", unit=Unit.KG, sale_price=D("49.90"))
    with pytest.raises(InvalidInput) as exc:
        _add(session, "QMU")
    assert exc.value.message == "Informe o peso para Queijo."


def test_kg_with_weight(session, cash, make_product):
    make_product(sku="QMU", unit=Unit.KG, sale_price=D("49.90"))
    item = _add(session, "QMU", "0.350")
    assert item.line_total == D("17.47")
    assert current_sale(session).total == D("17.47")


def test_fractional_un_refused(session, cash, make_product):
    make_product(sku="ARR5", sale_price=D("5.00"))
    with pytest.raises(InvalidInput):
        _add(session, "ARR5", "1.5")
    assert current_sale(session) is None


def test_fractional_merge_refused(session, cash, make_product):
    make_product(sku="CX", unit=Unit.CX, sale_price=D("5.00"))
    _add(session, "CX", "1")
    with pytest.raises(InvalidInput):
        _add(session, "CX", "0.5")


def test_no_price_refused(session, cash, make_product):
    make_product(sku="NP")
    with pytest.raises(InvalidInput) as exc:
        _add(session, "NP")
    assert exc.value.message == "Produto sem preço de venda: cadastre o preço antes de vender."


def test_add_without_session_refused(session, make_product):
    make_product(sku="ARR5", sale_price=D("5.00"))
    with pytest.raises(NoOpenSession):
        _add(session, "ARR5")


def test_unknown_code(session, cash):
    with pytest.raises(NotFound):
        _add(session, "ZZZ")


def test_over_stock_flag_does_not_block(session, cash, make_product):
    make_product(sku="ARR5", sale_price=D("5.00"), current_stock=D("1"))
    item = _add(session, "ARR5", "3")
    assert item.quantity == D("3")
    assert over_stock(item) is True
    set_item_quantity(session, item.id, ItemQtyIn(quantity=D("1")))
    assert over_stock(item) is False


def test_set_quantity_recalculates(session, cash, make_product):
    make_product(sku="ARR5", sale_price=D("5.00"))
    item = _add(session, "ARR5")
    set_item_quantity(session, item.id, ItemQtyIn(quantity=D("4")))
    assert item.line_total == D("20.00")
    assert current_sale(session).total == D("20.00")
    with pytest.raises(InvalidInput):
        set_item_quantity(session, item.id, ItemQtyIn(quantity=D("1.5")))
    with pytest.raises(NotFound):
        set_item_quantity(session, 999999, ItemQtyIn(quantity=D("1")))


def test_remove_and_discard(session, cash, make_product):
    make_product(sku="A", sale_price=D("5.00"))
    make_product(sku="B", sale_price=D("2.00"))
    a = _add(session, "A")
    _add(session, "B")
    remove_item(session, a.id)
    sale = current_sale(session)
    assert [i.product.sku for i in sale.items] == ["B"]
    assert sale.total == D("2.00")
    with pytest.raises(NotFound):
        remove_item(session, a.id)
    remove_item(session, sale.items[0].id)
    assert current_sale(session).total == D("0")
    _add(session, "A")
    discard_sale(session)
    session.flush()
    assert current_sale(session) is None
    assert session.scalar(select(func.count()).select_from(Sale)) == 0
    assert session.scalar(select(func.count()).select_from(SaleItem)) == 0
    discard_sale(session)


def test_item_of_non_open_sale_refused(session, cash, make_product):
    make_product(sku="A", sale_price=D("5.00"))
    item = _add(session, "A")
    current_sale(session).status = SaleStatus.COMPLETED
    session.flush()
    with pytest.raises(InvalidTransition):
        remove_item(session, item.id)
    with pytest.raises(InvalidTransition):
        set_item_quantity(session, item.id, ItemQtyIn(quantity=D("2")))


def test_concurrent_first_add_reuses_one_sale(engine, session, cash, make_product):
    make_product(sku="ARR5", sale_price=D("5.00"))
    session.commit()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker() -> None:
        with Session(engine) as s:
            barrier.wait()
            try:
                add_item(s, AddItemIn(code="ARR5"), now=NOW)
                s.commit()
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    session.rollback()
    sales = session.scalars(select(Sale)).all()
    assert len(sales) == 1
    assert sales[0].status is SaleStatus.OPEN
    assert len(sales[0].items) == 1
    assert sales[0].items[0].quantity == D("2")
    assert sales[0].total == D("10.00")
