from datetime import UTC, datetime
from decimal import Decimal

import pytest

from giro.orders.schemas import FailedIn, SentIn
from giro.orders.service import approve, claim, mark_failed, mark_sent, suggest_orders

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
TZ = "America/Sao_Paulo"


@pytest.fixture
def notified(monkeypatch) -> list[int]:
    """Substitui o webhook do n8n e registra os ids notificados."""
    calls: list[int] = []

    def fake(order_id, settings):
        calls.append(order_id)
        return True

    monkeypatch.setattr("giro.web.orders.notify_order_approved", fake)
    return calls


@pytest.fixture
def suggested_order(session, make_supplier, make_product):
    sup = make_supplier(name="Dist A", whatsapp="+5511987654321")
    make_product(name="Arroz", supplier=sup, current_stock=Decimal(1), min_stock=Decimal(5))
    order = suggest_orders(session, now=NOW, tz=TZ)[0]
    session.commit()
    return order


def _to_sending(session, order):
    approve(session, order.id, now=NOW)
    claim(session, order.id, now=NOW)


@pytest.fixture
def sent_order(session, suggested_order):
    _to_sending(session, suggested_order)
    mark_sent(session, suggested_order.id, SentIn(message_text="Olá Dist A", dry_run=False), now=NOW)
    session.commit()
    return suggested_order


@pytest.fixture
def sent_dry_run_order(session, suggested_order):
    _to_sending(session, suggested_order)
    mark_sent(
        session, suggested_order.id, SentIn(message_text="Olá Dist A (teste)", dry_run=True), now=NOW
    )
    session.commit()
    return suggested_order


@pytest.fixture
def failed_order(session, suggested_order):
    _to_sending(session, suggested_order)
    mark_failed(session, suggested_order.id, FailedIn(error="(#131030) Recipient not allowed"))
    session.commit()
    return suggested_order
