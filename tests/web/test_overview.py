import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from giro.inventory.schemas import MovementIn
from giro.inventory.service import record_movement


def test_overview_renders_rows_and_kpis(client, make_product):
    make_product(name="Arroz 5kg", current_stock=Decimal(2), min_stock=Decimal(10))
    html = client.get("/").text
    assert "Arroz 5kg" in html
    assert "🔴 Abaixo do mínimo" in html
    for label in ("Abaixo do mínimo", "Saída hoje", "Saída no mês", "Aguardando aprovação"):
        assert label in html


def test_overview_no_forecast_label(client, make_product):
    make_product(name="Sal", current_stock=Decimal(50), min_stock=Decimal(5))
    assert "sem previsão" in client.get("/").text


def test_overview_shows_next_purchase_date(client, session, make_product):
    p = make_product(name="Café", current_stock=Decimal(200), min_stock=Decimal(10))
    now = datetime.now(UTC)
    for d in range(30):
        record_movement(
            session,
            MovementIn(
                product_id=p.id,
                kind="sale",
                quantity=Decimal(3),
                occurred_at=now - timedelta(days=d),
            ),
        )
    html = client.get("/").text
    assert re.search(r"\d{2}/\d{2}", html)
    assert "🟡 Comprar em breve" in html or "🟢 OK" in html


def test_table_partial_filters(client, make_product):
    make_product(name="Arroz 5kg")
    make_product(name="Óleo 900ml")
    html = client.get("/products/table?q=arr").text
    assert "Arroz 5kg" in html
    assert "Óleo 900ml" not in html
    assert "<html" not in html


def test_chart_series_embedded(client, make_product):
    make_product()
    html = client.get("/").text
    assert 'id="sales-chart"' in html
    match = re.search(r'<script id="sales-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    assert len(json.loads(match.group(1))["labels"]) == 30


def _complete_sale(session, product, *, unit_cost, demo=False):
    from giro.models import CashSession, PaymentMethod, Sale, SaleItem, SalePayment, SaleStatus

    cs = CashSession(opening_amount=Decimal(0), is_demo=demo)
    session.add(cs)
    session.flush()
    session.add(
        Sale(
            session_id=cs.id,
            status=SaleStatus.COMPLETED,
            total=Decimal("20.00"),
            change_given=Decimal(0),
            completed_at=datetime.now(UTC),
            items=[
                SaleItem(
                    product_id=product.id,
                    quantity=Decimal(2),
                    unit_price=Decimal("10.00"),
                    unit_cost=unit_cost,
                    line_total=Decimal("20.00"),
                )
            ],
            payments=[SalePayment(method=PaymentMethod.PIX, amount=Decimal("20.00"))],
        )
    )
    session.flush()


def test_overview_sales_strip_with_sale(client, session, make_product):
    _complete_sale(session, make_product(), unit_cost=Decimal("6.00"))
    html = client.get("/").text
    for label in (
        "Vendas",
        "Faturamento hoje",
        "Faturamento no mês",
        "Ticket médio",
        "Margem bruta",
    ):
        assert label in html
    assert "R$ 20,00" in html
    assert "R$ 8,00" in html
    assert "sobre 1 de 1 itens com custo" in html
    assert 'id="payments-chart"' in html
    assert 'aria-label="Vendas por forma de pagamento no mês"' in html
    match = re.search(
        r'<script id="payments-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert match
    data = json.loads(match.group(1))
    assert data["labels"] == ["Dinheiro", "Pix", "Crédito", "Débito"]
    assert data["values"] == [0, 20, 0, 0]


def test_overview_sales_strip_without_sales(client, make_product):
    make_product()
    html = client.get("/").text
    assert "R$ 0,00" in html
    assert "sem vendas no mês" in html
    assert "sem custo cadastrado" in html
    assert "Sem vendas no mês." in html
    assert 'id="payments-chart"' not in html


def test_overview_margin_without_cost(client, session, make_product):
    _complete_sale(session, make_product(), unit_cost=None)
    html = client.get("/").text
    assert "sem custo cadastrado" in html
    assert "R$ 20,00" in html


def test_overview_labels_demo_sales(client, session, make_product):
    _complete_sale(session, make_product(), unit_cost=None, demo=True)
    assert "Inclui vendas de demonstração." in client.get("/").text


def test_overview_no_demo_label_without_demo_sales(client, session, make_product):
    _complete_sale(session, make_product(), unit_cost=None)
    assert "Inclui vendas de demonstração." not in client.get("/").text
