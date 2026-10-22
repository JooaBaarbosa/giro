from fastapi import APIRouter
from sqlalchemy import text

from giro.api.deps import SessionDep

router = APIRouter(include_in_schema=False)


@router.get("/healthz")
def healthz(s: SessionDep) -> dict[str, str]:
    s.execute(text("SELECT 1"))
    return {"status": "ok"}
