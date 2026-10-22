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
