# Giro Caixa (PDV + turno) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ponto de venda de caixa único integrado ao estoque do Giro: carrinho por código, pagamento manual (dividido, com troco), baixa de estoque na conclusão, cancelamento com estorno e turno com sangria, suprimento e fechamento conferido.

**Architecture:** Novo módulo `src/giro/pos/` com a regra de negócio (venda `open` persistida no banco; estoque baixado só em `complete_sale`, numa transação com `FOR UPDATE`). Rotas JSON em `api/pos.py` e telas HTMX em `web/pos.py` chamam o mesmo serviço, que nunca faz commit. Relatórios passam a usar venda líquida (`sale − sale_cancel`).

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17, HTMX 2.0.4, Chart.js 4.4.7 (já vendorizados), pytest + testcontainers.

**Spec:** `docs/superpowers/specs/2026-11-20-giro-caixa-design.md`

## Global Constraints

- Branch `feat/caixa`; projeto continua em Python 3.14 (`requires-python` atual não muda).
- Nenhuma dependência nova é necessária. Se surgir uma, fixar a versão estável vigente verificada no registry.
- Testes: `uv run python -m pytest` (nunca `pytest.exe`; o Windows bloqueia o launcher). Docker Desktop precisa estar aberto (testcontainers).
- Serviços não fazem commit; quem chama (rota) decide.
- Dinheiro `NUMERIC(12,2)`; quantidade `NUMERIC(12,3)`; arredondamento `ROUND_HALF_UP` em 2 casas.
- Mensagens ao usuário em pt-BR, prontas, via `DomainError`.
- UI: sem botões pílula, sem emoji como ícone, sem travessões (— ou –) em textos novos, sem números inventados, movimento só funcional com `prefers-reduced-motion` respeitado.
- Estoque só muda via `inventory.service` (reusar `_lock_product`); `stock_movements` é só de inserção.
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Concluir venda sem itens: recusar com "Adicione ao menos um item antes de concluir." (teste na Task 6).
- Preço do produto alterado depois do item entrar no carrinho: a linha mantém o preço congelado e a conclusão usa esse preço (teste na Task 5).
- Código que é `barcode` de um produto e `sku` de outro: o `barcode` vence; espaços nas pontas são ignorados (teste na Task 3).
- Peso com mais de 3 casas (`0,3456*QMU`) ou quantidade fracionada para `un`: recusa com `InvalidInput`, sem arredondar em silêncio (teste na Task 2 e Task 5).
- Pagamento com valor zero ou negativo, ou Pix/cartão acima do restante: recusado ao lançar, não só ao concluir (teste na Task 6).

---

### Task 1: Modelo de dados e migração 0003

**Files:**
- Modify: `src/giro/models.py`
- Create: `migrations/versions/0003_pos.py`
- Modify: `tests/conftest.py` (`TABLES`)
- Test: `tests/integration/test_schema.py`

**Interfaces:**
- Produces (em `giro.models`):
  - `Money = Numeric(12, 2)`
  - `MovementKind.SALE_CANCEL = "sale_cancel"`
  - `class PaymentMethod(StrEnum)`: `CASH="cash"`, `PIX="pix"`, `CREDIT="credit"`, `DEBIT="debit"`
  - `class SaleStatus(StrEnum)`: `OPEN="open"`, `COMPLETED="completed"`, `CANCELLED="cancelled"`
  - `class CashMovementKind(StrEnum)`: `SUPPLY="supply"`, `WITHDRAWAL="withdrawal"`
  - `Product.barcode: str | None`, `Product.sale_price: Decimal | None`, `Product.cost_price: Decimal | None`
  - `StockMovement.sale_id: int | None`
  - `CashSession` (`cash_sessions`): `id, opened_at, opening_amount, closed_at, expected_amount, counted_amount, difference, close_note, is_demo`; relationships `sales`, `movements`
  - `CashMovement` (`cash_movements`): `id, session_id, kind, amount, note, occurred_at`
  - `Sale` (`sales`): `id, session_id, status, total, change_given, created_at, completed_at, cancelled_at, cancel_reason`; relationships `session`, `items` (cascade delete-orphan, order_by id), `payments` (cascade delete-orphan, order_by id)
  - `SaleItem` (`sale_items`): `id, sale_id, product_id, quantity, unit_price, unit_cost, line_total`; relationship `product`
  - `SalePayment` (`sale_payments`): `id, sale_id, method, amount`

- [ ] **Step 1: Write the failing schema tests** em `tests/integration/test_schema.py`:

```python
def test_pos_tables_exist(engine):
    names = set(sa.inspect(engine).get_table_names())
    assert {"cash_sessions", "cash_movements", "sales", "sale_items", "sale_payments"} <= names

def test_only_one_open_cash_session(session):
    session.add_all([CashSession(opening_amount=Decimal(0)), CashSession(opening_amount=Decimal(0))])
    with pytest.raises(IntegrityError):
        session.flush()

def test_only_one_open_sale(session):
    cs = CashSession(opening_amount=Decimal(0)); session.add(cs); session.flush()
    session.add_all([Sale(session_id=cs.id), Sale(session_id=cs.id)])
    with pytest.raises(IntegrityError):
        session.flush()

def test_barcode_unique_when_present(session, make_product):
    make_product(barcode="7891000100103"); make_product(barcode=None); make_product(barcode=None)
    with pytest.raises(IntegrityError):
        make_product(barcode="7891000100103")

def test_sale_cancel_movement_kind_accepted(session, make_product):
    p = make_product(current_stock=Decimal(1))
    session.add(StockMovement(product_id=p.id, kind=MovementKind.SALE_CANCEL,
                              quantity=Decimal(1), occurred_at=datetime.now(UTC)))
    session.flush()
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/integration/test_schema.py -v`. Expected: FAIL (import/tabela inexistente).

- [ ] **Step 3: Implement models** conforme Interfaces. `Sale.status` padrão `OPEN`, `total` e `change_given` padrão `0`; `opened_at`/`created_at` com `server_default=func.now()`; enums com o helper `_enum` existente.

- [ ] **Step 4: Write `migrations/versions/0003_pos.py`** (`revision="0003"`, `down_revision="0002"`). Conteúdo:
  - `products`: `barcode VARCHAR(14)`, `sale_price NUMERIC(12,2)`, `cost_price NUMERIC(12,2)`; `CHECK sale_price > 0`, `CHECK cost_price >= 0`; índice único parcial `ux_products_barcode` `WHERE barcode IS NOT NULL`.
  - 5 tabelas com `CHECK` de enum (`... IN (...)`) e de valores (`amount > 0`, `quantity > 0`, `opening_amount >= 0`, `total >= 0`, `change_given >= 0`, `counted_amount >= 0`).
  - `ux_cash_sessions_open` único em `(( true ))` `WHERE closed_at IS NULL`; `ux_sales_open` idem `WHERE status = 'open'`. Use `op.create_index(..., unique=True, postgresql_where=...)` sobre uma expressão constante, ex.: `sa.text("(true)")`.
  - `stock_movements.sale_id` FK `sales.id`; trocar `ck_movements_kind` para incluir `'sale_cancel'`.
  - `downgrade`: se `SELECT 1 FROM stock_movements WHERE kind='sale_cancel'` existir, `raise RuntimeError("Há cancelamentos de venda no estoque; o downgrade apagaria histórico.")`; senão desfaz tudo na ordem inversa.

- [ ] **Step 5: Update `TABLES`** em `tests/conftest.py` para `"sale_payments, sale_items, cash_movements, stock_movements, sales, cash_sessions, purchase_order_items, purchase_orders, products, suppliers"`.

- [ ] **Step 6: Run** `uv run python -m pytest tests/integration/test_schema.py -v` → PASS; depois `uv run python -m pytest -q` → tudo verde.

- [ ] **Step 7: Commit** `feat(caixa): modelo de dados e migração 0003 do PDV`.

---

### Task 2: Erros, dinheiro e parse de código (unitário)

**Files:**
- Modify: `src/giro/errors.py`, `src/giro/api/errors.py`
- Create: `src/giro/pos/__init__.py`, `src/giro/pos/money.py`, `src/giro/pos/codes.py`
- Test: `tests/unit/test_pos_money.py`, `tests/unit/test_pos_codes.py`

**Interfaces:**
- Produces:
  - `errors.NoOpenSession`, `errors.SessionAlreadyOpen`, `errors.PaymentMismatch` (todos `DomainError`)
  - `@dataclass(frozen=True) class Shortage: product_id: int; name: str; available: Decimal; needed: Decimal` em `giro.errors`
  - `InsufficientStock.__init__(self, message: str, shortages: list[Shortage] | None = None)`; atributo `shortages` (lista vazia por padrão)
  - `STATUS_BY_ERROR`: `NoOpenSession: 409`, `SessionAlreadyOpen: 409`, `PaymentMismatch: 422`; `BACK_LINKS["/caixa"] = "Caixa"`
  - `pos.money.line_total(quantity: Decimal, unit_price: Decimal) -> Decimal`
  - `pos.money.compute_change(total: Decimal, payments: Sequence[tuple[PaymentMethod, Decimal]]) -> Decimal`; levanta `PaymentMismatch`
  - `pos.money.validate_new_payment(total: Decimal, paid: Sequence[tuple[PaymentMethod, Decimal]], method: PaymentMethod, amount: Decimal) -> None`; levanta `PaymentMismatch`
  - `pos.codes.CodeEntry(quantity: Decimal | None, code: str)` (dataclass frozen)
  - `pos.codes.parse_entry(raw: str) -> CodeEntry`; levanta `InvalidInput`
  - `pos.codes.is_valid_gtin(code: str) -> bool`

- [ ] **Step 1: Write failing tests.**

```python
# test_pos_money.py
def test_line_total_rounds_half_up():
    assert line_total(Decimal("0.355"), Decimal("49.90")) == Decimal("17.71")  # 17.7145
    assert line_total(Decimal("3"), Decimal("4.99")) == Decimal("14.97")

def test_change_only_from_cash():
    assert compute_change(Decimal("30.00"), [(CASH, Decimal("50.00"))]) == Decimal("20.00")
    assert compute_change(Decimal("30.00"), [(CASH, Decimal("20.00")), (PIX, Decimal("10.00"))]) == 0
    with pytest.raises(PaymentMismatch):
        compute_change(Decimal("30.00"), [(PIX, Decimal("40.00"))])

def test_underpaid_refused():
    with pytest.raises(PaymentMismatch, match="Faltam R\\$ 5,00"):
        compute_change(Decimal("30.00"), [(PIX, Decimal("25.00"))])

def test_non_cash_cannot_exceed_remaining():
    with pytest.raises(PaymentMismatch):
        validate_new_payment(Decimal("30"), [(CASH, Decimal("20"))], CREDIT, Decimal("15"))

@pytest.mark.parametrize("amount", [Decimal(0), Decimal("-1")])
def test_payment_must_be_positive(amount):
    with pytest.raises(PaymentMismatch):
        validate_new_payment(Decimal("30"), [], CASH, amount)

# test_pos_codes.py
@pytest.mark.parametrize("raw,expected", [
    ("7891000100103", CodeEntry(None, "7891000100103")),
    ("  ARR5 ", CodeEntry(None, "ARR5")),
    ("3*ARR5", CodeEntry(Decimal(3), "ARR5")),
    ("0,350*QMU", CodeEntry(Decimal("0.350"), "QMU")),
    ("2 * ARR5", CodeEntry(Decimal(2), "ARR5")),
])
def test_parse_entry(raw, expected): assert parse_entry(raw) == expected

@pytest.mark.parametrize("raw", ["", "   ", "*ARR5", "x*ARR5", "0*ARR5", "-1*ARR5", "0,3456*QMU", "3*"])
def test_parse_entry_invalid(raw):
    with pytest.raises(InvalidInput): parse_entry(raw)

@pytest.mark.parametrize("code,ok", [
    ("7891000100103", True), ("7891000100104", False), ("96385074", True),
    ("036000291452", True), ("12345", False), ("78910001001AB", False),
])
def test_gtin(code, ok): assert is_valid_gtin(code) is ok
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/unit/test_pos_money.py tests/unit/test_pos_codes.py -v` → FAIL.

- [ ] **Step 3: Implement.** Mensagens: falta → `"Faltam R$ X para fechar o total."`; excedente não-dinheiro → `"Pix e cartão não podem passar do valor da venda; troco só em dinheiro."`; valor ≤ 0 → `"Valor do pagamento deve ser maior que zero."`. Formato `R$` via nova `fmt.brl(value: Decimal) -> str` (`"R$ 1.234,56"`, sempre 2 casas), com teste em `tests/unit/test_pos_money.py::test_brl`. `parse_entry`: separa no primeiro `*`, aceita vírgula ou ponto decimal, quantidade > 0 com no máximo 3 casas. `is_valid_gtin`: só dígitos, comprimento 8/12/13, dígito verificador GS1 (pesos 3/1 da direita para a esquerda, excluindo o verificador).

- [ ] **Step 4: Run** os testes → PASS.

- [ ] **Step 5: Commit** `feat(caixa): erros de domínio, cálculo de troco e leitura do código`.

---

### Task 3: Preço, custo e código de barras no produto

**Files:**
- Modify: `src/giro/inventory/schemas.py`, `src/giro/inventory/service.py`, `src/giro/web/inventory.py`, `src/giro/web/templates/_product_fields.html`, `_product_table.html`, `product_detail.html`, `src/giro/web/templating.py` (filtro `brl`)
- Test: `tests/integration/test_inventory.py`, `tests/web/test_forms.py`, `tests/api/test_api_inventory.py`

**Interfaces:**
- Consumes: `is_valid_gtin` (Task 2), `fmt.brl` (Task 2).
- Produces:
  - `ProductIn`/`ProductUpdate`/`ProductOut` ganham `barcode: str | None`, `sale_price: Decimal | None` (`gt=0`, 2 casas), `cost_price: Decimal | None` (`ge=0`, 2 casas). Validador de `barcode`: `strip`, vazio vira `None`, inválido levanta `ValueError("Código de barras inválido (EAN-8, UPC-A ou EAN-13).")`.
  - `inventory.service.find_product_by_code(s: Session, code: str) -> Product`: ativo, `barcode` exato primeiro, depois `sku` exato; senão `NotFound("Produto não encontrado.")`.
  - Barcode duplicado (pré-checagem + `IntegrityError` em savepoint, como o SKU) levanta `InvalidInput("Código de barras já cadastrado em outro produto.")`.
  - Filtro Jinja `brl`.

- [ ] **Step 1: Write failing tests.**
  - `test_find_by_code_prefers_barcode_over_sku`: produto A `sku="7891000100103"`, produto B `barcode="7891000100103"` → retorna B.
  - `test_find_by_code_ignores_inactive` → `NotFound`.
  - `test_duplicate_barcode_refused` (create e update) → `InvalidInput`.
  - `test_invalid_barcode_rejected_by_schema` → `ValidationError`.
  - API: `POST /api/products` com `sale_price="12.90"` retorna o campo; web: criar produto pelo formulário com `barcode`, `sale_price="12,90"`, `cost_price="8,00"` grava `Decimal("12.90")`/`Decimal("8.00")`; tabela de produtos mostra `R$ 12,90`; detalhe de produto sem preço mostra `"Sem preço de venda: não pode ser vendido no caixa."`.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** schemas, service, campos no formulário (labels "Código de barras", "Preço de venda (R$)", "Custo (R$)"; `inputmode="decimal"` / `inputmode="numeric"` para o código), coluna "Preço" na tabela e aviso no detalhe. Valores do formulário passam pelo `parse_decimal` existente.
- [ ] **Step 4: Run** `uv run python -m pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(caixa): preço de venda, custo e código de barras no produto`.

---

### Task 4: Turno de caixa (abrir, sangria, suprimento, esperado)

**Files:**
- Create: `src/giro/pos/state.py`, `src/giro/pos/schemas.py`, `src/giro/pos/service.py`
- Test: `tests/unit/test_pos_state.py`, `tests/integration/test_pos_session.py`

**Interfaces:**
- Produces:
  - `pos.state.TRANSITIONS` (`OPEN→{COMPLETED}`, `COMPLETED→{CANCELLED}`, `CANCELLED→{}`), `STATUS_LABELS` (`"em andamento"`, `"concluída"`, `"cancelada"`), `ensure_transition(current: SaleStatus, target: SaleStatus) -> None` (mensagem `"Venda não pode passar de {a} para {b}."`), `METHOD_LABELS` (`Dinheiro`, `Pix`, `Crédito`, `Débito`).
  - `pos.schemas`: `OpenSessionIn(opening_amount: Decimal ge=0)`, `CashMovementIn(kind, amount gt=0, note ≤500)`, `CloseSessionIn(counted_amount ge=0, note ≤500 | None)`, `AddItemIn(code: str, quantity: Decimal | None)`, `ItemQtyIn(quantity gt=0)`, `PaymentIn(method, amount)`, `CancelSaleIn(reason: min 3, max 500)`, `AdjustShortageIn(product_id, reason: min 3, max 500)`, e saídas `CashSessionOut`, `SaleOut` (com `items`, `payments`), `SessionSummaryOut`.
  - `pos.service.open_session(s, data: OpenSessionIn, *, now: datetime) -> CashSession` (`SessionAlreadyOpen("Já existe um caixa aberto.")`, inclusive via `IntegrityError` em savepoint)
  - `pos.service.current_session(s) -> CashSession | None`
  - `pos.service.require_session(s, *, lock: bool = False) -> CashSession` (`NoOpenSession("O caixa está fechado. Abra o caixa para continuar.")`)
  - `pos.service.add_cash_movement(s, data: CashMovementIn, *, now) -> CashMovement` (sangria acima do esperado: `InvalidInput("Sangria maior que o dinheiro esperado no caixa (R$ X).")`)
  - `pos.service.expected_cash(s, session_id: int) -> Decimal` (fórmula da spec §3.3; dinheiro de cada venda concluída = `Σ cash − change_given`)

- [ ] **Step 1: Write failing tests:** transições válidas/inválidas da venda; `open_session` duas vezes → `SessionAlreadyOpen`; operações sem turno → `NoOpenSession`; `expected_cash` com troco inicial 100, suprimento 50, sangria 30 → `120.00`; sangria de 200 nesse cenário → `InvalidInput`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `expected_cash` faz agregações SQL (não carrega vendas em Python).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): abertura de turno, sangria e suprimento`.

---

### Task 5: Carrinho (venda em aberto)

**Files:**
- Modify: `src/giro/pos/service.py`
- Test: `tests/integration/test_pos_cart.py`

**Interfaces:**
- Consumes: `find_product_by_code` (Task 3), `ensure_quantity_fits_unit` (inventory), `line_total` (Task 2), `require_session` (Task 4).
- Produces:
  - `current_sale(s) -> Sale | None` (a venda `open`)
  - `add_item(s, data: AddItemIn, *, now) -> SaleItem`: cria a venda `open` se não houver; produto sem `sale_price` → `InvalidInput("Produto sem preço de venda: cadastre o preço antes de vender.")`; `kg` sem quantidade → `InvalidInput("Informe o peso para {nome}.")`; `un`/`cx` sem quantidade → 1; mesmo produto já no carrinho soma na linha (mantém o preço congelado da linha); recalcula `line_total` e `Sale.total`.
  - `set_item_quantity(s, item_id: int, data: ItemQtyIn) -> SaleItem`, `remove_item(s, item_id: int) -> None`, `discard_sale(s) -> None` (só da venda `open`; item de venda não aberta → `InvalidTransition`)
  - `over_stock(item: SaleItem) -> bool` (quantidade da linha > `product.current_stock`; usado pela tela para o aviso "acima do estoque (disponível X)")

- [ ] **Step 1: Write failing tests:**
  - `test_add_by_barcode_creates_open_sale` (total = preço).
  - `test_same_product_merges_line`: `ARR5` e depois `2*ARR5` → 1 linha, quantidade 3.
  - `test_price_frozen_on_line`: adiciona, muda `product.sale_price`, adiciona de novo → linha mantém o preço antigo; `total` = 2 × preço antigo.
  - `test_kg_requires_weight` e `test_kg_with_weight` (`0,350*QMU` a 49,90 → `17.47`).
  - `test_fractional_un_refused` (`1,5*ARR5` → `InvalidInput`).
  - `test_no_price_refused`, `test_add_without_session_refused`.
  - `test_over_stock_flag_does_not_block` (estoque 1, adiciona 3 → item criado e `over_stock` verdadeiro).
  - `test_remove_and_discard` (discard apaga venda e itens; `current_sale` volta `None`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): carrinho com venda em aberto persistida`.

---

### Task 6: Pagamentos, conclusão e ajuste de falta

**Files:**
- Modify: `src/giro/pos/service.py`, `src/giro/inventory/service.py`
- Test: `tests/integration/test_pos_complete.py`

**Interfaces:**
- Consumes: `validate_new_payment`, `compute_change` (Task 2), `_lock_product`, `record_movement` (inventory).
- Produces:
  - `add_payment(s, data: PaymentIn) -> SalePayment` (valida com `validate_new_payment` contra o total atual), `remove_payment(s, payment_id: int) -> None`
  - `complete_sale(s, *, now) -> Sale`: trava a venda (`FOR UPDATE`, `populate_existing`); estado ≠ `open` → `InvalidTransition`; sem itens → `InvalidInput("Adicione ao menos um item antes de concluir.")`; `compute_change`; trava produtos em ordem de id; se faltar, `InsufficientStock` com `shortages` e mensagem `"Estoque insuficiente para concluir a venda."`; senão cria movimentos `sale` com `sale_id` (estender `record_movement` com parâmetro `sale_id: int | None = None`), grava `change_given`, `status`, `completed_at`.
  - `adjust_for_shortage(s, data: AdjustShortageIn, *, now) -> StockMovement`: calcula `needed − available` para o produto na venda `open`; se já não falta, `InvalidInput("Este item não está mais em falta.")`; cria `adjustment` com nota `f"Ajuste no caixa: {reason}"`.

- [ ] **Step 1: Write failing tests:**
  - `test_complete_deducts_stock_with_sale_id` (movimento `sale`, `sale_id` preenchido, estoque baixado, status `completed`).
  - `test_complete_split_payment_with_change` (total 30: Pix 10 + dinheiro 50 → `change_given == 30.00`).
  - `test_complete_empty_refused`.
  - `test_complete_shortage_rolls_back`: dois itens, um em falta → `InsufficientStock` com `shortages == [Shortage(id, nome, Decimal(1), Decimal(3))]`; após `session.rollback()` nenhum movimento existe e a venda segue `open` com itens.
  - `test_adjust_then_complete` (ajuste cria `adjustment` de +2 com a nota; conclusão passa).
  - `test_payment_validation_at_entry` (Pix acima do restante → `PaymentMismatch`; valor 0 → `PaymentMismatch`).
  - `test_concurrent_complete_deducts_once`: no padrão de `tests/integration/test_orders.py::test_claim_only_once` (duas threads, sessões próprias, `engine`), só uma conclusão vence; a outra recebe `InvalidTransition`; existe um único movimento `sale` por item.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run python -m pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(caixa): pagamento dividido, conclusão com baixa e ajuste de falta`.

---

### Task 7: Cancelamento, resumo e fechamento do turno

**Files:**
- Modify: `src/giro/pos/service.py`
- Test: `tests/integration/test_pos_close.py`

**Interfaces:**
- Produces:
  - `cancel_sale(s, sale_id: int, data: CancelSaleIn, *, now) -> Sale`: trava a venda; `ensure_transition(COMPLETED→CANCELLED)`; turno fechado → `InvalidTransition("Não é possível cancelar venda de um caixa já fechado.")`; um movimento `sale_cancel` por item com `sale_id`.
  - `@dataclass(frozen=True) class SessionSummary: by_method: dict[PaymentMethod, Decimal]; sales_count: int; sales_total: Decimal; cancelled_count: int; supplies: Decimal; withdrawals: Decimal; expected_cash: Decimal` (dinheiro em `by_method` já líquido de troco)
  - `session_summary(s, session_id: int) -> SessionSummary`
  - `close_session(s, data: CloseSessionIn, *, now) -> CashSession`: trava o turno; venda `open` → `InvalidTransition("Conclua ou descarte a venda em andamento antes de fechar o caixa.")`; grava `expected_amount`, `counted_amount`, `difference`, `close_note`, `closed_at`.
  - `get_sale(s, sale_id) -> Sale`, `get_session(s, session_id) -> CashSession` (`NotFound`), `list_sessions(s, *, limit: int = 50) -> list[CashSession]` (mais recentes primeiro), `list_session_sales(s, session_id) -> list[Sale]` (concluídas e canceladas, mais recentes primeiro).

- [ ] **Step 1: Write failing tests:** cancelar devolve estoque e cria `sale_cancel`; cancelar duas vezes → `InvalidTransition`; cancelar venda de turno fechado → `InvalidTransition`; `session_summary` com 2 vendas (uma cancelada), troco e sangria bate com valores calculados à mão; venda cancelada fora do esperado; fechar com venda aberta → recusado; fechar com contado 95 e esperado 100 → `difference == Decimal("-5.00")`; depois do fechamento `current_session` é `None` e um novo turno abre.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): cancelamento com estorno e fechamento conferido`.

---

### Task 8: Relatórios com venda líquida e indicadores de vendas

**Files:**
- Modify: `src/giro/reports.py`
- Test: `tests/integration/test_reports.py`

**Interfaces:**
- Produces:
  - `_sales_by_day` e `daily_sales` somam `sale` e subtraem `sale_cancel` (`CASE` na soma, filtro `kind IN (sale, sale_cancel)`); `_first_sales` continua só com `sale`.
  - `@dataclass(frozen=True) class SalesKpis: revenue_today: Decimal; revenue_month: Decimal; sales_month: int; avg_ticket_month: Decimal | None; margin_month: Decimal | None; items_with_cost: int; items_month: int; by_method_month: dict[PaymentMethod, Decimal]`
  - `sales_kpis(s: Session, *, now: datetime, tz: str) -> SalesKpis`: só vendas `completed`, por `completed_at` no fuso da loja; margem = `Σ(line_total − round_half_up(quantity × unit_cost))` sobre itens com custo, `None` se nenhum; ticket médio `None` sem vendas; dinheiro em `by_method_month` líquido de troco.

- [ ] **Step 1: Write failing tests:** saída do dia e previsão descontam venda cancelada; `sales_kpis` com vendas de hoje, do mês passado (fora) e uma cancelada (fora); margem só sobre itens com custo e `items_with_cost`/`items_month` corretos; sem vendas → `avg_ticket_month is None` e `margin_month is None`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run python -m pytest -q` → PASS (testes antigos de relatório continuam verdes).
- [ ] **Step 5: Commit** `feat(caixa): relatórios com venda líquida e indicadores de vendas`.

---

### Task 9: API JSON do caixa

**Files:**
- Create: `src/giro/api/pos.py`
- Modify: `src/giro/main.py`
- Test: `tests/api/test_api_pos.py`

**Interfaces:**
- Consumes: todo `pos.service` e `pos.schemas`; `SessionDep`, `NowDep`.
- Produces (prefixo `/api/pos`, cada rota faz `s.commit()` ao final como as rotas atuais):
  - `POST /session` (201, `CashSessionOut`), `GET /session` (`CashSessionOut | None`), `POST /session/movements` (201), `GET /session/summary` (`SessionSummaryOut`), `POST /session/close`
  - `GET /sale` (`SaleOut | None`), `POST /sale/items` (201), `PATCH /sale/items/{item_id}`, `DELETE /sale/items/{item_id}` (204), `DELETE /sale` (204), `POST /sale/payments` (201), `DELETE /sale/payments/{payment_id}` (204), `POST /sale/complete`, `POST /sale/adjust`
  - `GET /sales/{sale_id}`, `POST /sales/{sale_id}/cancel`
  - `InsufficientStock` na API responde 409 com `{"detail": ..., "shortages": [...]}` (ajustar `_domain_error_handler` para incluir `shortages` quando houver).

- [ ] **Step 1: Write failing tests:** fluxo completo pela API (abrir, adicionar, pagar, concluir, cancelar, fechar); 409 com `shortages`; 409 `NoOpenSession`; 422 em pagamento inválido.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): API JSON do caixa`.

---

### Task 10: Tela do PDV (`/caixa`)

**Files:**
- Create: `src/giro/web/pos.py`, `templates/pos.html`, `templates/pos_open.html`, `templates/_pos_panel.html`, `src/giro/web/static/pos.js`
- Modify: `src/giro/web/routes.py` (incluir router), `templates/base.html` (menu "Caixa" primeiro, `nav == "pos"`), `src/giro/web/templating.py` (globais `METHOD_LABELS`, `SALE_STATUS_LABELS`), `static/app.css`
- Test: `tests/web/test_pos_web.py`

**Interfaces:**
- Consumes: `pos.service` (Tasks 4 a 7).
- Produces: rotas HTML. Ações HTMX respondem sempre 200 com o fragmento `_pos_panel.html` inteiro (carrinho + pagamentos + total + mensagem), seguindo a convenção de `web/orders.py` de devolver erro de domínio dentro do fragmento.
  - `GET /caixa` (abrir ou PDV), `POST /caixa/abrir`, `POST /caixa/itens` (`entry`, `weight` opcional), `POST /caixa/itens/{id}` (quantidade), `POST /caixa/itens/{id}/remover`, `POST /caixa/descartar`, `POST /caixa/pagamentos`, `POST /caixa/pagamentos/{id}/remover`, `POST /caixa/concluir`, `POST /caixa/ajustar`, `POST /caixa/movimentos`.
  - Fluxo kg: `POST /caixa/itens` com produto `kg` sem quantidade devolve o painel com campo "Peso (kg)" focado e o código preservado em campo oculto.
  - Falta de estoque: painel lista as faltas, cada uma com formulário "Ajustar estoque e continuar" (motivo obrigatório) que faz `adjust_for_shortage` e tenta `complete_sale` de novo.
  - Conclusão OK: painel vazio com `"Venda #N concluída · troco R$ X"`.
  - `pos.js` (sem dependência): mantém foco no campo `#entry` após cada swap HTMX (`htmx:afterSwap`), `F2` foca o código, `F4` envia concluir, `Esc` fecha o painel aberto; destaque do último item via classe CSS com transição desativada em `prefers-reduced-motion`.

- [ ] **Step 1: Write failing tests:** `GET /caixa` sem turno mostra "Abrir caixa"; com turno mostra o campo `id="entry"` com `autofocus`; `POST /caixa/itens` com código inexistente devolve 200 com "Produto não encontrado" e campo vazio; produto kg sem peso devolve campo de peso; falta de estoque mostra "Ajustar estoque e continuar"; conclusão mostra "Venda #1 concluída"; menu tem "Caixa" como primeiro link.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Valores de formulário via `parse_decimal`; erros de validação Pydantic viram mensagem no painel com `form_errors`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): tela do PDV com carrinho, pagamento e ajuste`.

---

### Task 11: Fechamento, turnos e detalhe da venda

**Files:**
- Modify: `src/giro/web/pos.py`
- Create: `templates/pos_close.html`, `templates/pos_sessions.html`, `templates/pos_session_detail.html`, `templates/pos_sale_detail.html`
- Test: `tests/web/test_pos_web.py`

**Interfaces:**
- Consumes: `session_summary`, `close_session`, `cancel_sale`, `list_sessions`, `list_session_sales`, `get_sale`, `get_session` (Task 7).
- Produces: `GET /caixa/fechar` + `POST /caixa/fechar` (redireciona para `/caixa/turnos/{id}`), `GET /caixa/turnos`, `GET /caixa/turnos/{id}`, `GET /caixa/vendas/{id}`, `POST /caixa/vendas/{id}/cancelar` (motivo obrigatório; redireciona ao detalhe). "Vendas do turno" no PDV linka para `/caixa/turnos/{id}` do turno atual.

- [ ] **Step 1: Write failing tests:** fechamento mostra esperado e aceita contado, mostrando "Falta R$ 5,00" ou "Sobra R$ 5,00"; fechar com venda aberta mostra a mensagem do serviço; turno `is_demo` aparece com "Dados de demonstração"; detalhe da venda cancelada mostra o motivo; cancelar pela tela devolve estoque.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): fechamento, histórico de turnos e cancelamento pela tela`.

---

### Task 12: Faixa "Vendas" na Visão geral

**Files:**
- Modify: `src/giro/web/routes.py`, `templates/overview.html`, `src/giro/web/static/chart.js`
- Test: `tests/web/test_overview.py`

**Interfaces:**
- Consumes: `sales_kpis` (Task 8).
- Produces: seção `kpis` "Vendas" com Faturamento hoje, Faturamento no mês, Ticket médio, Margem bruta (texto auxiliar `"sobre N de M itens com custo"`; `"sem custo cadastrado"` quando `None`), e canvas `id="payments-chart"` com os dados em `data-` atributos (rótulos de `METHOD_LABELS`, valores em reais) desenhado como barras pelo `chart.js` local.

- [ ] **Step 1: Write failing tests:** com uma venda concluída a Visão geral mostra `R$` correto em Faturamento hoje; sem vendas mostra `R$ 0,00` e "sem vendas no mês" no lugar do ticket; margem sem custo mostra "sem custo cadastrado".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(caixa): indicadores de vendas na visão geral`.

---

### Task 13: Seed, e2e e documentação

**Files:**
- Modify: `src/giro/seed.py`, `tests/integration/test_seed.py`, `tests/e2e/test_smoke.py`, `README.md`, `docs/superpowers/specs/2026-11-20-giro-caixa-design.md` (§10: testes rodam com `uv run python -m pytest`, nativo)
- Test: os próprios

**Interfaces:**
- Consumes: `pos.service` (abrir, adicionar, pagar, concluir, fechar) para gerar os turnos de demonstração.
- Produces: `DemoProduct` ganha `price: str` e `cost: str`; 6 produtos ganham `barcode` com EAN-13 válido (verificado por `is_valid_gtin` no teste do seed). O seed cria 3 turnos fechados `is_demo=True` nos últimos 3 dias com algumas vendas cada, via serviço (estoque baixado de verdade), com `random.Random(seed fixo)` como o seed atual.

- [ ] **Step 1: Write failing tests:** seed preenche preço e custo em todos os produtos; barcodes válidos; existem 3 turnos fechados `is_demo`; rodar o seed de novo não duplica nada. E2E: abrir caixa (ou usar o aberto), adicionar por código, pagar dividido com troco, concluir, cancelar, fechar com diferença; ao final desativa/limpa o que criou, no padrão de `tests/e2e/test_smoke.py`.
- [ ] **Step 2: Run** `uv run python -m pytest tests/integration/test_seed.py -v` → FAIL.
- [ ] **Step 3: Implement** seed; README ganha seção "Caixa" (como abrir, atalhos F2/F4/Esc, leitor USB funciona como teclado no mesmo campo) e "Limitações" ganha: sem emissão fiscal (NFC-e/SAT), sem desconto, sem comprovante impresso.
- [ ] **Step 4: Run** `uv run python -m pytest -q` → PASS; `docker compose up -d --build --wait` e `uv run python -m pytest -m e2e` → PASS (checar `docker ps` antes por conflito de portas 8000/5432).
- [ ] **Step 5: Commit** `feat(caixa): dados de demonstração, e2e e documentação`.
