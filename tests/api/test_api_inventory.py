from decimal import Decimal


def test_requires_token(client):
    assert client.get("/api/products").status_code == 401
    r = client.get("/api/products", headers={"X-Service-Token": "x"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Token de serviço inválido."}


def test_create_supplier_and_product(client, auth):
    r = client.post("/api/suppliers", headers=auth, json={"name": "Dist", "whatsapp": "+55 11 98765-4321"})
    assert r.status_code == 201
    sup = r.json()
    assert sup["whatsapp"] == "+5511987654321"
    r = client.post(
        "/api/products",
        headers=auth,
        json={"sku": "A1", "name": "Arroz", "unit": "un", "supplier_id": sup["id"], "min_stock": "5"},
    )
    assert r.status_code == 201
    assert Decimal(r.json()["current_stock"]) == 0
    assert [p["sku"] for p in client.get("/api/products", headers=auth).json()] == ["A1"]


def test_duplicate_sku_is_422(client, auth, make_product):
    p = make_product(sku="DUP")
    r = client.post(
        "/api/products",
        headers=auth,
        json={"sku": "DUP", "name": "X", "unit": "un", "supplier_id": p.supplier_id, "min_stock": "1"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "SKU já cadastrado."


def test_invalid_phone_is_422(client, auth):
    r = client.post("/api/suppliers", headers=auth, json={"name": "X", "whatsapp": "123"})
    assert r.status_code == 422


def test_movement_created(client, auth, make_product):
    p = make_product(current_stock=Decimal(5))
    r = client.post("/api/movements", headers=auth, json={"product_id": p.id, "kind": "sale", "quantity": "2"})
    assert r.status_code == 201
    assert r.json()["kind"] == "sale"
    movs = client.get(f"/api/products/{p.id}/movements", headers=auth).json()
    assert len(movs) == 1


def test_movement_insufficient_is_409(client, auth, make_product):
    p = make_product(current_stock=Decimal(1))
    r = client.post("/api/movements", headers=auth, json={"product_id": p.id, "kind": "sale", "quantity": "2"})
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Estoque insuficiente")


def test_unknown_product_is_404(client, auth):
    r = client.patch("/api/products/999", headers=auth, json={"min_stock": "3"})
    assert r.status_code == 404


def test_patch_product(client, auth, make_product):
    p = make_product()
    r = client.patch(f"/api/products/{p.id}", headers=auth, json={"min_stock": "3"})
    assert r.status_code == 200
    assert Decimal(r.json()["min_stock"]) == 3
