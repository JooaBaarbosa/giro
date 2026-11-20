from datetime import UTC, datetime
from decimal import Decimal

import pytest

from giro.models import CashSession, SaleStatus, Unit
from giro.pos import service
from giro.pos.schemas import OpenSessionIn

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


@pytest.fixture
def open_cash(session):
    service.open_session(session, OpenSessionIn(opening_amount=Decimal("100")), now=NOW)
    session.commit()


@pytest.fixture
def arroz(make_product, session):
    p = make_product(
        sku="ARR5",
        name="Arroz 5kg",
        barcode="7891000100103",
        sale_price=Decimal("25.00"),
        current_stock=Decimal(10),
    )
    session.commit()
    return p


@pytest.fixture
def tomate(make_product, session):
    p = make_product(
        sku="TOM",
        name="Tomate",
        unit=Unit.KG,
        sale_price=Decimal("8.00"),
        current_stock=Decimal(9),
    )
    session.commit()
    return p


def _add(client, entry, **extra):
    return client.post("/caixa/itens", data={"entry": entry, **extra})


def test_menu_has_caixa_first(client):
    html = client.get("/caixa").text
    nav = html.split("<nav>")[1].split("</nav>")[0]
    assert nav.strip().startswith('<a href="/caixa"')
    assert nav.index("Caixa") < nav.index("Visão geral")


def test_get_without_session_shows_open_form(client):
    r = client.get("/caixa")
    assert r.status_code == 200
    assert "Abrir caixa" in r.text
    assert 'id="entry"' not in r.text


def test_open_session_then_pdv(client):
    r = client.post("/caixa/abrir", data={"opening_amount": "50,00"}, follow_redirects=True)
    assert r.status_code == 200
    assert 'id="entry"' in r.text
    assert "autofocus" in r.text


def test_open_session_twice_shows_error(client, open_cash):
    r = client.post("/caixa/abrir", data={"opening_amount": "1"})
    assert "Já existe um caixa aberto." in r.text


def test_pdv_has_focused_entry(client, open_cash):
    html = client.get("/caixa").text
    assert 'id="entry"' in html
    assert "autofocus" in html
    assert "pos.js" in html


def test_unknown_code_message_and_empty_field(client, open_cash):
    r = _add(client, "NAOEXISTE")
    assert r.status_code == 200
    assert "Produto não encontrado" in r.text
    assert 'id="entry"' in r.text
    assert 'value="NAOEXISTE"' not in r.text


def test_invalid_entry_message(client, open_cash):
    r = _add(client, "abc*X")
    assert r.status_code == 200
    assert "Quantidade inválida" in r.text


def test_add_item_with_quantity_prefix(client, open_cash, arroz):
    r = _add(client, "2*ARR5")
    assert "Arroz 5kg" in r.text
    assert "R$ 50,00" in r.text


def test_add_item_by_barcode_defaults_to_one(client, open_cash, arroz):
    r = _add(client, "7891000100103")
    assert "R$ 25,00" in r.text


def test_kg_without_weight_asks_for_weight(client, session, open_cash, tomate):
    r = _add(client, "TOM")
    assert "Peso (kg)" in r.text
    assert 'name="weight"' in r.text
    assert 'name="entry" value="TOM"' in r.text
    assert service.current_sale(session) is None
    r2 = _add(client, "TOM", weight="0,350")
    assert "Tomate" in r2.text
    assert "R$ 2,80" in r2.text
    assert 'name="weight"' not in r2.text


def test_kg_with_inline_quantity_skips_weight(client, open_cash, tomate):
    r = _add(client, "0,5*TOM")
    assert 'name="weight"' not in r.text
    assert "R$ 4,00" in r.text


def test_kg_invalid_weight_keeps_weight_field(client, open_cash, tomate):
    r = _add(client, "TOM", weight="abc")
    assert 'name="weight"' in r.text
    assert 'name="entry" value="TOM"' in r.text


def test_over_stock_warning_on_line(client, open_cash, arroz):
    r = _add(client, "11*ARR5")
    assert "acima do estoque (disponível 10)" in r.text


def test_edit_and_remove_item(client, session, open_cash, arroz):
    _add(client, "ARR5")
    item = service.current_sale(session).items[0]
    r = client.post(f"/caixa/itens/{item.id}", data={"quantity": "3"})
    assert "R$ 75,00" in r.text
    r = client.post(f"/caixa/itens/{item.id}", data={"quantity": "0"})
    assert "R$ 75,00" in r.text
    r = client.post(f"/caixa/itens/{item.id}/remover")
    assert "Arroz 5kg" not in r.text


def test_discard_sale(client, session, open_cash, arroz):
    _add(client, "ARR5")
    r = client.post("/caixa/descartar")
    assert r.status_code == 200
    assert "Arroz 5kg" not in r.text
    assert service.current_sale(session) is None


def test_payment_prefilled_remaining_and_change_preview(client, open_cash, arroz):
    _add(client, "ARR5")
    r = client.post("/caixa/pagamentos", data={"method": "cash", "amount": "10,00"})
    assert "Dinheiro" in r.text
    assert 'value="15,00"' in r.text
    r = client.post("/caixa/pagamentos", data={"method": "cash", "amount": "50,00"})
    assert "Troco R$ 35,00" in r.text


def test_remove_payment(client, session, open_cash, arroz):
    _add(client, "ARR5")
    client.post("/caixa/pagamentos", data={"method": "pix", "amount": "25,00"})
    payment = service.current_sale(session, fresh=True).payments[0]
    r = client.post(f"/caixa/pagamentos/{payment.id}/remover")
    assert 'data-testid="payment-row"' not in r.text
    assert 'value="25,00"' in r.text


def test_pix_overpay_message(client, open_cash, arroz):
    _add(client, "ARR5")
    r = client.post("/caixa/pagamentos", data={"method": "pix", "amount": "30,00"})
    assert "troco só em dinheiro" in r.text


def _sale_id(session):
    return service.current_sale(session, fresh=True).id


def test_complete_sale(client, session, open_cash, arroz):
    _add(client, "ARR5")
    client.post("/caixa/pagamentos", data={"method": "cash", "amount": "30,00"})
    r = client.post("/caixa/concluir", data={"sale_id": _sale_id(session)})
    assert "Venda #1 concluída" in r.text
    assert "troco R$ 5,00" in r.text
    session.refresh(arroz)
    assert arroz.current_stock == 9
    assert 'id="entry"' in r.text


def test_complete_without_payment_shows_error(client, session, open_cash, arroz):
    _add(client, "ARR5")
    r = client.post("/caixa/concluir", data={"sale_id": _sale_id(session)})
    assert "Faltam R$ 25,00" in r.text


def test_complete_without_open_sale(client, open_cash):
    r = client.post("/caixa/concluir")
    assert "Não há venda em andamento." in r.text


def test_shortage_flow_adjust_and_continue(client, session, open_cash, arroz):
    _add(client, "12*ARR5")
    client.post("/caixa/pagamentos", data={"method": "pix", "amount": "300,00"})
    sale_id = _sale_id(session)
    r = client.post("/caixa/concluir", data={"sale_id": sale_id})
    assert "Estoque insuficiente" in r.text
    assert "Ajustar estoque e continuar" in r.text
    session.refresh(arroz)
    assert arroz.current_stock == 10
    r = client.post(
        "/caixa/ajustar", data={"sale_id": sale_id, "product_id": arroz.id, "reason": "x"}
    )
    assert "Ajustar estoque e continuar" in r.text
    r = client.post(
        "/caixa/ajustar",
        data={"sale_id": sale_id, "product_id": arroz.id, "reason": "Contagem errada"},
    )
    assert "Venda #1 concluída" in r.text
    session.refresh(arroz)
    assert arroz.current_stock == 0
    assert service.get_sale(session, sale_id).status is SaleStatus.COMPLETED


def test_cash_movements(client, open_cash):
    r = client.post("/caixa/movimentos", data={"kind": "supply", "amount": "20,00"})
    assert "Suprimento de R$ 20,00 registrado" in r.text
    r = client.post(
        "/caixa/movimentos", data={"kind": "withdrawal", "amount": "50,00", "note": "banco"}
    )
    assert "Sangria de R$ 50,00 registrada" in r.text
    r = client.post("/caixa/movimentos", data={"kind": "withdrawal", "amount": "999,00"})
    assert "Sangria maior que o dinheiro esperado" in r.text


def test_actions_without_session_show_message(client):
    r = _add(client, "X")
    assert r.status_code == 200
    assert "caixa está fechado" in r.text


def test_pos_assets_served(client):
    assert client.get("/static/pos.js").status_code == 200


def _sell(client, session, entry="ARR5", amount="25,00", method="cash"):
    _add(client, entry)
    client.post("/caixa/pagamentos", data={"method": method, "amount": amount})
    client.post("/caixa/concluir", data={"sale_id": _sale_id(session)})


def test_close_without_session_redirects(client):
    r = client.get("/caixa/fechar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/caixa"


def test_close_shows_summary_and_expected(client, session, open_cash, arroz):
    _sell(client, session)
    html = client.get("/caixa/fechar").text
    assert "Esperado em dinheiro" in html
    assert "R$ 125,00" in html
    assert "Dinheiro" in html


@pytest.mark.parametrize(
    ("counted", "expected_text"),
    [("120,00", "Falta R$ 5,00"), ("130,00", "Sobra R$ 5,00"), ("125,00", "Sem diferença")],
)
def test_close_records_difference(client, session, open_cash, arroz, counted, expected_text):
    _sell(client, session)
    r = client.post("/caixa/fechar", data={"counted_amount": counted}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    assert r.headers["location"].startswith("/caixa/turnos/")
    assert expected_text in page.text
    assert "Contado" in page.text


def test_close_with_open_sale_shows_service_message(client, session, open_cash, arroz):
    _add(client, "ARR5")
    r = client.post("/caixa/fechar", data={"counted_amount": "100,00"})
    assert r.status_code == 200
    assert "Conclua ou descarte a venda em andamento" in r.text


def test_close_invalid_amount_shows_error(client, open_cash):
    r = client.post("/caixa/fechar", data={"counted_amount": "abc"})
    assert r.status_code == 200
    assert 'role="alert"' in r.text


def test_sessions_list_marks_demo(client, session, open_cash):
    session.add(CashSession(opening_amount=Decimal(0), is_demo=True, closed_at=NOW))
    session.commit()
    html = client.get("/caixa/turnos").text
    assert "Dados de demonstração" in html
    assert "aberto" in html
    assert "fechado" in html


def test_session_detail_lists_sales_and_links(client, session, open_cash, arroz):
    _sell(client, session)
    sid = service.current_session(session).id
    html = client.get(f"/caixa/turnos/{sid}").text
    assert "/caixa/vendas/1" in html
    assert 'href="/caixa"' in html
    assert "Esperado em dinheiro" in html


def test_unknown_ids_show_not_found(client):
    for url in ("/caixa/turnos/999", "/caixa/vendas/999"):
        r = client.get(url)
        assert r.status_code == 404
        assert "não encontrad" in r.text


def test_sale_detail_and_cancel_returns_stock(client, session, open_cash, arroz):
    _sell(client, session, amount="30,00")
    html = client.get("/caixa/vendas/1").text
    assert "Arroz 5kg" in html
    assert "Dinheiro" in html
    assert "concluída" in html
    assert "Troco" in html
    assert "/caixa/vendas/1/cancelar" in html
    r = client.post("/caixa/vendas/1/cancelar", data={"reason": "ab"})
    assert r.status_code == 200
    assert "alert" in r.text
    session.refresh(arroz)
    assert arroz.current_stock == 9
    r = client.post(
        "/caixa/vendas/1/cancelar", data={"reason": "Cliente desistiu"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/caixa/vendas/1"
    session.refresh(arroz)
    assert arroz.current_stock == 10
    html = client.get("/caixa/vendas/1").text
    assert "cancelada" in html
    assert "Cliente desistiu" in html
    assert "/caixa/vendas/1/cancelar" not in html


def test_sale_of_closed_session_has_no_cancel_form(client, session, open_cash, arroz):
    _sell(client, session)
    client.post("/caixa/fechar", data={"counted_amount": "125,00"})
    assert "/caixa/vendas/1/cancelar" not in client.get("/caixa/vendas/1").text
