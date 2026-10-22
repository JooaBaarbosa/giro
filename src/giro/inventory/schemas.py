import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from giro.models import MovementKind, Unit

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
PHONE_MSG = "Telefone deve estar no formato internacional, ex.: +5511987654321."

Name = Annotated[str, Field(min_length=1, max_length=120)]


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[\s()\-.]", "", raw)
    if not E164.match(phone):
        raise ValueError(PHONE_MSG)
    return phone


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


class ProductUpdate(BaseModel):
    name: Name | None = None
    unit: Unit | None = None
    supplier_id: int | None = None
    min_stock: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=3)] | None = None
    cover_days: Annotated[int, Field(ge=1)] | None = None
    active: bool | None = None


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
