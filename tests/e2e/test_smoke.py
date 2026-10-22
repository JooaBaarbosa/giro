"""Fumaça ponta a ponta contra o `docker compose up` (dry-run do WhatsApp).

Rodar: docker compose up -d --build && uv run pytest -m e2e -v
"""

import os
import time
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("GIRO_URL", "http://localhost:8000")
# variável própria: o conftest define SERVICE_TOKEN=test-token para os testes locais
TOKEN = os.environ.get("GIRO_SERVICE_TOKEN", "troque-este-token")
AUTH = {"X-Service-Token": TOKEN}

pytestmark = pytest.mark.e2e


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        yield c


@pytest.fixture
def e2e_supplier_product(client):
    """Fornecedor + produto zerado (abaixo do mínimo) próprios do teste; desativados no fim
    para não poluir a demo."""
    suffix = uuid.uuid4().hex[:8]
    supplier = (
        client.post(
            "/api/suppliers",
            headers=AUTH,
            json={"name": f"Fornecedor E2E {suffix}", "whatsapp": "+5511900009999"},
        )
        .raise_for_status()
        .json()
    )
    product = (
        client.post(
            "/api/products",
            headers=AUTH,
            json={
                "sku": f"E2E-{suffix}",
                "name": f"Item E2E {suffix}",
                "unit": "un",
                "supplier_id": supplier["id"],
                "min_stock": "5",
            },
        )
        .raise_for_status()
        .json()
    )
    yield supplier, product
    client.patch(f"/api/products/{product['id']}", headers=AUTH, json={"active": False})
    client.patch(f"/api/suppliers/{supplier['id']}", headers=AUTH, json={"active": False})


def test_approved_order_is_sent_in_dry_run(client, e2e_supplier_product):
    supplier, product = e2e_supplier_product
    created = client.post("/api/purchase-orders/suggest", headers=AUTH).raise_for_status().json()
    ours = [o for o in created if o["supplier"]["id"] == supplier["id"]]
    assert len(ours) == 1, created
    order_id = ours[0]["id"]

    approve = client.post(f"/orders/{order_id}/approve")
    assert approve.status_code == 200
    assert "Aprovado" in approve.text

    deadline = time.monotonic() + 60
    order: dict[str, object] = {}
    while time.monotonic() < deadline:
        order = client.get(f"/api/purchase-orders/{order_id}", headers=AUTH).json()
        if order["status"] in ("sent", "failed"):
            break
        time.sleep(2)

    assert order["status"] == "sent", order
    assert order["dry_run"] is True
    assert str(order["message_text"]).startswith("Olá ")


def test_e2e_data_left_inactive(client):
    """Roda depois do teste acima: nenhum item E2E ativo sobra na demo."""
    active = client.get("/api/products", headers=AUTH).raise_for_status().json()
    assert not [p for p in active if p["sku"].startswith("E2E-")]
