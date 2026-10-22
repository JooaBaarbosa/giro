"""Telas de movimentação, produtos e fornecedores."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from giro import reports
from giro.api.deps import NowDep, SessionDep, SettingsDep
from giro.errors import DomainError
from giro.fmt import br_number
from giro.inventory import service
from giro.inventory.schemas import (
    MovementIn,
    ProductIn,
    ProductUpdate,
    SupplierIn,
    SupplierUpdate,
)
from giro.models import MovementKind
from giro.web.templating import KIND_LABELS, chart_data, form_errors, is_htmx, parse_decimal, render

router = APIRouter()

Field = Annotated[str, Form()]
OptionalField = Annotated[str, Form()]


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# --- Movimentações ---------------------------------------------------------------------------


def _movement_page(
    request: Request,
    s: SessionDep,
    *,
    form: dict[str, Any],
    errors: dict[str, str],
    result: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    template = "_movement_panel.html" if is_htmx(request) else "movement_form.html"
    response = render(
        request,
        template,
        products=service.list_products(s),
        form=form,
        errors=errors,
        result=result,
        nav="movements",
    )
    response.status_code = status_code
    return response


@router.get("/movements/new", response_class=HTMLResponse)
def movement_form(request: Request, s: SessionDep) -> HTMLResponse:
    return _movement_page(request, s, form={"kind": "sale"}, errors={})


@router.post("/movements", response_class=HTMLResponse)
def movement_create(
    request: Request,
    s: SessionDep,
    product_id: Field = "",
    kind: Field = "sale",
    quantity: Field = "",
    note: OptionalField = "",
) -> HTMLResponse:
    form = {"product_id": product_id, "kind": kind, "quantity": quantity, "note": note}
    try:
        data = MovementIn.model_validate(
            {
                "product_id": product_id,
                "kind": kind,
                "quantity": parse_decimal(quantity),
                "note": note.strip() or None,
            }
        )
        movement = service.record_movement(s, data)
        s.commit()
    except ValidationError as exc:
        # 200 de propósito: o HTMX não troca o conteúdo em respostas 4xx.
        return _movement_page(request, s, form=form, errors=form_errors(exc))
    except DomainError as exc:
        s.rollback()
        return _movement_page(request, s, form=form, errors={"__all__": exc.message})

    product = movement.product
    gender = "registrado" if data.kind is MovementKind.ADJUSTMENT else "registrada"
    unit = product.unit.value
    result = (
        f"{KIND_LABELS[data.kind]} {gender}: {product.name} {br_number(data.quantity)} {unit}. "
        f"Estoque: {br_number(product.current_stock)}"
    )
    return _movement_page(
        request, s, form={"kind": data.kind.value, "product_id": ""}, errors={}, result=result
    )


# --- Produtos ----------------------------------------------------------------------------------


@router.get("/products", response_class=HTMLResponse)
def products_page(request: Request, s: SessionDep) -> HTMLResponse:
    return render(
        request,
        "products.html",
        products=service.list_products(s, include_inactive=True),
        nav="products",
    )


def _product_form(
    request: Request,
    s: SessionDep,
    *,
    form: dict[str, Any],
    errors: dict[str, str],
    status_code: int = 200,
) -> HTMLResponse:
    response = render(
        request,
        "product_new.html",
        suppliers=service.list_suppliers(s),
        form=form,
        errors=errors,
        nav="products",
    )
    response.status_code = status_code
    return response


@router.get("/products/new", response_class=HTMLResponse)
def product_new(request: Request, s: SessionDep) -> HTMLResponse:
    return _product_form(request, s, form={"unit": "un", "cover_days": "15"}, errors={})


@router.post("/products", response_class=HTMLResponse)
def product_create(
    request: Request,
    s: SessionDep,
    sku: Field = "",
    name: Field = "",
    unit: Field = "un",
    supplier_id: Field = "",
    min_stock: Field = "",
    cover_days: Field = "15",
) -> Response:
    form = {
        "sku": sku,
        "name": name,
        "unit": unit,
        "supplier_id": supplier_id,
        "min_stock": min_stock,
        "cover_days": cover_days,
    }
    try:
        data = ProductIn.model_validate(
            form | {"sku": sku.strip(), "name": name.strip(), "min_stock": parse_decimal(min_stock)}
        )
        product = service.create_product(s, data)
        s.commit()
    except ValidationError as exc:
        return _product_form(request, s, form=form, errors=form_errors(exc), status_code=422)
    except DomainError as exc:
        s.rollback()
        return _product_form(
            request, s, form=form, errors={"__all__": exc.message}, status_code=422
        )
    return _see_other(f"/products/{product.id}")


def _product_detail(
    request: Request,
    s: SessionDep,
    settings: SettingsDep,
    now: NowDep,
    product_id: int,
    *,
    form: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    product = service.get_product(s, product_id)
    series = reports.daily_sales(s, now=now, tz=settings.store_tz, product_id=product.id)
    response = render(
        request,
        "product_detail.html",
        product=product,
        forecast=reports.forecast_for(s, product, now=now, tz=settings.store_tz),
        today=reports.local_today(now, settings.store_tz),
        movements=service.list_movements(s, product.id),
        suppliers=service.list_suppliers(s),
        chart=chart_data(series),
        form=form
        or {
            "name": product.name,
            "supplier_id": product.supplier_id,
            "min_stock": br_number(product.min_stock),
            "cover_days": product.cover_days,
            "active": product.active,
        },
        errors=errors or {},
        nav="products",
    )
    response.status_code = status_code
    return response


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_detail(
    request: Request, product_id: int, s: SessionDep, settings: SettingsDep, now: NowDep
) -> HTMLResponse:
    return _product_detail(request, s, settings, now, product_id)


@router.post("/products/{product_id}", response_class=HTMLResponse)
def product_update(
    request: Request,
    product_id: int,
    s: SessionDep,
    settings: SettingsDep,
    now: NowDep,
    name: Field = "",
    supplier_id: Field = "",
    min_stock: Field = "",
    cover_days: Field = "",
    active: OptionalField = "",
) -> Response:
    form = {
        "name": name,
        "supplier_id": supplier_id,
        "min_stock": min_stock,
        "cover_days": cover_days,
        "active": active == "on",
    }
    try:
        data = ProductUpdate.model_validate(
            form | {"name": name.strip(), "min_stock": parse_decimal(min_stock)}
        )
        service.update_product(s, product_id, data)
        s.commit()
    except ValidationError as exc:
        s.rollback()
        return _product_detail(
            request,
            s,
            settings,
            now,
            product_id,
            form=form,
            errors=form_errors(exc),
            status_code=422,
        )
    except DomainError as exc:
        s.rollback()
        return _product_detail(
            request,
            s,
            settings,
            now,
            product_id,
            form=form,
            errors={"__all__": exc.message},
            status_code=422,
        )
    return _see_other(f"/products/{product_id}")


# --- Fornecedores ------------------------------------------------------------------------------


@router.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request, s: SessionDep) -> HTMLResponse:
    return render(
        request,
        "suppliers.html",
        suppliers=service.list_suppliers(s, include_inactive=True),
        nav="suppliers",
    )


def _supplier_form(
    request: Request,
    *,
    supplier_id: int | None,
    form: dict[str, Any],
    errors: dict[str, str],
    status_code: int = 200,
) -> HTMLResponse:
    response = render(
        request,
        "supplier_form.html",
        supplier_id=supplier_id,
        form=form,
        errors=errors,
        nav="suppliers",
    )
    response.status_code = status_code
    return response


@router.get("/suppliers/new", response_class=HTMLResponse)
def supplier_new(request: Request) -> HTMLResponse:
    return _supplier_form(request, supplier_id=None, form={"active": True}, errors={})


@router.post("/suppliers", response_class=HTMLResponse)
def supplier_create(
    request: Request, s: SessionDep, name: Field = "", whatsapp: Field = ""
) -> Response:
    form = {"name": name, "whatsapp": whatsapp, "active": True}
    try:
        service.create_supplier(s, SupplierIn.model_validate(form | {"name": name.strip()}))
        s.commit()
    except ValidationError as exc:
        return _supplier_form(
            request, supplier_id=None, form=form, errors=form_errors(exc), status_code=422
        )
    return _see_other("/suppliers")


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_edit(request: Request, supplier_id: int, s: SessionDep) -> HTMLResponse:
    supplier = service.get_supplier(s, supplier_id)
    form = {"name": supplier.name, "whatsapp": supplier.whatsapp, "active": supplier.active}
    return _supplier_form(request, supplier_id=supplier_id, form=form, errors={})


@router.post("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_update(
    request: Request,
    supplier_id: int,
    s: SessionDep,
    name: Field = "",
    whatsapp: Field = "",
    active: OptionalField = "",
) -> Response:
    form = {"name": name, "whatsapp": whatsapp, "active": active == "on"}
    try:
        data = SupplierUpdate.model_validate(form | {"name": name.strip()})
        service.update_supplier(s, supplier_id, data)
        s.commit()
    except ValidationError as exc:
        return _supplier_form(
            request, supplier_id=supplier_id, form=form, errors=form_errors(exc), status_code=422
        )
    return _see_other("/suppliers")
