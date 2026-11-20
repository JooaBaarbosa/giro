import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from giro.models import MovementKind, Unit
from giro.pos.codes import is_valid_gtin

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
PHONE_MSG = "Telefone deve estar no formato internacional, ex.: +5511987654321."

Name = Annotated[str, Field(min_length=1, max_length=120)]
SalePrice = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
CostPrice = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
BARCODE_MSG = "Código de barras inválido (EAN-8, UPC-A ou EAN-13)."


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[\s()\-.]", "", raw)
    if not E164.match(phone):
        raise ValueError(PHONE_MSG)
    return phone


def normalize_barcode(raw: str | None) -> str | None:
    if raw is None:
        return None
    code = raw.strip()
    if not code:
        return None
    if not is_valid_gtin(code):
        raise ValueError(BARCODE_MSG)
    return code


class SupplierIn(BaseModel):
    name: Name
    whatsapp: str
    active: bool = True

    _phone = field_validator("whatsapp")(normalize_phone)


class SupplierUpdate(BaseModel):
    name: Name | None = None
    whatsapp: str | None = None
    active: bool | None = None

    @field_validator("whatsapp")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return None if v is None else normalize_phone(v)


class ProductIn(BaseModel):
    sku: Annotated[str, Field(min_length=1, max_length=40)]
    name: Name
    unit: Unit
    supplier_id: int
    min_stock: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=3)]
    cover_days: Annotated[int, Field(ge=1)] = 15
    barcode: str | None = None
    sale_price: SalePrice | None = None
    cost_price: CostPrice | None = None

    _barcode = field_validator("barcode")(normalize_barcode)


class ProductUpdate(BaseModel):
    name: Name | None = None
    unit: Unit | None = None
    supplier_id: int | None = None
    min_stock: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=3)] | None = None
    cover_days: Annotated[int, Field(ge=1)] | None = None
    active: bool | None = None
    barcode: str | None = None
    sale_price: SalePrice | None = None
    cost_price: CostPrice | None = None

    _barcode = field_validator("barcode")(normalize_barcode)


class MovementIn(BaseModel):
    product_id: int
    kind: MovementKind
    quantity: Annotated[Decimal, Field(max_digits=12, decimal_places=3)]
    occurred_at: datetime | None = None
    note: Annotated[str, Field(max_length=500)] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind is MovementKind.ADJUSTMENT:
            if self.quantity == 0:
                raise ValueError("Ajuste não pode ser zero.")
        elif self.quantity <= 0:
            raise ValueError("Quantidade deve ser maior que zero.")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("Data/hora deve ter fuso horário.")
        return self


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    whatsapp: str
    active: bool


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    unit: Unit
    supplier_id: int
    min_stock: Decimal
    cover_days: int
    current_stock: Decimal
    active: bool
    barcode: str | None
    sale_price: Decimal | None
    cost_price: Decimal | None


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    kind: MovementKind
    quantity: Decimal
    occurred_at: datetime
    note: str | None
    purchase_order_id: int | None
