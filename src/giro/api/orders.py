from fastapi import APIRouter, Depends

from giro.api.deps import NowDep, SessionDep, SettingsDep, require_service_token
from giro.models import OrderStatus
from giro.orders import service
from giro.orders.schemas import FailedIn, OrderOut, OwnerAlertIn, SentIn, SuggestedOrderOut

router = APIRouter(prefix="/api/purchase-orders", dependencies=[Depends(require_service_token)])


@router.post("/suggest", response_model=list[SuggestedOrderOut])
def suggest(s: SessionDep, settings: SettingsDep, now: NowDep) -> list[SuggestedOrderOut]:
    orders = service.suggest_orders(s, now=now, tz=settings.store_tz)
    s.commit()
    return [service.to_suggested_out(o, settings=settings) for o in orders]


@router.get("", response_model=list[OrderOut])
def list_orders(
    s: SessionDep, settings: SettingsDep, status: OrderStatus | None = None
) -> list[OrderOut]:
    return [service.to_out(o, settings=settings) for o in service.list_orders(s, status)]


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, s: SessionDep, settings: SettingsDep) -> OrderOut:
    return service.to_out(service.get_order(s, order_id), settings=settings)


@router.post("/{order_id}/claim", response_model=OrderOut)
def claim(order_id: int, s: SessionDep, settings: SettingsDep, now: NowDep) -> OrderOut:
    order = service.claim(s, order_id, now=now)
    s.commit()
    return service.to_out(order, settings=settings)


@router.post("/{order_id}/sent", response_model=OrderOut)
def sent(
    order_id: int, data: SentIn, s: SessionDep, settings: SettingsDep, now: NowDep
) -> OrderOut:
    order = service.mark_sent(s, order_id, data, now=now)
    s.commit()
    return service.to_out(order, settings=settings)


@router.post("/{order_id}/failed", response_model=OrderOut)
def failed(order_id: int, data: FailedIn, s: SessionDep, settings: SettingsDep) -> OrderOut:
    order = service.mark_failed(s, order_id, data)
    s.commit()
    return service.to_out(order, settings=settings)


@router.post("/{order_id}/owner-alert", response_model=OrderOut)
def owner_alert(
    order_id: int, data: OwnerAlertIn, s: SessionDep, settings: SettingsDep, now: NowDep
) -> OrderOut:
    order = service.record_owner_alert(s, order_id, data, now=now)
    s.commit()
    return service.to_out(order, settings=settings)
