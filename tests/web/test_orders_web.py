from decimal import Decimal

from giro.models import OrderStatus


def test_orders_page_lists_by_status(client, suggested_order, notified):
    html = client.get("/orders?status=suggested").text
    assert "Dist A" in html
    assert "Olá Dist A, aqui é da Loja Teste." in html  # prévia da mensagem
    assert "Aprovar" in html


def test_approve_calls_notifier_once(client, session, suggested_order, notified):
    oid = suggested_order.id
    first = client.post(f"/orders/{oid}/approve")
    assert "Aprovado — aguardando envio" in first.text
    second = client.post(f"/orders/{oid}/approve")
    assert notified == [oid]
    assert "Pedido não pode passar de aprovado para aprovado." in second.text
    session.refresh(suggested_order)
    assert suggested_order.status is OrderStatus.APPROVED


def test_approve_with_n8n_down_shows_waiting(client, suggested_order, monkeypatch):
    monkeypatch.setattr("giro.web.orders.notify_order_approved", lambda oid, s: False)
    r = client.post(f"/orders/{suggested_order.id}/approve")
    assert "será reenviado automaticamente" in r.text


def test_edit_item_quantity(client, session, suggested_order, notified):
    item = suggested_order.items[0]
    r = client.post(f"/orders/{suggested_order.id}/items/{item.id}", data={"quantity": "12"})
    assert r.status_code == 200
    assert "Arroz: 12 un" in r.text
    session.refresh(item)
    assert item.quantity == 12


def test_edit_item_invalid_quantity_keeps_item(client, session, suggested_order, notified):
    item = suggested_order.items[0]
    before = item.quantity
    for bad in ("0", "-3", "2,5"):
        r = client.post(f"/orders/{suggested_order.id}/items/{item.id}", data={"quantity": bad})
        assert "field-error" in r.text or "flash-error" in r.text
    session.refresh(item)
    assert item.quantity == before


def test_remove_item_then_approve_refused(client, suggested_order, notified):
    item = suggested_order.items[0]
    client.post(f"/orders/{suggested_order.id}/items/{item.id}/delete")
    r = client.post(f"/orders/{suggested_order.id}/approve")
    assert "Pedido sem itens não pode ser aprovado." in r.text
    assert notified == []


def test_sent_dry_run_shows_badge_and_message(client, sent_dry_run_order, notified):
    html = client.get(f"/orders/{sent_dry_run_order.id}").text
    assert "simulado" in html
    assert "Olá Dist A (teste)" in html
    assert "Marcar recebido" in html


def test_failed_shows_error_and_retry(client, failed_order, notified):
    html = client.get("/orders?status=failed").text
    assert "Recipient not allowed" in html
    assert "Reenviar" in html
    r = client.post(f"/orders/{failed_order.id}/retry")
    assert notified == [failed_order.id]
    assert "aguardando envio" in r.text


def test_receive_updates_stock(client, session, sent_order, notified):
    r = client.post(f"/orders/{sent_order.id}/receive")
    assert "Recebido" in r.text
    product = sent_order.items[0].product
    session.refresh(product)
    assert product.current_stock == Decimal(1) + sent_order.items[0].quantity


def test_cancel(client, session, suggested_order, notified):
    r = client.post(f"/orders/{suggested_order.id}/cancel")
    assert "Cancelado" in r.text
    session.refresh(suggested_order)
    assert suggested_order.status is OrderStatus.CANCELLED


def test_orders_empty_state(client):
    assert "Nenhum pedido" in client.get("/orders").text


def test_order_times_in_store_timezone(client, session, sent_dry_run_order, notified):
    from datetime import UTC, datetime

    sent_dry_run_order.sent_at = datetime(2026, 10, 22, 2, 30, tzinfo=UTC)
    session.commit()
    html = client.get(f"/orders/{sent_dry_run_order.id}").text
    assert "21/10 23:30" in html
