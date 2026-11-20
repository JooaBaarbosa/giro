from decimal import Decimal

import pytest

P = "/api/pos"


@pytest.fixture
def product(make_product):
    return make_product(
        sku="ARROZ", sale_price=Decimal("10.00"), cost_price=Decimal("6"), current_stock=Decimal(5)
    )


def _open(client, auth, amount="100.00"):
    r = client.post(f"{P}/session", headers=auth, json={"opening_amount": amount})
    assert r.status_code == 201
    return r.json()


def test_requires_token(client):
    assert client.get(f"{P}/session").status_code == 401


def test_full_flow(client, auth, product):
    assert client.get(f"{P}/session", headers=auth).json() is None
    assert _open(client, auth)["closed_at"] is None
    assert client.get(f"{P}/sale", headers=auth).json() is None

    r = client.post(f"{P}/sale/items", headers=auth, json={"code": "ARROZ", "quantity": "2"})
    assert r.status_code == 201
    item = r.json()
    assert item["name"] == product.name and Decimal(item["line_total"]) == 20

    r = client.patch(f"{P}/sale/items/{item['id']}", headers=auth, json={"quantity": "3"})
    assert Decimal(r.json()["line_total"]) == 30
    sale = client.get(f"{P}/sale", headers=auth).json()
    assert Decimal(sale["total"]) == 30

    r = client.post(f"{P}/sale/payments", headers=auth, json={"method": "cash", "amount": "50"})
    assert r.status_code == 201
    r = client.post(f"{P}/sale/complete", headers=auth, json={"sale_id": sale["id"]})
    assert r.status_code == 200
    done = r.json()
    assert done["status"] == "completed" and Decimal(done["change_given"]) == 20

    # duplo envio nao conclui outra venda
    r = client.post(f"{P}/sale/complete", headers=auth, json={"sale_id": sale["id"]})
    assert r.status_code == 409

    r = client.post(f"{P}/session/movements", headers=auth, json={"kind": "supply", "amount": "10"})
    assert r.status_code == 201

    summary = client.get(f"{P}/session/summary", headers=auth).json()
    assert summary["sales_count"] == 1 and Decimal(summary["expected_cash"]) == 140

    assert client.get(f"{P}/sales/{sale['id']}", headers=auth).json()["id"] == sale["id"]
    r = client.post(
        f"{P}/sales/{sale['id']}/cancel", headers=auth, json={"reason": "Cliente desistiu"}
    )
    assert r.json()["status"] == "cancelled"

    r = client.post(f"{P}/session/close", headers=auth, json={"counted_amount": "110"})
    assert r.status_code == 200
    assert Decimal(r.json()["difference"]) == 0
    assert client.get(f"{P}/session", headers=auth).json() is None


def test_remove_item_payment_and_discard(client, auth, product):
    _open(client, auth)
    item = client.post(f"{P}/sale/items", headers=auth, json={"code": "ARROZ"}).json()
    pay = client.post(
        f"{P}/sale/payments", headers=auth, json={"method": "pix", "amount": "5"}
    ).json()
    assert client.delete(f"{P}/sale/payments/{pay['id']}", headers=auth).status_code == 204
    assert client.delete(f"{P}/sale/items/{item['id']}", headers=auth).status_code == 204
    client.post(f"{P}/sale/items", headers=auth, json={"code": "ARROZ"})
    assert client.delete(f"{P}/sale", headers=auth).status_code == 204
    assert client.get(f"{P}/sale", headers=auth).json() is None


def test_insufficient_stock_has_shortages_and_adjust(client, auth, product):
    _open(client, auth)
    sale_item = client.post(
        f"{P}/sale/items", headers=auth, json={"code": "ARROZ", "quantity": "8"}
    ).json()
    client.post(f"{P}/sale/payments", headers=auth, json={"method": "pix", "amount": "80"})
    sale = client.get(f"{P}/sale", headers=auth).json()
    r = client.post(f"{P}/sale/complete", headers=auth, json={"sale_id": sale["id"]})
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "Estoque insuficiente para concluir a venda."
    assert len(body["shortages"]) == 1
    sh = body["shortages"][0]
    assert sh["product_id"] == sale_item["product_id"]
    assert sh["name"] == product.name
    assert Decimal(sh["available"]) == 5 and Decimal(sh["needed"]) == 8
    assert isinstance(sh["needed"], str)

    r = client.post(
        f"{P}/sale/adjust", headers=auth, json={"product_id": product.id, "reason": "Contagem"}
    )
    assert r.status_code == 200
    r = client.post(f"{P}/sale/complete", headers=auth, json={"sale_id": sale["id"]})
    assert r.status_code == 200


def test_no_open_session_is_409(client, auth):
    for r in (
        client.post(f"{P}/sale/items", headers=auth, json={"code": "X"}),
        client.get(f"{P}/session/summary", headers=auth),
        client.post(f"{P}/session/close", headers=auth, json={"counted_amount": "1"}),
    ):
        assert r.status_code == 409
        assert r.json() == {"detail": "O caixa está fechado. Abra o caixa para continuar."}


def test_open_twice_is_409(client, auth):
    _open(client, auth)
    r = client.post(f"{P}/session", headers=auth, json={"opening_amount": "1"})
    assert r.status_code == 409


def test_invalid_payment_is_422(client, auth, product):
    _open(client, auth)
    client.post(f"{P}/sale/items", headers=auth, json={"code": "ARROZ"})
    r = client.post(f"{P}/sale/payments", headers=auth, json={"method": "bogus", "amount": "5"})
    assert r.status_code == 422
    r = client.post(f"{P}/sale/payments", headers=auth, json={"method": "pix", "amount": "50"})
    assert r.status_code == 422
