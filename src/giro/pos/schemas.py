from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from giro.models import CashMovementKind, PaymentMethod, SaleStatus, Unit

Amount = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]
NonNegAmount = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
PositiveQty = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]
Reason = Annotated[str, Field(min_length=3, max_length=500)]
Note500 = Annotated[str, Field(max_length=500)]


class OpenSessionIn(BaseModel):
    opening_amount: NonNegAmount


class CashMovementIn(BaseModel):
    kind: CashMovementKind
    amount: PositiveAmount
    note: Note500 | None = None


class CloseSessionIn(BaseModel):
    counted_amount: NonNegAmount
    note: Note500 | None = None


class AddItemIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: Annotated[str, Field(min_length=1, max_length=60)]
    quantity: PositiveQty | None = None


class ItemQtyIn(BaseModel):
    quantity: PositiveQty


class PaymentIn(BaseModel):
    method: PaymentMethod
    amount: Amount


class CancelSaleIn(BaseModel):
    reason: Reason


class AdjustShortageIn(BaseModel):
    product_id: int
    reason: Reason


class CashMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: CashMovementKind
    amount: Decimal
    note: str | None
    occurred_at: datetime


class CashSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    opened_at: datetime
    opening_amount: Decimal
    closed_at: datetime | None
    expected_amount: Decimal | None
    counted_amount: Decimal | None
    difference: Decimal | None
    close_note: str | None
    is_demo: bool


class SaleItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    name: str = Field(validation_alias=AliasPath("product", "name"))
    unit: Unit = Field(validation_alias=AliasPath("product", "unit"))
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class SalePaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method: PaymentMethod
    amount: Decimal


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    status: SaleStatus
    total: Decimal
    change_given: Decimal
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    items: list[SaleItemOut]
    payments: list[SalePaymentOut]


class SessionSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    by_method: dict[PaymentMethod, Decimal]
    sales_count: int
    sales_total: Decimal
    cancelled_count: int
    supplies: Decimal
    withdrawals: Decimal
    expected_cash: Decimal
