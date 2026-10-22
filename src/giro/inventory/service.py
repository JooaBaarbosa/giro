"""Cadastros e movimentações de estoque. Nenhuma função faz commit: quem chama decide."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from giro.errors import DuplicateSku, InsufficientStock, InvalidInput, NotFound
from giro.fmt import br_number
from giro.inventory.schemas import (
    MovementIn,
    ProductIn,
    ProductUpdate,
    SupplierIn,
    SupplierUpdate,
)
from giro.models import (
    MovementKind,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    StockMovement,
    Supplier,
    Unit,
)
from giro.orders.state import OPEN_STATUSES


def create_supplier(s: Session, data: SupplierIn) -> Supplier:
    supplier = Supplier(**data.model_dump())
    s.add(supplier)
    s.flush()
    return supplier


def get_supplier(s: Session, supplier_id: int) -> Supplier:
    supplier = s.get(Supplier, supplier_id)
    if supplier is None:
        raise NotFound("Fornecedor não encontrado.")
    return supplier


def update_supplier(s: Session, supplier_id: int, data: SupplierUpdate) -> Supplier:
    supplier = get_supplier(s, supplier_id)
    for key, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(supplier, key, value)
    s.flush()
    return supplier


def list_suppliers(s: Session, *, include_inactive: bool = False) -> list[Supplier]:
    stmt = select(Supplier).order_by(Supplier.name)
    if not include_inactive:
        stmt = stmt.where(Supplier.active)
    return list(s.scalars(stmt))


def create_product(s: Session, data: ProductIn) -> Product:
    if s.scalar(select(Product.id).where(Product.sku == data.sku)) is not None:
        raise DuplicateSku("SKU já cadastrado.")
    get_supplier(s, data.supplier_id)
    product = Product(**data.model_dump(), current_stock=Decimal(0))
    try:
        # savepoint: um cadastro concorrente com o mesmo SKU vira erro de domínio, não 500
        with s.begin_nested():
            s.add(product)
            s.flush()
    except IntegrityError as exc:
        raise DuplicateSku("SKU já cadastrado.") from exc
    return product


def get_product(s: Session, product_id: int) -> Product:
    product = s.get(Product, product_id)
    if product is None:
        raise NotFound("Produto não encontrado.")
    return product


def update_product(s: Session, product_id: int, data: ProductUpdate) -> Product:
    product = get_product(s, product_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "supplier_id" in changes:
        get_supplier(s, changes["supplier_id"])
    if changes.get("unit", product.unit) is not product.unit:
        _ensure_unit_change_allowed(s, product, changes["unit"])
    for key, value in changes.items():
        setattr(product, key, value)
    s.flush()
    return product


def _is_integral(value: Decimal) -> bool:
    return value == value.to_integral_value()


def _ensure_unit_change_allowed(s: Session, product: Product, unit: Unit) -> None:
    """Mudar para un/cx com saldo fracionado deixaria um resto impossível de vender ou ajustar."""
    if unit is Unit.KG:
        return
    if not _is_integral(product.current_stock):
        raise InvalidInput(
            f"Não é possível mudar para {unit.value}: o estoque atual está fracionado "
            f"({br_number(product.current_stock)}). Faça um ajuste antes."
        )
    open_quantities = s.scalars(
        select(PurchaseOrderItem.quantity)
        .join(PurchaseOrder)
        .where(
            PurchaseOrderItem.product_id == product.id,
            PurchaseOrder.status.in_(OPEN_STATUSES),
        )
    )
    if any(not _is_integral(q) for q in open_quantities):
        raise InvalidInput(
            f"Não é possível mudar para {unit.value}: há quantidade fracionada em pedido em aberto."
        )


def list_products(s: Session, *, include_inactive: bool = False) -> list[Product]:
    stmt = select(Product).order_by(Product.name)
    if not include_inactive:
        stmt = stmt.where(Product.active)
    return list(s.scalars(stmt))


def ensure_quantity_fits_unit(quantity: Decimal, unit: Unit) -> None:
    if unit is not Unit.KG and quantity != quantity.to_integral_value():
        raise InvalidInput(f"Quantidade deve ser inteira para unidade {unit.value}.")


def _lock_product(s: Session, product_id: int) -> Product:
    product = s.execute(
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if product is None:
        raise NotFound("Produto não encontrado.")
    return product


def record_movement(
    s: Session, data: MovementIn, *, purchase_order_id: int | None = None
) -> StockMovement:
    product = _lock_product(s, data.product_id)
    ensure_quantity_fits_unit(data.quantity, product.unit)

    delta = -data.quantity if data.kind is MovementKind.SALE else data.quantity
    new_stock = product.current_stock + delta
    if new_stock < 0:
        raise InsufficientStock(
            f"Estoque insuficiente: disponível {br_number(product.current_stock)}, "
            f"solicitado {br_number(abs(data.quantity))}."
        )

    product.current_stock = new_stock
    movement = StockMovement(
        product_id=product.id,
        kind=data.kind,
        quantity=data.quantity,
        occurred_at=data.occurred_at or datetime.now(UTC),
        note=data.note,
        purchase_order_id=purchase_order_id,
    )
    s.add(movement)
    s.flush()
    return movement


def list_movements(s: Session, product_id: int, *, limit: int = 50) -> list[StockMovement]:
    stmt = (
        select(StockMovement)
        .where(StockMovement.product_id == product_id)
        .order_by(StockMovement.occurred_at.desc(), StockMovement.id.desc())
        .limit(limit)
    )
    return list(s.scalars(stmt))
