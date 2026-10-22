from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from giro.api import health
from giro.api import inventory as api_inventory
from giro.api import orders as api_orders
from giro.api.errors import register_error_handlers
from giro.logging_setup import configure_logging
from giro.web import routes as web_routes

STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Giro", description="Estoque com pedidos automáticos via WhatsApp")
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(api_inventory.router)
    app.include_router(api_orders.router)
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
