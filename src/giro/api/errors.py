from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse({"detail": exc.message}, status_code=status_for(exc))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
