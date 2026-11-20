"""Dados de demonstração: uma mercearia fictícia com 90 dias de vendas.

Uso: `python -m giro.seed` (não faz nada se já existir algum produto).

Também cria 3 turnos de caixa fechados (`is_demo`) nos 3 dias anteriores, gerados pelo serviço do
caixa. O estoque inicial inclui o que essas vendas consomem, então o saldo final de cada produto
continua no perfil (vermelho, amarelo, verde) pretendido.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from giro.inventory.schemas import ProductIn, SupplierIn
from giro.inventory.service import create_product, create_supplier
from giro.models import MovementKind, PaymentMethod, Product, StockMovement, Unit
from giro.pos import service as pos
from giro.pos.schemas import AddItemIn, CloseSessionIn, OpenSessionIn, PaymentIn

HISTORY_DAYS = 90
WEEKEND_FACTOR = 1.5
DEMO_SESSION_DAYS = (3, 2, 1)  # dias atrás (turnos fechados, sempre no passado)
DEMO_OPENING = Decimal("100.00")
DEMO_METHODS = (PaymentMethod.CASH, PaymentMethod.PIX, PaymentMethod.DEBIT, PaymentMethod.CREDIT)

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
    price: str  # preço de venda (fictício)
    cost: str  # custo (fictício), sempre menor que o preço
    barcode: str | None = None  # EAN-13 fictício (prefixo 789 + dígitos inventados)


UN, KG, CX = Unit.UN, Unit.KG, Unit.CX

PRODUCTS = [
    DemoProduct(0, "ARR5", "Arroz 5kg", UN, 12, 4.0, "green", "28.90", "22.40", "7891000000113"),
    DemoProduct(
        0, "FEI1", "Feijão carioca 1kg", UN, 15, 5.0, "yellow", "8.49", "6.20", "7891000000281"
    ),
    DemoProduct(
        0, "ACU1", "Açúcar refinado 1kg", UN, 10, 3.0, "green", "4.99", "3.70", "7891000000359"
    ),
    DemoProduct(0, "FAR1", "Farinha de trigo 1kg", UN, 8, 2.0, "green", "5.49", "4.10"),
    DemoProduct(0, "MAC5", "Macarrão espaguete 500g", UN, 12, 4.0, "green", "4.79", "3.30"),
    DemoProduct(0, "SAL1", "Sal refinado 1kg", UN, 5, 1.0, "green", "3.29", "2.10"),
    DemoProduct(
        0, "CAF5", "Café torrado 500g", UN, 10, 3.5, "red", "18.90", "14.20", "7891000000427"
    ),
    DemoProduct(0, "OLE9", "Óleo de soja 900ml", UN, 12, 4.0, "red", "7.99", "6.10"),
    DemoProduct(
        1, "LEI1", "Leite integral 1L", UN, 24, 10.0, "red", "5.49", "4.30", "7891000000595"
    ),
    DemoProduct(1, "QMU", "Queijo muçarela", KG, 3, 1.2, "red", "42.90", "33.50"),
    DemoProduct(1, "MAN2", "Manteiga 200g", UN, 6, 1.5, "green", "11.90", "8.90"),
    DemoProduct(1, "IOG1", "Iogurte natural 170g", UN, 10, 3.0, "yellow", "3.99", "2.80"),
    DemoProduct(1, "REQ2", "Requeijão 200g", UN, 6, 1.5, "green", "7.49", "5.60"),
    DemoProduct(1, "PRE", "Presunto cozido", KG, 2, 0.8, "green", "39.90", "31.00"),
    DemoProduct(
        2, "REF2", "Refrigerante cola 2L", UN, 12, 5.0, "yellow", "9.99", "7.30", "7891000000663"
    ),
    DemoProduct(2, "AGU5", "Água mineral 500ml (12 un)", CX, 4, 1.0, "green", "19.90", "14.50"),
    DemoProduct(2, "SUC1", "Suco de uva 1L", UN, 6, 1.5, "green", "12.90", "9.40"),
    DemoProduct(2, "CER3", "Cerveja lata 350ml (12 un)", CX, 5, 1.5, "green", "49.90", "39.00"),
    DemoProduct(3, "SAB1", "Sabão em pó 1kg", UN, 6, 1.5, "green", "14.90", "10.80"),
    DemoProduct(3, "DET5", "Detergente 500ml", UN, 10, 3.0, "red", "2.79", "1.90"),
    DemoProduct(3, "PAP12", "Papel higiênico 12 rolos", UN, 6, 2.0, "yellow", "19.90", "14.60"),
    DemoProduct(3, "SAN1", "Água sanitária 1L", UN, 6, 1.5, "green", "4.99", "3.40"),
    DemoProduct(3, "PAS9", "Pasta de dente 90g", UN, 8, 2.0, "green", "4.49", "3.10"),
    DemoProduct(3, "SBO9", "Sabonete 90g", UN, 12, 3.0, "green", "2.49", "1.60"),
    DemoProduct(3, "ESP1", "Esponja dupla face", UN, 8, 1.5, "green", "3.99", "2.60"),
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


# venda planejada: (índice do produto em PRODUCTS, quantidade) por linha, forma de pagamento
DemoSale = tuple[list[tuple[int, Decimal]], PaymentMethod]


def _plan_demo_sales(rng: random.Random) -> list[list[DemoSale]]:
    plan: list[list[DemoSale]] = []
    for _ in DEMO_SESSION_DAYS:
        sales: list[DemoSale] = []
        for _ in range(rng.randint(3, 5)):
            lines = []
            for idx in rng.sample(range(len(PRODUCTS)), rng.randint(1, 4)):
                unit = PRODUCTS[idx].unit
                qty = (
                    Decimal(str(round(rng.uniform(0.2, 1.2), 1)))
                    if unit is Unit.KG
                    else Decimal(rng.randint(1, 3))
                )
                lines.append((idx, qty))
            sales.append((lines, rng.choice(DEMO_METHODS)))
        plan.append(sales)
    return plan


def _run_demo_session(s: Session, sales: list[DemoSale], *, day: datetime, tz: ZoneInfo) -> None:
    def at(hour: int, minute: int = 0) -> datetime:
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz).astimezone(
            UTC
        )

    session = pos.open_session(s, OpenSessionIn(opening_amount=DEMO_OPENING), now=at(8))
    session.is_demo = True
    for n, (lines, method) in enumerate(sales):
        moment = at(9 + n, 15)
        for idx, qty in lines:
            pos.add_item(s, AddItemIn(code=PRODUCTS[idx].sku, quantity=qty), now=moment)
        sale = pos.current_sale(s, fresh=True)
        assert sale is not None
        pos.add_payment(s, PaymentIn(method=method, amount=sale.total))
        pos.complete_sale(s, now=moment)
    counted = pos.expected_cash(s, session.id)  # sem diferença inventada
    pos.close_session(s, CloseSessionIn(counted_amount=counted), now=at(18))


def seed(s: Session, *, now: datetime, rng_seed: int = 42, tz: str = "America/Sao_Paulo") -> bool:
    if s.scalar(select(Product.id).limit(1)) is not None:
        return False
    rng = random.Random(rng_seed)
    # sorteio separado: não altera o histórico (e os perfis de estoque) gerado por `rng`
    demo_plan = _plan_demo_sales(random.Random(rng_seed + 1))
    demo_need: dict[int, Decimal] = {}
    for day_sales in demo_plan:
        for lines, _ in day_sales:
            for idx, qty in lines:
                demo_need[idx] = demo_need.get(idx, Decimal(0)) + qty

    suppliers = [
        create_supplier(s, SupplierIn(name=name, whatsapp=phone)) for name, phone in SUPPLIERS
    ]
    for idx, p in enumerate(PRODUCTS):
        product = create_product(
            s,
            ProductIn(
                sku=p.sku,
                name=p.name,
                unit=p.unit,
                supplier_id=suppliers[p.supplier].id,
                min_stock=Decimal(p.min_stock),
                barcode=p.barcode,
                sale_price=Decimal(p.price),
                cost_price=Decimal(p.cost),
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
        # as vendas dos turnos de demonstração baixam este extra, voltando ao saldo `final`
        extra = demo_need.get(idx, Decimal(0))
        total_sold = sum((q for _, q in sales), Decimal(0))
        s.add(
            StockMovement(
                product_id=product.id,
                kind=MovementKind.PURCHASE,
                quantity=final + total_sold + extra,
                occurred_at=now - timedelta(days=HISTORY_DAYS, hours=12),
                note="Estoque inicial (demonstração)",
            )
        )
        s.add_all(
            StockMovement(product_id=product.id, kind=MovementKind.SALE, quantity=q, occurred_at=at)
            for at, q in sales
        )
        product.current_stock = final + extra
    s.flush()

    zone = ZoneInfo(tz)
    today = now.astimezone(zone)
    for days_ago, day_sales in zip(DEMO_SESSION_DAYS, demo_plan, strict=True):
        _run_demo_session(s, day_sales, day=today - timedelta(days=days_ago), tz=zone)
    return True


def main() -> None:
    from giro.config import get_settings
    from giro.db import make_engine

    engine = make_engine(get_settings().database_url)
    with Session(engine) as s:
        created = seed(s, now=datetime.now(UTC), tz=get_settings().store_tz)
        s.commit()
    print("Dados de demonstração criados." if created else "Banco já tem produtos; seed ignorado.")


if __name__ == "__main__":
    main()
