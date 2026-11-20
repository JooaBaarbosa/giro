from fastapi import APIRouter, Depends, Response, status

from giro.api.deps import NowDep, SessionDep, require_service_token
from giro.inventory.schemas import MovementOut
from giro.models import CashMovement, CashSession, Sale, SaleItem, SalePayment, StockMovement
from giro.pos import service
from giro.pos.schemas import (
    AddItemIn,
    AdjustShortageIn,
    CancelSaleIn,
    CashMovementIn,
    CashMovementOut,
    CashSessionOut,
    CloseSessionIn,
    CompleteSaleIn,
    ItemQtyIn,
    OpenSessionIn,
    PaymentIn,
    SaleItemOut,
    SaleOut,
    SalePaymentOut,
    SessionSummaryOut,
)

router = APIRouter(prefix="/api/pos", dependencies=[Depends(require_service_token)])


@router.post("/session", response_model=CashSessionOut, status_code=status.HTTP_201_CREATED)
def open_session(data: OpenSessionIn, s: SessionDep, now: NowDep) -> CashSession:
    session = service.open_session(s, data, now=now)
    s.commit()
    return session


@router.get("/session", response_model=CashSessionOut | None)
def get_current_session(s: SessionDep) -> CashSession | None:
    return service.current_session(s)


@router.post(
    "/session/movements", response_model=CashMovementOut, status_code=status.HTTP_201_CREATED
)
def add_cash_movement(data: CashMovementIn, s: SessionDep, now: NowDep) -> CashMovement:
    movement = service.add_cash_movement(s, data, now=now)
    s.commit()
    return movement


@router.get("/session/summary", response_model=SessionSummaryOut)
def session_summary(s: SessionDep) -> service.SessionSummary:
    session = service.require_session(s)
    return service.session_summary(s, session.id)


@router.post("/session/close", response_model=CashSessionOut)
def close_session(data: CloseSessionIn, s: SessionDep, now: NowDep) -> CashSession:
    session = service.close_session(s, data, now=now)
    s.commit()
    return session


@router.get("/sale", response_model=SaleOut | None)
def get_current_sale(s: SessionDep) -> Sale | None:
    return service.current_sale(s)


@router.post("/sale/items", response_model=SaleItemOut, status_code=status.HTTP_201_CREATED)
def add_item(data: AddItemIn, s: SessionDep, now: NowDep) -> SaleItem:
    item = service.add_item(s, data, now=now)
    s.commit()
    return item


@router.patch("/sale/items/{item_id}", response_model=SaleItemOut)
def set_item_quantity(item_id: int, data: ItemQtyIn, s: SessionDep) -> SaleItem:
    item = service.set_item_quantity(s, item_id, data)
    s.commit()
    return item


@router.delete("/sale/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(item_id: int, s: SessionDep) -> Response:
    service.remove_item(s, item_id)
    s.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sale", status_code=status.HTTP_204_NO_CONTENT)
def discard_sale(s: SessionDep) -> Response:
    service.discard_sale(s)
    s.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sale/payments", response_model=SalePaymentOut, status_code=status.HTTP_201_CREATED)
def add_payment(data: PaymentIn, s: SessionDep) -> SalePayment:
    payment = service.add_payment(s, data)
    s.commit()
    return payment


@router.delete("/sale/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_payment(payment_id: int, s: SessionDep) -> Response:
    service.remove_payment(s, payment_id)
    s.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sale/complete", response_model=SaleOut)
def complete_sale(data: CompleteSaleIn, s: SessionDep, now: NowDep) -> Sale:
    sale = service.complete_sale(s, now=now, sale_id=data.sale_id)
    s.commit()
    return sale


@router.post("/sale/adjust", response_model=MovementOut)
def adjust_for_shortage(data: AdjustShortageIn, s: SessionDep, now: NowDep) -> StockMovement:
    movement = service.adjust_for_shortage(s, data, now=now)
    s.commit()
    return movement


@router.get("/sales/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: int, s: SessionDep) -> Sale:
    return service.get_sale(s, sale_id)


@router.post("/sales/{sale_id}/cancel", response_model=SaleOut)
def cancel_sale(sale_id: int, data: CancelSaleIn, s: SessionDep, now: NowDep) -> Sale:
    sale = service.cancel_sale(s, sale_id, data, now=now)
    s.commit()
    return sale
