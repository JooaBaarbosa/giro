from fastapi import APIRouter, Depends, status

from giro.api.deps import SessionDep, require_service_token
from giro.inventory import service
from giro.inventory.schemas import (
    MovementIn,
    MovementOut,
    ProductIn,
    ProductOut,
    ProductUpdate,
    SupplierIn,
    SupplierOut,
    SupplierUpdate,
)
from giro.models import Product, StockMovement, Supplier

router = APIRouter(prefix="/api", dependencies=[Depends(require_service_token)])


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(s: SessionDep, include_inactive: bool = False) -> list[Supplier]:
    return service.list_suppliers(s, include_inactive=include_inactive)


@router.post("/suppliers", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(data: SupplierIn, s: SessionDep) -> Supplier:
    supplier = service.create_supplier(s, data)
    s.commit()
    return supplier


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: int, data: SupplierUpdate, s: SessionDep) -> Supplier:
    supplier = service.update_supplier(s, supplier_id, data)
    s.commit()
    return supplier


@router.get("/products", response_model=list[ProductOut])
def list_products(s: SessionDep, include_inactive: bool = False) -> list[Product]:
    return service.list_products(s, include_inactive=include_inactive)


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductIn, s: SessionDep) -> Product:
    product = service.create_product(s, data)
    s.commit()
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, data: ProductUpdate, s: SessionDep) -> Product:
    product = service.update_product(s, product_id, data)
    s.commit()
    return product


@router.get("/products/{product_id}/movements", response_model=list[MovementOut])
def list_movements(product_id: int, s: SessionDep, limit: int = 50) -> list[StockMovement]:
    service.get_product(s, product_id)
    return service.list_movements(s, product_id, limit=limit)


@router.post("/movements", response_model=MovementOut, status_code=status.HTTP_201_CREATED)
def create_movement(data: MovementIn, s: SessionDep) -> StockMovement:
    movement = service.record_movement(s, data)
    s.commit()
    return movement
