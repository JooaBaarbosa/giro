from decimal import Decimal

from sqlalchemy import select

from giro.inventory.schemas import MovementIn
from giro.inventory.service import record_movement
from giro.models import Product, Supplier, Unit


def test_movement_form_lists_products(client, make_product):
    make_product(name="Café 500g", sku="CAF5")
    html = client.get("/movements/new").text
    assert "Café 500g — CAF5" in html


def test_register_sale_via_form(client, make_product):
    p = make_product(name="Café 500g", current_stock=Decimal(10))
    r = client.post("/movements", data={"product_id": p.id, "kind": "sale", "quantity": "2"})
    assert r.status_code == 200
    assert "Venda registrada: Café 500g 2 un. Estoque: 8" in r.text


def test_register_adjustment_label(client, make_product):
    p = make_product(name="Sal", current_stock=Decimal(10))
    r = client.post("/movements", data={"product_id": p.id, "kind": "adjustment", "quantity": "-1"})
    assert "Ajuste registrado: Sal -1 un. Estoque: 9" in r.text


def test_htmx_post_returns_partial(client, make_product):
    p = make_product(current_stock=Decimal(10))
    r = client.post(
        "/movements",
        data={"product_id": p.id, "kind": "sale", "quantity": "1"},
        headers={"HX-Request": "true"},
    )
    assert "registrada" in r.text
    assert "<html" not in r.text


def test_form_shows_insufficient_stock(client, make_product):
    p = make_product(current_stock=Decimal(1))
    r = client.post("/movements", data={"product_id": p.id, "kind": "sale", "quantity": "5"})
    assert "Estoque insuficiente: disponível 1, solicitado 5." in r.text
    assert 'value="5"' in r.text


def test_form_shows_fractional_error(client, make_product):
    p = make_product(unit=Unit.UN, current_stock=Decimal(10))
    r = client.post("/movements", data={"product_id": p.id, "kind": "sale", "quantity": "1,5"})
    assert "inteira para unidade un" in r.text


def test_form_shows_validation_error(client, make_product):
    p = make_product(current_stock=Decimal(10))
    r = client.post("/movements", data={"product_id": p.id, "kind": "sale", "quantity": "0"})
    assert "Quantidade deve ser maior que zero." in r.text


def test_create_supplier_bad_phone_shows_field_error(client, session):
    r = client.post("/suppliers", data={"name": "Dist", "whatsapp": "123"})
    assert "formato internacional" in r.text
    assert session.scalars(select(Supplier)).all() == []


def test_create_supplier_and_product_via_forms(client, session):
    r = client.post("/suppliers", data={"name": "Dist", "whatsapp": "+55 11 98765-4321"})
    assert r.status_code == 200  # segue o redirect para a lista
    supplier = session.scalars(select(Supplier)).one()
    r = client.post(
        "/products",
        data={
            "sku": "ARR5",
            "name": "Arroz 5kg",
            "unit": "un",
            "supplier_id": supplier.id,
            "min_stock": "10",
            "cover_days": "15",
        },
    )
    assert r.status_code == 200
    assert "Arroz 5kg" in r.text
    assert session.scalars(select(Product)).one().sku == "ARR5"


def test_create_product_duplicate_sku(client, make_product):
    p = make_product(sku="DUP")
    r = client.post(
        "/products",
        data={
            "sku": "DUP",
            "name": "X",
            "unit": "un",
            "supplier_id": p.supplier_id,
            "min_stock": "1",
            "cover_days": "15",
        },
    )
    assert "SKU já cadastrado." in r.text


def test_product_detail_shows_history(client, session, make_product):
    p = make_product(name="Feijão", current_stock=Decimal(10))
    record_movement(
        session, MovementIn(product_id=p.id, kind="sale", quantity=Decimal(3), note="balcão")
    )
    html = client.get(f"/products/{p.id}").text
    assert "Feijão" in html
    assert "balcão" in html
    assert "Venda" in html
    assert 'id="sales-chart"' in html


def test_edit_min_stock(client, session, make_product):
    p = make_product(min_stock=Decimal(10))
    r = client.post(
        f"/products/{p.id}",
        data={
            "name": p.name,
            "supplier_id": p.supplier_id,
            "min_stock": "20",
            "cover_days": "15",
            "active": "on",
        },
    )
    assert r.status_code == 200
    session.refresh(p)
    assert p.min_stock == 20 and p.active


def test_unknown_product_404(client):
    assert client.get("/products/999").status_code == 404
