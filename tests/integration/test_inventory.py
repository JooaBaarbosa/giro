import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from giro.errors import DuplicateSku, InsufficientStock, InvalidInput, NotFound
from giro.inventory.schemas import MovementIn, ProductIn, ProductUpdate, SupplierIn
from giro.inventory.service import (
    create_product,
    create_supplier,
    get_product,
    list_movements,
    record_movement,
    update_product,
)
from giro.models import Product, Unit


def sale(product_id: int, qty: str | int) -> MovementIn:
    return MovementIn(product_id=product_id, kind="sale", quantity=Decimal(qty))


def test_sale_decrements_and_records(session, make_product):
    p = make_product(current_stock=Decimal(10))
    record_movement(session, sale(p.id, 3))
    assert p.current_stock == 7
    assert len(list_movements(session, p.id)) == 1


def test_purchase_increments(session, make_product):
    p = make_product(current_stock=Decimal(10))
    record_movement(session, MovementIn(product_id=p.id, kind="purchase", quantity=Decimal(5)))
    assert p.current_stock == 15


def test_sale_beyond_stock_refused(session, make_product):
    p = make_product(current_stock=Decimal(2))
    with pytest.raises(InsufficientStock) as e:
        record_movement(session, sale(p.id, 3))
    assert e.value.message == "Estoque insuficiente: disponível 2, solicitado 3."
    assert p.current_stock == 2


def test_negative_adjustment_refused_below_zero(session, make_product):
    p = make_product(current_stock=Decimal(1))
    with pytest.raises(InsufficientStock):
        record_movement(
            session, MovementIn(product_id=p.id, kind="adjustment", quantity=Decimal(-2))
        )
    assert p.current_stock == 1


def test_fractional_quantity_refused_for_un(session, make_product):
    p = make_product(unit=Unit.UN, current_stock=Decimal(10))
    with pytest.raises(InvalidInput, match="inteira para unidade un"):
        record_movement(session, sale(p.id, "1.5"))
    assert p.current_stock == 10


def test_fractional_quantity_allowed_for_kg(session, make_product):
    p = make_product(unit=Unit.KG, current_stock=Decimal(10))
    record_movement(session, sale(p.id, "1.5"))
    assert p.current_stock == Decimal("8.5")


def test_unknown_product(session):
    with pytest.raises(NotFound):
        record_movement(session, sale(999, 1))


def test_concurrent_sales_never_oversell(engine, session, make_product):
    p = make_product(current_stock=Decimal(10))
    session.commit()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker() -> None:
        with Session(engine) as s:
            barrier.wait()
            try:
                record_movement(s, sale(p.id, 6))
                s.commit()
                results.append("ok")
            except InsufficientStock:
                s.rollback()
                results.append("insufficient")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["insufficient", "ok"]
    session.expire_all()
    assert session.get(Product, p.id).current_stock == 4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+55 (11) 98765-4321", "+5511987654321"), ("+5511987654321", "+5511987654321")],
)
def test_phone_normalized(raw, expected):
    assert SupplierIn(name="X", whatsapp=raw).whatsapp == expected


@pytest.mark.parametrize("raw", ["11987654321", "+55 11", "abc"])
def test_phone_invalid(raw):
    with pytest.raises(ValidationError, match="formato internacional"):
        SupplierIn(name="X", whatsapp=raw)


def test_duplicate_sku(session):
    sup = create_supplier(session, SupplierIn(name="Dist", whatsapp="+5511987654321"))
    data = ProductIn(sku="A1", name="Arroz", unit=Unit.UN, supplier_id=sup.id, min_stock=5)
    create_product(session, data)
    with pytest.raises(DuplicateSku, match="SKU já cadastrado"):
        create_product(session, data)
    # a sessão continua utilizável após o erro
    assert get_product(session, 1).sku == "A1"


def test_product_with_unknown_supplier(session):
    with pytest.raises(NotFound):
        create_product(
            session, ProductIn(sku="B", name="B", unit=Unit.UN, supplier_id=999, min_stock=1)
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "sale", "quantity": 0},
        {"kind": "purchase", "quantity": -1},
        {"kind": "adjustment", "quantity": 0},
        {"kind": "sale", "quantity": 1, "occurred_at": datetime(2026, 10, 22, 10, 0)},
    ],
)
def test_movement_validation(kwargs):
    with pytest.raises(ValidationError):
        MovementIn(product_id=1, **kwargs)


def test_unit_change_refused_with_fractional_stock(session, make_product):
    p = make_product(unit=Unit.KG, current_stock=Decimal("2.5"))
    with pytest.raises(InvalidInput, match="fracionad"):
        update_product(session, p.id, ProductUpdate(unit=Unit.UN))
    assert p.unit is Unit.KG


def test_unit_change_allowed_with_integer_stock(session, make_product):
    p = make_product(unit=Unit.KG, current_stock=Decimal("3.000"))
    update_product(session, p.id, ProductUpdate(unit=Unit.UN))
    assert p.unit is Unit.UN


def test_unit_change_refused_with_fractional_open_order_item(session, make_product):
    from giro.orders.service import suggest_orders

    p = make_product(unit=Unit.KG, current_stock=Decimal(0), min_stock=Decimal("1.5"))
    suggest_orders(session, now=datetime(2026, 10, 22, 15, tzinfo=UTC), tz="America/Sao_Paulo")
    with pytest.raises(InvalidInput, match="pedido em aberto"):
        update_product(session, p.id, ProductUpdate(unit=Unit.UN))


def test_concurrent_duplicate_sku_is_domain_error(engine, session, make_supplier):
    sup = make_supplier()
    session.commit()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker() -> None:
        with Session(engine) as s:
            barrier.wait()
            try:
                create_product(
                    s,
                    ProductIn(sku="RACE", name="X", unit=Unit.UN, supplier_id=sup.id, min_stock=1),
                )
                s.commit()
                results.append("ok")
            except DuplicateSku:
                s.rollback()
                results.append("duplicate")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["duplicate", "ok"]
