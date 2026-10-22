class DomainError(Exception):
    """Erro de regra de negócio com mensagem pronta para o usuário (pt-BR)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidInput(DomainError):
    pass


class DuplicateSku(DomainError):
    pass


class InsufficientStock(DomainError):
    pass


class InvalidTransition(DomainError):
    pass


class EmptyOrder(DomainError):
    pass


class NotFound(DomainError):
    pass
