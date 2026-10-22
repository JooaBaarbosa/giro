def test_missing_order_page_is_html_404(client):
    r = client.get("/orders/999")
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "Pedido não encontrado." in r.text
    assert 'href="/orders"' in r.text or 'href="/"' in r.text


def test_missing_product_and_supplier_pages_are_html(client):
    for url, msg in (
        ("/products/999", "Produto não encontrado."),
        ("/suppliers/999", "Fornecedor não encontrado."),
    ):
        r = client.get(url)
        assert r.status_code == 404
        assert msg in r.text and "<html" in r.text


def test_api_errors_stay_json(client, auth):
    r = client.get("/api/purchase-orders/999", headers=auth)
    assert r.status_code == 404
    assert r.json() == {"detail": "Pedido não encontrado."}


def test_cancel_asks_for_confirmation(client, suggested_order, notified):
    html = client.get(f"/orders/{suggested_order.id}").text
    assert f'hx-confirm="Cancelar o pedido #{suggested_order.id}?"' in html
