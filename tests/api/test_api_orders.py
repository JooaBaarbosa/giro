from decimal import Decimal

import pytest

from giro.orders.service import approve, suggest_orders
from tests.integration.test_orders import NOW, TZ


@pytest.fixture
def low_stock(make_supplier, make_product):
    sup = make_supplier(name="Dist A", whatsapp="+5511987654321")
    make_product(name="Arroz", supplier=sup, current_stock=Decimal(1), min_stock=Decimal(5))
    make_product(name="Café", supplier=sup, current_stock=Decimal(0), min_stock=Decimal(3))
    return sup


@pytest.fixture
def approved_order(session, low_stock):
    order = suggest_orders(session, now=NOW, tz=TZ)[0]
    approve(session, order.id, now=NOW)
    session.commit()
    return order


def test_suggest_returns_alert_params(client, auth, low_stock):
    r = client.post("/api/purchase-orders/suggest", headers=auth)
    assert r.status_code == 200
    [order] = r.json()
    assert order["alert_params"][0] == "2"
    assert order["alert_params"][3].endswith(f"/orders/{order['id']}")
    assert order["supplier"]["whatsapp_digits"] == "5511987654321"
    assert client.post("/api/purchase-orders/suggest", headers=auth).json() == []


def test_list_and_get(client, auth, approved_order):
    listed = client.get("/api/purchase-orders?status=approved", headers=auth).json()
    assert [o["id"] for o in listed] == [approved_order.id]
    one = client.get(f"/api/purchase-orders/{approved_order.id}", headers=auth).json()
    assert one["template_params"] == ["Dist A", "Loja Teste", "Arroz: 4 un · Café: 3 un"]


def test_claim_twice_is_409(client, auth, approved_order):
    url = f"/api/purchase-orders/{approved_order.id}/claim"
    assert client.post(url, headers=auth).json()["status"] == "sending"
    assert client.post(url, headers=auth).status_code == 409


def test_sent_callback_stores_dry_run(client, auth, approved_order):
    oid = approved_order.id
    client.post(f"/api/purchase-orders/{oid}/claim", headers=auth)
    r = client.post(
        f"/api/purchase-orders/{oid}/sent",
        headers=auth,
        json={"message_text": "Olá...", "dry_run": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent" and body["dry_run"] is True and body["message_text"] == "Olá..."


def test_failed_callback(client, auth, approved_order):
    oid = approved_order.id
    client.post(f"/api/purchase-orders/{oid}/claim", headers=auth)
    r = client.post(
        f"/api/purchase-orders/{oid}/failed", headers=auth, json={"error": "token expirado"}
    )
    assert r.json()["status"] == "failed" and r.json()["error"] == "token expirado"


def test_unknown_order_404(client, auth):
    assert client.get("/api/purchase-orders/999", headers=auth).status_code == 404


def test_owner_alert_callback(client, auth, approved_order):
    r = client.post(
        f"/api/purchase-orders/{approved_order.id}/owner-alert",
        headers=auth,
        json={"dry_run": True},
    )
    assert r.status_code == 200
    assert r.json()["owner_alert_dry_run"] is True
    assert r.json()["owner_alerted_at"] is not None
