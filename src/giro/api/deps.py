import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from giro.config import Settings, get_settings
from giro.db import get_session

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_now() -> datetime:
    return datetime.now(UTC)


NowDep = Annotated[datetime, Depends(get_now)]


def require_service_token(
    settings: SettingsDep,
    x_service_token: Annotated[str | None, Header()] = None,
) -> None:
    if x_service_token is None or not secrets.compare_digest(
        x_service_token.encode(), settings.service_token.encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de serviço inválido.")
