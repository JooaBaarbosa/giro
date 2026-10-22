from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from giro.errors import (
    DomainError,
    DuplicateSku,
    EmptyOrder,
    InsufficientStock,
    InvalidInput,
    InvalidTransition,
    NotFound,
)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    InvalidInput: 422,
    DuplicateSku: 422,
    InsufficientStock: 409,
    InvalidTransition: 409,
    EmptyOrder: 409,
    NotFound: 404,
}


def status_for(exc: DomainError) -> int:
    return STATUS_BY_ERROR.get(type(exc), 400)


BACK_LINKS = {"/orders": "Pedidos", "/products": "Produtos", "/suppliers": "Fornecedores"}


async def _domain_error_handler(request: Request, exc: Exception) -> Response:
    """JSON para a API; página em pt-BR para as telas."""
    assert isinstance(exc, DomainError)
    status = status_for(exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.message}, status_code=status)

    from giro.web.templating import render  # import tardio: web depende da api, não o contrário

    prefix = "/" + request.url.path.strip("/").split("/")[0]
    back_url, back_label = (
        (prefix, BACK_LINKS[prefix]) if prefix in BACK_LINKS else ("/", "Visão geral")
    )
    response = render(
        request, "error.html", message=exc.message, back_url=back_url, back_label=back_label
    )
    response.status_code = status
    return response


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
