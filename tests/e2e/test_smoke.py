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


def _suggested_order_id(client: httpx.Client) -> int:
    """Cria um produto zerado (abaixo do mínimo) com fornecedor próprio e devolve o pedido
    sugerido para ele — o teste não depende do estado deixado pelo seed."""
    suffix = uuid.uuid4().hex[:8]
    supplier = client.post(
        "/api/suppliers",
        headers=AUTH,
        json={"name": f"Fornecedor E2E {suffix}", "whatsapp": "+5511900009999"},
    )
    supplier.raise_for_status()
    product = client.post(
        "/api/products",
        headers=AUTH,
        json={
            "sku": f"E2E-{suffix}",
            "name": f"Item E2E {suffix}",
            "unit": "un",
            "supplier_id": supplier.json()["id"],
            "min_stock": "5",
        },
    )
    product.raise_for_status()
    created = client.post("/api/purchase-orders/suggest", headers=AUTH)
    created.raise_for_status()
    ours = [o for o in created.json() if o["supplier"]["id"] == supplier.json()["id"]]
    assert len(ours) == 1, created.json()
    return int(ours[0]["id"])


def test_approved_order_is_sent_in_dry_run():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        order_id = _suggested_order_id(client)

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
