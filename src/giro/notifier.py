"""Avisa o n8n que um pedido foi aprovado. Nunca levanta: se falhar, o cron do WF1 recupera."""

import logging

import httpx

from giro.config import Settings

log = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_S = 3


def notify_order_approved(order_id: int, settings: Settings) -> bool:
    url = f"{settings.n8n_webhook_url.rstrip('/')}/pedido-aprovado"
    try:
        response = httpx.post(url, json={"order_id": order_id}, timeout=WEBHOOK_TIMEOUT_S)
        response.raise_for_status()
    except Exception as exc:
        log.warning(
            "n8n indisponível; pedido fica aguardando envio",
            extra={"order_id": order_id, "error": str(exc)},
        )
        return False
    return True
