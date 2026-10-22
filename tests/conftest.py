import itertools
import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from giro.config import get_settings
from giro.db import make_engine
from giro.models import Product, Supplier, Unit

TABLES = "purchase_order_items, purchase_orders, stock_movements, products, suppliers"

os.environ.setdefault("SERVICE_TOKEN", "test-token")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver="psycopg") as pg:
        url = pg.get_connection_url()
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()
        yield url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[sa.Engine]:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    eng = make_engine(database_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: sa.Engine) -> Iterator[Session]:
    s = Session(engine, expire_on_commit=False)
    yield s
    s.rollback()
    s.close()
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


_seq = itertools.count(1)


@pytest.fixture
def make_supplier(session: Session) -> Callable[..., Supplier]:
    def factory(**kw: Any) -> Supplier:
        n = next(_seq)
        data: dict[str, Any] = {"name": f"Fornecedor {n}", "whatsapp": f"+55119000{n:05d}"}
        data.update(kw)
        supplier = Supplier(**data)
        session.add(supplier)
        session.flush()
        return supplier

    return factory


@pytest.fixture
def make_product(
    session: Session, make_supplier: Callable[..., Supplier]
) -> Callable[..., Product]:
    def factory(**kw: Any) -> Product:
        n = next(_seq)
        data: dict[str, Any] = {
            "sku": f"SKU{n}",
            "name": f"Produto {n}",
            "unit": Unit.UN,
            "min_stock": Decimal(10),
            "cover_days": 15,
            "current_stock": Decimal(0),
        }
        data.update(kw)
        if "supplier" not in data and "supplier_id" not in data:
            data["supplier"] = make_supplier()
        product = Product(**data)
        session.add(product)
        session.flush()
        return product

    return factory


TEST_SETTINGS = {
    "service_token": "test-token",
    "app_base_url": "http://testserver",
    "store_name": "Loja Teste",
    "n8n_webhook_url": "http://n8n.invalid/webhook",
}


@pytest.fixture
def settings(database_url: str) -> Any:
    from giro.config import Settings

    return Settings(database_url=database_url, **TEST_SETTINGS)


@pytest.fixture
def client(session: Session, settings: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from giro.db import get_session
    from giro.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-Service-Token": "test-token"}
