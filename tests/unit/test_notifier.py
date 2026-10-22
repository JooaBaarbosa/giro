import httpx

from giro.config import Settings
from giro.notifier import notify_order_approved

SETTINGS = Settings(database_url="x", service_token="t", n8n_webhook_url="http://n8n:5678/webhook/")


def test_notifier_posts_order_id(monkeypatch):
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert notify_order_approved(7, SETTINGS) is True
    assert calls == [("http://n8n:5678/webhook/pedido-aprovado", {"order_id": 7}, 3)]


def test_notifier_swallows_errors(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    assert notify_order_approved(7, SETTINGS) is False


def test_notifier_http_error_returns_false(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Response(500, request=httpx.Request("POST", url))
    )
    assert notify_order_approved(7, SETTINGS) is False
