"""Fumaça ponta a ponta contra o `docker compose up` (dry-run do WhatsApp).

Rodar: docker compose up -d --build --wait && uv run python -m pytest -m e2e -v

O e2e do caixa deixa no histórico local um turno fechado com a nota "Teste e2e" (não há API para
excluir turnos); os itens e fornecedores criados são desativados.
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


def _fake_ean13() -> str:
    """EAN-13 fictício (prefixo 789) e único por execução, com dígito verificador correto."""
    body = "789" + f"{uuid.uuid4().int % 10**9:09d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


@pytest.fixture
def e2e_pos_product(client, e2e_supplier_product):
    """Produto vendável (preço 10,00, EAN-13 fictício, estoque 10) para o caixa.

    Pula antes de criar qualquer coisa se já houver caixa aberto: sem teardown, o turno e a venda
    em aberto de quem usa o sistema nunca são tocados."""
    if client.get("/api/pos/session", headers=AUTH).json():
        pytest.skip("Já existe um caixa aberto; o e2e não mexe em turno alheio.")
    supplier, _ = e2e_supplier_product
    suffix = uuid.uuid4().hex[:8]
    barcode = _fake_ean13()
    product = (
        client.post(
            "/api/products",
            headers=AUTH,
            json={
                "sku": f"E2E-PDV-{suffix}",
                "name": f"Item PDV E2E {suffix}",
                "unit": "un",
                "supplier_id": supplier["id"],
                "min_stock": "0",
                "barcode": barcode,
                "sale_price": "10.00",
                "cost_price": "6.00",
            },
        )
        .raise_for_status()
        .json()
    )
    client.post(
        "/api/movements",
        headers=AUTH,
        json={"product_id": product["id"], "kind": "purchase", "quantity": "10"},
    ).raise_for_status()
    yield product
    # limpeza: só chega aqui sem caixa alheio (o skip acima), então o turno aberto é do teste
    client.delete("/api/pos/sale", headers=AUTH)
    if client.get("/api/pos/session", headers=AUTH).json():
        client.post("/api/pos/session/close", headers=AUTH, json={"counted_amount": "0"})
    client.patch(f"/api/products/{product['id']}", headers=AUTH, json={"active": False})


def _stock(client, product_id: int) -> str:
    products = client.get("/api/products", headers=AUTH).raise_for_status().json()
    return next(p for p in products if p["id"] == product_id)["current_stock"]


def test_pos_flow_split_payment_cancel_and_close_with_difference(client, e2e_pos_product):
    product = e2e_pos_product
    assert client.get("/caixa").status_code == 200

    client.post(
        "/api/pos/session", headers=AUTH, json={"opening_amount": "50.00"}
    ).raise_for_status()

    # venda 1: 2 itens pelo código de barras, Pix 5,00 + dinheiro 20,00 (troco 5,00)
    client.post(
        "/api/pos/sale/items", headers=AUTH, json={"code": product["barcode"], "quantity": "2"}
    ).raise_for_status()
    client.post(
        "/api/pos/sale/payments", headers=AUTH, json={"method": "pix", "amount": "5.00"}
    ).raise_for_status()
    client.post(
        "/api/pos/sale/payments", headers=AUTH, json={"method": "cash", "amount": "20.00"}
    ).raise_for_status()
    sale = client.get("/api/pos/sale", headers=AUTH).raise_for_status().json()
    done = (
        client.post("/api/pos/sale/complete", headers=AUTH, json={"sale_id": sale["id"]})
        .raise_for_status()
        .json()
    )
    assert done["status"] == "completed"
    assert done["total"] == "20.00"
    assert done["change_given"] == "5.00"
    assert _stock(client, product["id"]) == "8.000"

    # venda 2: 1 item no Pix, depois cancelada (estoque volta)
    client.post(
        "/api/pos/sale/items", headers=AUTH, json={"code": product["sku"]}
    ).raise_for_status()
    client.post(
        "/api/pos/sale/payments", headers=AUTH, json={"method": "pix", "amount": "10.00"}
    ).raise_for_status()
    sale2 = client.get("/api/pos/sale", headers=AUTH).raise_for_status().json()
    client.post(
        "/api/pos/sale/complete", headers=AUTH, json={"sale_id": sale2["id"]}
    ).raise_for_status()
    assert _stock(client, product["id"]) == "7.000"
    cancelled = (
        client.post(
            f"/api/pos/sales/{sale2['id']}/cancel", headers=AUTH, json={"reason": "Teste e2e"}
        )
        .raise_for_status()
        .json()
    )
    assert cancelled["status"] == "cancelled"
    assert _stock(client, product["id"]) == "8.000"

    # fechamento: esperado 50,00 + 20,00 - 5,00 = 65,00; contado 60,00 => diferença -5,00
    summary = client.get("/api/pos/session/summary", headers=AUTH).raise_for_status().json()
    assert summary["expected_cash"] == "65.00"
    closed = (
        client.post(
            "/api/pos/session/close",
            headers=AUTH,
            json={"counted_amount": "60.00", "note": "Teste e2e"},
        )
        .raise_for_status()
        .json()
    )
    assert closed["difference"] == "-5.00"
    assert closed["closed_at"] is not None
    assert client.get("/api/pos/session", headers=AUTH).json() is None


def test_e2e_data_left_inactive(client):
    """Roda depois dos testes acima: nenhum item E2E ativo sobra na demo."""
    active = client.get("/api/products", headers=AUTH).raise_for_status().json()
    assert not [p for p in active if p["sku"].startswith("E2E-")]
