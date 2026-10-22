from fastapi import FastAPI

from giro.api import inventory as api_inventory
from giro.api import orders as api_orders
from giro.api.errors import register_error_handlers
from giro.logging_setup import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Giro", description="Estoque com pedidos automáticos via WhatsApp")
    register_error_handlers(app)
    app.include_router(api_inventory.router)
    app.include_router(api_orders.router)
    return app
