"""Máquina de estados da venda do caixa."""

from collections.abc import Mapping

from giro.errors import InvalidTransition
from giro.models import PaymentMethod
from giro.models import SaleStatus as S

TRANSITIONS: Mapping[S, frozenset[S]] = {
    S.OPEN: frozenset({S.COMPLETED}),
    S.COMPLETED: frozenset({S.CANCELLED}),
    S.CANCELLED: frozenset(),
}

STATUS_LABELS: Mapping[S, str] = {
    S.OPEN: "em andamento",
    S.COMPLETED: "concluída",
    S.CANCELLED: "cancelada",
}

METHOD_LABELS: Mapping[PaymentMethod, str] = {
    PaymentMethod.CASH: "Dinheiro",
    PaymentMethod.PIX: "Pix",
    PaymentMethod.CREDIT: "Crédito",
    PaymentMethod.DEBIT: "Débito",
}


def ensure_transition(current: S, target: S) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(
            f"Venda não pode passar de {STATUS_LABELS[current]} para {STATUS_LABELS[target]}."
        )
