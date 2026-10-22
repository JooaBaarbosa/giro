import itertools

import pytest

from giro.errors import InvalidTransition
from giro.models import OrderStatus
from giro.orders.state import OPEN_STATUSES, ensure_transition

VALID = [
    ("suggested", "approved"),
    ("suggested", "cancelled"),
    ("approved", "sending"),
    ("approved", "cancelled"),
    ("sending", "sent"),
    ("sending", "failed"),
    ("sent", "received"),
    ("failed", "approved"),
    ("failed", "cancelled"),
]


@pytest.mark.parametrize(("a", "b"), VALID)
def test_valid(a, b):
    ensure_transition(OrderStatus(a), OrderStatus(b))


@pytest.mark.parametrize(
    ("a", "b"),
    [p for p in itertools.product(OrderStatus, repeat=2) if (p[0].value, p[1].value) not in VALID],
)
def test_invalid(a, b):
    with pytest.raises(InvalidTransition):
        ensure_transition(a, b)


def test_message_in_portuguese():
    with pytest.raises(InvalidTransition) as e:
        ensure_transition(OrderStatus.SENT, OrderStatus.CANCELLED)
    assert e.value.message == "Pedido não pode passar de enviado para cancelado."


def test_open_statuses():
    assert OPEN_STATUSES == {
        OrderStatus.SUGGESTED,
        OrderStatus.APPROVED,
        OrderStatus.SENDING,
        OrderStatus.SENT,
        OrderStatus.FAILED,
    }
