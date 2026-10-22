"""Dados de demonstração: uma mercearia fictícia com 90 dias de vendas.

Uso: `python -m giro.seed` (não faz nada se já existir algum produto).
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from giro.inventory.schemas import ProductIn, SupplierIn
from giro.inventory.service import create_product, create_supplier
from giro.models import MovementKind, Product, StockMovement, Unit

HISTORY_DAYS = 90
WEEKEND_FACTOR = 1.5

SUPPLIERS = [
    ("Distribuidora Grão de Ouro", "+5511900000001"),
    ("Laticínios Serra Verde", "+5511900000002"),
    ("Bebidas Rio Claro", "+5511900000003"),
    ("Higiene & Lar Express", "+5511900000004"),
]


@dataclass(frozen=True)
class DemoProduct:
    supplier: int  # índice em SUPPLIERS
    sku: str
    name: str
    unit: Unit
    min_stock: int
    daily: float  # demanda média por dia útil
    profile: str  # "red" abaixo do mínimo, "yellow" comprar em breve, "green" folgado


UN, KG, CX = Unit.UN, Unit.KG, Unit.CX

PRODUCTS = [
    DemoProduct(0, "ARR5", "Arroz 5kg", UN, 12, 4.0, "green"),
    DemoProduct(0, "FEI1", "Feijão carioca 1kg", UN, 15, 5.0, "yellow"),
    DemoProduct(0, "ACU1", "Açúcar refinado 1kg", UN, 10, 3.0, "green"),
    DemoProduct(0, "FAR1", "Farinha de trigo 1kg", UN, 8, 2.0, "green"),
    DemoProduct(0, "MAC5", "Macarrão espaguete 500g", UN, 12, 4.0, "green"),
    DemoProduct(0, "SAL1", "Sal refinado 1kg", UN, 5, 1.0, "green"),
    DemoProduct(0, "CAF5", "Café torrado 500g", UN, 10, 3.5, "red"),
    DemoProduct(0, "OLE9", "Óleo de soja 900ml", UN, 12, 4.0, "red"),
    DemoProduct(1, "LEI1", "Leite integral 1L", UN, 24, 10.0, "red"),
    DemoProduct(1, "QMU", "Queijo muçarela", KG, 3, 1.2, "red"),
    DemoProduct(1, "MAN2", "Manteiga 200g", UN, 6, 1.5, "green"),
    DemoProduct(1, "IOG1", "Iogurte natural 170g", UN, 10, 3.0, "yellow"),
    DemoProduct(1, "REQ2", "Requeijão 200g", UN, 6, 1.5, "green"),
    DemoProduct(1, "PRE", "Presunto cozido", KG, 2, 0.8, "green"),
    DemoProduct(2, "REF2", "Refrigerante cola 2L", UN, 12, 5.0, "yellow"),
    DemoProduct(2, "AGU5", "Água mineral 500ml (12 un)", CX, 4, 1.0, "green"),
    DemoProduct(2, "SUC1", "Suco de uva 1L", UN, 6, 1.5, "green"),
    DemoProduct(2, "CER3", "Cerveja lata 350ml (12 un)", CX, 5, 1.5, "green"),
    DemoProduct(3, "SAB1", "Sabão em pó 1kg", UN, 6, 1.5, "green"),
    DemoProduct(3, "DET5", "Detergente 500ml", UN, 10, 3.0, "red"),
    DemoProduct(3, "PAP12", "Papel higiênico 12 rolos", UN, 6, 2.0, "yellow"),
    DemoProduct(3, "SAN1", "Água sanitária 1L", UN, 6, 1.5, "green"),
    DemoProduct(3, "PAS9", "Pasta de dente 90g", UN, 8, 2.0, "green"),
    DemoProduct(3, "SBO9", "Sabonete 90g", UN, 12, 3.0, "green"),
    DemoProduct(3, "ESP1", "Esponja dupla face", UN, 8, 1.5, "green"),
]


def _round(value: float, unit: Unit) -> Decimal:
    if unit is Unit.KG:
        return Decimal(str(round(max(value, 0.1), 1)))
    return Decimal(max(round(value), 1))


def _final_stock(p: DemoProduct, rng: random.Random) -> Decimal:
    if p.profile == "red":
        target = p.min_stock * rng.uniform(0.2, 0.6)
    elif p.profile == "yellow":
        target = p.min_stock + p.daily * rng.uniform(2.0, 4.5)
    else:
        target = p.min_stock + p.daily * rng.uniform(15, 40)
    return _round(target, p.unit) if target >= 0.1 else Decimal(0)


def seed(s: Session, *, now: datetime, rng_seed: int = 42) -> bool:
    if s.scalar(select(Product.id).limit(1)) is not None:
        return False
    rng = random.Random(rng_seed)

    suppliers = [
        create_supplier(s, SupplierIn(name=name, whatsapp=phone)) for name, phone in SUPPLIERS
    ]
    for p in PRODUCTS:
        product = create_product(
            s,
            ProductIn(
                sku=p.sku,
                name=p.name,
                unit=p.unit,
                supplier_id=suppliers[p.supplier].id,
                min_stock=Decimal(p.min_stock),
            ),
        )
        sales: list[tuple[datetime, Decimal]] = []
        for day in range(HISTORY_DAYS):
            # horário sempre no passado em relação a `now`, mesmo para hoje
            at = now - timedelta(days=day, hours=rng.uniform(0.5, 9))
            factor = WEEKEND_FACTOR if at.weekday() >= 5 else 1.0
            sales.append((at, _round(p.daily * factor * rng.uniform(0.6, 1.4), p.unit)))

        # Inserção em lote: o seed controla todo o histórico, então o saldo final é conhecido
        # e não precisa do lock por movimento de record_movement (90 dias × 25 itens).
        final = _final_stock(p, rng)
        total_sold = sum((q for _, q in sales), Decimal(0))
        s.add(
            StockMovement(
                product_id=product.id,
                kind=MovementKind.PURCHASE,
                quantity=final + total_sold,
                occurred_at=now - timedelta(days=HISTORY_DAYS, hours=12),
                note="Estoque inicial (demonstração)",
            )
        )
        s.add_all(
            StockMovement(product_id=product.id, kind=MovementKind.SALE, quantity=q, occurred_at=at)
            for at, q in sales
        )
        product.current_stock = final
    s.flush()
    return True


def main() -> None:
    from giro.config import get_settings
    from giro.db import make_engine

    engine = make_engine(get_settings().database_url)
    with Session(engine) as s:
        created = seed(s, now=datetime.now(UTC))
        s.commit()
    print("Dados de demonstração criados." if created else "Banco já tem produtos; seed ignorado.")


if __name__ == "__main__":
    main()
