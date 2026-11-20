from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Qty = Numeric(12, 3)
Money = Numeric(12, 2)


class Unit(StrEnum):
    UN = "un"
    KG = "kg"
    CX = "cx"


class MovementKind(StrEnum):
    SALE = "sale"
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"
    SALE_CANCEL = "sale_cancel"


class OrderStatus(StrEnum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    RECEIVED = "received"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    CASH = "cash"
    PIX = "pix"
    CREDIT = "credit"
    DEBIT = "debit"


class SaleStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CashMovementKind(StrEnum):
    SUPPLY = "supply"
    WITHDRAWAL = "withdrawal"


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [m.value for m in cls],
        native_enum=False,
        length=20,
    )


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    whatsapp: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("current_stock >= 0", name="ck_products_stock_nonneg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    unit: Mapped[Unit] = mapped_column(_enum(Unit, "unit"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    min_stock: Mapped[Decimal] = mapped_column(Qty)
    cover_days: Mapped[int] = mapped_column(default=15)
    current_stock: Mapped[Decimal] = mapped_column(Qty, default=Decimal(0))
    active: Mapped[bool] = mapped_column(default=True)
    barcode: Mapped[str | None] = mapped_column(String(14))
    sale_price: Mapped[Decimal | None] = mapped_column(Money)
    cost_price: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    supplier: Mapped[Supplier] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (Index("ix_movements_product_time", "product_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    kind: Mapped[MovementKind] = mapped_column(_enum(MovementKind, "movement_kind"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(500))
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped[Product] = relationship()


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"), default=OrderStatus.SUGGESTED
    )
    message_text: Mapped[str | None] = mapped_column(String(4000))
    dry_run: Mapped[bool | None] = mapped_column()
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_alert_dry_run: Mapped[bool | None] = mapped_column()
    owner_alert_error: Mapped[str | None] = mapped_column(String(2000))

    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list[PurchaseOrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderItem.id",
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)

    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class CashSession(Base):
    __tablename__ = "cash_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opening_amount: Mapped[Decimal] = mapped_column(Money)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_amount: Mapped[Decimal | None] = mapped_column(Money)
    counted_amount: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    close_note: Mapped[str | None] = mapped_column(String(500))
    is_demo: Mapped[bool] = mapped_column(default=False)

    sales: Mapped[list[Sale]] = relationship(back_populates="session")
    movements: Mapped[list[CashMovement]] = relationship(back_populates="session")


class CashMovement(Base):
    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cash_sessions.id"))
    kind: Mapped[CashMovementKind] = mapped_column(_enum(CashMovementKind, "cash_movement_kind"))
    amount: Mapped[Decimal] = mapped_column(Money)
    note: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[CashSession] = relationship(back_populates="movements")


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cash_sessions.id"))
    status: Mapped[SaleStatus] = mapped_column(
        _enum(SaleStatus, "sale_status"), default=SaleStatus.OPEN
    )
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    change_given: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))

    session: Mapped[CashSession] = relationship(back_populates="sales")
    items: Mapped[list[SaleItem]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", order_by="SaleItem.id"
    )
    payments: Mapped[list[SalePayment]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", order_by="SalePayment.id"
    )


class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)

    sale: Mapped[Sale] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class SalePayment(Base):
    __tablename__ = "sale_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"))
    method: Mapped[PaymentMethod] = mapped_column(_enum(PaymentMethod, "payment_method"))
    amount: Mapped[Decimal] = mapped_column(Money)

    sale: Mapped[Sale] = relationship(back_populates="payments")
