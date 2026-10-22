from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from giro.models import OrderStatus, Unit


class SupplierRef(BaseModel):
    id: int
    name: str
    whatsapp: str
    whatsapp_digits: str


class OrderItemOut(BaseModel):
    id: int
    product_id: int
    name: str
    unit: Unit
    quantity: Decimal


class OrderOut(BaseModel):
    id: int
    status: OrderStatus
    supplier: SupplierRef
    items: list[OrderItemOut]
    message_preview: str
    template_params: list[str]
    dry_run: bool | None
    message_text: str | None
    whatsapp_message_id: str | None
    error: str | None
    created_at: datetime | None
    approved_at: datetime | None
    claimed_at: datetime | None
    sent_at: datetime | None
    received_at: datetime | None
    owner_alerted_at: datetime | None
    owner_alert_dry_run: bool | None
    owner_alert_error: str | None
    link: str


class SuggestedOrderOut(OrderOut):
    alert_params: list[str]


class SentIn(BaseModel):
    message_text: Annotated[str, Field(max_length=4000)]
    dry_run: bool
    whatsapp_message_id: Annotated[str, Field(max_length=200)] | None = None


class FailedIn(BaseModel):
    error: Annotated[str, Field(min_length=1)]


class OwnerAlertIn(BaseModel):
    dry_run: bool
    error: str | None = None


class ItemQtyIn(BaseModel):
    quantity: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]
