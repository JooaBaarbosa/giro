from dataclasses import dataclass
from decimal import Decimal


class DomainError(Exception):
    """Erro de regra de negócio com mensagem pronta para o usuário (pt-BR)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidInput(DomainError):
    pass


class DuplicateSku(DomainError):
    pass


@dataclass(frozen=True)
class Shortage:
    product_id: int
    name: str
    available: Decimal
    needed: Decimal


class InsufficientStock(DomainError):
    def __init__(self, message: str, shortages: list[Shortage] | None = None) -> None:
        super().__init__(message)
        self.shortages: list[Shortage] = shortages if shortages is not None else []


class InvalidTransition(DomainError):
    pass


class EmptyOrder(DomainError):
    pass


class NotFound(DomainError):
    pass


class NoOpenSession(DomainError):
    pass


class SessionAlreadyOpen(DomainError):
    pass


class PaymentMismatch(DomainError):
    pass
