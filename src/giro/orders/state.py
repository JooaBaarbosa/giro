"""Máquina de estados do pedido de compra (spec §3)."""

from collections.abc import Mapping

from giro.errors import InvalidTransition
from giro.models import OrderStatus as S

TRANSITIONS: Mapping[S, frozenset[S]] = {
    S.SUGGESTED: frozenset({S.APPROVED, S.CANCELLED}),
    S.APPROVED: frozenset({S.SENDING, S.CANCELLED}),
    S.SENDING: frozenset({S.SENT, S.FAILED}),
    S.SENT: frozenset({S.RECEIVED}),
    S.FAILED: frozenset({S.APPROVED, S.CANCELLED}),
    S.RECEIVED: frozenset(),
    S.CANCELLED: frozenset(),
}

OPEN_STATUSES: frozenset[S] = frozenset({S.SUGGESTED, S.APPROVED, S.SENDING, S.SENT, S.FAILED})

STATUS_LABELS: Mapping[S, str] = {
    S.SUGGESTED: "sugerido",
    S.APPROVED: "aprovado",
    S.SENDING: "enviando",
    S.SENT: "enviado",
    S.RECEIVED: "recebido",
    S.FAILED: "falhou",
    S.CANCELLED: "cancelado",
}


def invalid_transition(current: S, target: S) -> InvalidTransition:
    return InvalidTransition(
        f"Pedido não pode passar de {STATUS_LABELS[current]} para {STATUS_LABELS[target]}."
    )


def ensure_transition(current: S, target: S) -> None:
    if target not in TRANSITIONS[current]:
        raise invalid_transition(current, target)
