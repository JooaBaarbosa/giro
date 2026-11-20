import itertools

import pytest

from giro.errors import InvalidTransition
from giro.models import PaymentMethod, SaleStatus
from giro.pos.state import METHOD_LABELS, STATUS_LABELS, ensure_transition

S = SaleStatus
VALID = [(S.OPEN, S.COMPLETED), (S.COMPLETED, S.CANCELLED)]
INVALID = [p for p in itertools.product(S, S) if p not in VALID]


@pytest.mark.parametrize(("a", "b"), VALID)
def test_valid(a, b):
    ensure_transition(a, b)


@pytest.mark.parametrize(("a", "b"), INVALID)
def test_invalid(a, b):
    with pytest.raises(InvalidTransition):
        ensure_transition(a, b)


def test_message():
    with pytest.raises(InvalidTransition) as exc:
        ensure_transition(S.CANCELLED, S.COMPLETED)
    assert exc.value.message == "Venda não pode passar de cancelada para concluída."


def test_labels():
    assert STATUS_LABELS[S.OPEN] == "em andamento"
    assert METHOD_LABELS[PaymentMethod.CASH] == "Dinheiro"
    assert set(METHOD_LABELS) == set(PaymentMethod)
