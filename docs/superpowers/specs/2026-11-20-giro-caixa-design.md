# Giro: Caixa (PDV + turno), design

**Data:** 2026-11-20
**Status:** aprovado em conversa; aguardando revisão da spec escrita
**Branch:** `feat/caixa`

Ponto de venda de caixa único, integrado ao estoque do Giro: venda com carrinho por código (SKU ou código de barras), pagamento registrado manualmente (inclusive dividido), baixa automática no estoque e turno de caixa com abertura, sangria, suprimento e fechamento conferido.

## 1. Objetivo e contexto

- **Para quem:** pequeno comércio com **um único caixa**, operado pelo próprio dono. Sem cadastro de operador.
- **Por quê:** hoje a venda entra no Giro como movimentação manual. O caixa passa a ser a forma normal de vender, e o estoque, a previsão e as sugestões de pedido ao fornecedor passam a ser alimentados automaticamente.
- **Sucesso:**
  1. Com o caixa aberto, digitar um código e apertar Enter adiciona o produto ao carrinho; a venda é concluída com uma ou mais formas de pagamento, com troco calculado.
  2. Concluir a venda baixa o estoque na mesma transação; faltando estoque, nada é gravado e o sistema oferece ajuste com motivo e nova tentativa.
  3. Cancelar uma venda concluída devolve os itens ao estoque sem apagar registros.
  4. O fechamento mostra o dinheiro esperado, recebe o contado e grava a diferença.
  5. A Visão geral mostra faturamento, ticket médio, margem bruta e vendas por forma de pagamento.

### Decisões tomadas

| Tema | Decisão |
|---|---|
| Escopo | PDV + turno de caixa (abertura, sangria, suprimento, fechamento com conferência) |
| Caixas | Um único caixa; no máximo 1 turno aberto e 1 venda aberta (garantido no banco) |
| Operador | Sem registro de operador; sem login (limitação já documentada do Giro) |
| Pagamento | Registro manual: dinheiro (com troco), Pix, crédito, débito; pagamento dividido permitido |
| Entrada de produto | Campo único aceita código de barras ou SKU, com Enter; leitor USB (que funciona como teclado) usará o mesmo campo numa feature futura |
| Preços | Preço de venda (obrigatório para vender) e custo (opcional, para margem) no produto; copiados para o item da venda |
| Sem estoque | Bloqueia a conclusão e oferece "Ajustar estoque e continuar" com motivo obrigatório |
| Carrinho | Venda `open` persistida no banco (abordagem A); estoque baixado só ao concluir |
| Dashboard | Faixa "Vendas" na Visão geral |

### Fora do escopo

Emissão fiscal (NFC-e/SAT), obrigatória para venda real ao consumidor e documentada como limitação; desconto; comprovante impresso; leitor de código de barras físico (fluxo já preparado); balança e etiqueta de peso; devolução parcial; gateway de pagamento; vários caixas simultâneos; operadores.

## 2. Arquitetura

- Novo módulo `src/giro/pos/`: `service.py` (regras), `schemas.py` (entrada/saída Pydantic), `state.py` (máquina de estados da venda), `money.py` (arredondamento e validação de pagamento), `codes.py` (parse de `3*CODIGO` e validação de EAN).
- Rotas JSON em `src/giro/api/pos.py` (`/api/pos/...`) e telas HTMX em `src/giro/web/pos.py` (`/caixa...`), chamando a **mesma camada de serviço**.
- Como no resto do Giro, o serviço **não faz commit**: quem chama decide. Estoque continua sendo alterado só por `inventory.service.record_movement` (ou por função equivalente no mesmo módulo que reusa `_lock_product`).
- O n8n não participa do caixa. WF1 e sugestões de pedido continuam lendo o estoque, que segue sendo a fonte da verdade.

## 3. Modelo de dados

### 3.1 Mudanças em tabelas existentes

`products`:

| Campo | Tipo | Regra |
|---|---|---|
| `barcode` | `VARCHAR(14)`, nulo | só dígitos; EAN-8, UPC-A (12) ou EAN-13 com dígito verificador válido; único quando preenchido |
| `sale_price` | `NUMERIC(12,2)`, nulo | `> 0` quando preenchido; produto sem preço não pode ser vendido no caixa |
| `cost_price` | `NUMERIC(12,2)`, nulo | `>= 0` quando preenchido; usado só para margem |

`stock_movements`:

- nova coluna `sale_id` (FK `sales.id`, nulo);
- novo tipo de movimento `sale_cancel` (quantidade `> 0`, soma ao estoque). O `CHECK ck_movements_kind` passa a aceitar `'sale', 'purchase', 'adjustment', 'sale_cancel'`.

### 3.2 Tabelas novas

| Tabela | Campos |
|---|---|
| `cash_sessions` | `id`, `opened_at`, `opening_amount`, `closed_at` (nulo), `expected_amount` (nulo), `counted_amount` (nulo), `difference` (nulo), `close_note` (nulo), `is_demo` (padrão falso) |
| `cash_movements` | `id`, `session_id`, `kind` (`supply` / `withdrawal`), `amount` (`> 0`), `note` (nulo), `occurred_at` |
| `sales` | `id`, `session_id`, `status` (`open` / `completed` / `cancelled`), `total`, `change_given` (padrão 0), `created_at`, `completed_at` (nulo), `cancelled_at` (nulo), `cancel_reason` (nulo) |
| `sale_items` | `id`, `sale_id`, `product_id`, `quantity` (`NUMERIC(12,3)`, `> 0`), `unit_price`, `unit_cost` (nulo), `line_total` |
| `sale_payments` | `id`, `sale_id`, `method` (`cash` / `pix` / `credit` / `debit`), `amount` (`> 0`) |

Valores em dinheiro são `NUMERIC(12,2)`. Enums seguem o padrão atual (`native_enum=False` + `CHECK`).

### 3.3 Regras

- **Um caixa só:** índice único parcial em `cash_sessions` onde `closed_at IS NULL`, e em `sales` onde `status = 'open'`.
- **Preço congelado:** ao entrar no carrinho, o item copia `sale_price` e `cost_price` do produto. Mudanças de preço posteriores não alteram vendas existentes, nem itens já no carrinho.
- **Mesma linha:** adicionar um produto que já está no carrinho soma à quantidade da linha existente.
- **Quantidade por unidade:** `un`/`cx` aceitam só inteiros; `kg` aceita até 3 casas (mesma regra de `ensure_quantity_fits_unit`).
- **Total da linha:** `line_total = round_half_up(quantity × unit_price, 2)`. Total da venda é a soma das linhas.
- **Pagamento:**
  - soma dos pagamentos não-dinheiro `<=` total;
  - soma de todos os pagamentos `>=` total;
  - excedente só pode vir de dinheiro e vira `change_given`;
  - ao gravar, o pagamento em dinheiro guarda o valor recebido; o dinheiro que fica no caixa é `recebido em dinheiro − troco`.
- **Máquina de estados da venda** (`pos/state.py`): `open → completed`, `completed → cancelled`. Venda `open` pode ser **descartada** (apagada com seus itens, pois nada foi baixado). Qualquer outra transição gera `InvalidTransition`.
- **Cancelamento:** só de venda `completed` cujo turno ainda está aberto; motivo obrigatório; cria um movimento `sale_cancel` por item com `sale_id`.
- **Esperado em dinheiro no turno:** `opening_amount + Σ supply − Σ withdrawal + Σ (dinheiro recebido − troco)` das vendas `completed` do turno (vendas canceladas não contam).
- **Sangria** não pode deixar o esperado em dinheiro negativo.
- **Fechamento:** proibido com venda `open`; grava `expected_amount`, `counted_amount`, `difference = counted − expected` e `closed_at`.

## 4. Fluxos

### 4.1 Abrir turno

`/caixa` sem turno aberto mostra só "Abrir caixa" com o troco inicial (`>= 0`). Com turno aberto, mostra o PDV. Segunda abertura gera `SessionAlreadyOpen` (o índice único é a garantia final; `IntegrityError` vira o mesmo erro de domínio, como já é feito com SKU duplicado).

### 4.2 Adicionar item

1. Campo de código sempre focado. Entrada `[N*]CODIGO` + Enter.
2. Busca: `barcode` exato, depois `sku` exato. Produto inativo conta como não encontrado.
3. Recusas: "Produto não encontrado", "Produto sem preço de venda: cadastre o preço antes de vender".
4. Quantidade: `un`/`cx` entram com `N` (padrão 1). `kg` sem `N` abre o campo de peso antes de adicionar; com `N` (ex.: `0,350*CODIGO`) usa o valor informado.
5. Sem venda `open`, cria uma no turno atual.
6. Se a quantidade no carrinho passar do estoque atual, a linha mostra o aviso "acima do estoque (disponível X)". Não bloqueia.
7. Cada linha permite editar a quantidade ou remover. "Descartar venda" apaga a venda `open`.

### 4.3 Concluir venda

1. Trava a venda (`FOR UPDATE`); se não estiver `open`, `InvalidTransition` (protege contra duplo clique e duas abas).
2. Valida os pagamentos (§3.3); se não fecharem, `PaymentMismatch`.
3. Trava os produtos em ordem de id e confere o saldo de cada item (somando linhas do mesmo produto, que já são únicas por regra).
4. Faltando estoque, `InsufficientStock` com a lista de faltas (produto, disponível, necessário) e **rollback total**.
5. Senão, na mesma transação: um movimento `sale` por item (com `sale_id`, `occurred_at` = agora), grava os pagamentos e `change_given`, `status=completed`, `completed_at`.
6. A tela mostra "Venda #N concluída · troco R$ X" e volta ao carrinho vazio.

### 4.4 Ajustar estoque e continuar

Para cada item em falta, o botão abre o motivo (obrigatório). Confirmar cria um `adjustment` de `+(necessário − disponível)` com nota `Ajuste no caixa: <motivo>` e repete a conclusão com os mesmos pagamentos já lançados.

### 4.5 Cancelar venda

Pela lista "Vendas do turno". Trava a venda, exige motivo, cria os movimentos `sale_cancel`, grava `cancelled_at` e `cancel_reason`. A venda continua listada como cancelada.

### 4.6 Sangria e suprimento

Valor `> 0` e observação opcional; sangria respeita §3.3.

### 4.7 Fechar caixa

Trava o turno. Com venda `open`, recusa com "Conclua ou descarte a venda em andamento". A tela de fechamento mostra o resumo (vendas por forma de pagamento, quantidade de vendas, canceladas, sangrias, suprimentos) e o esperado em dinheiro; recebe o contado (`>= 0`) e a observação opcional. Depois do fechamento, o turno é só leitura.

## 5. Telas

Menu ganha **"Caixa"** como primeiro item.

| Rota | Conteúdo |
|---|---|
| `/caixa` | Abrir caixa ou PDV |
| `/caixa/fechar` | Resumo e fechamento do turno atual |
| `/caixa/turnos` | Histórico de turnos (demo identificado como "Dados de demonstração") |
| `/caixa/turnos/{id}` | Detalhe do turno, só leitura |
| `/caixa/vendas/{id}` | Detalhe da venda: itens, pagamentos, troco, motivo do cancelamento |

**PDV** (duas colunas, pensado para teclado):

- Esquerda: campo de código no topo; carrinho abaixo (nome, qtd × preço, total da linha, editar, remover); último item adicionado destacado por um instante.
- Direita: total em destaque; pagamentos lançados e restante; botões Finalizar (`F4`), Descartar, Sangria, Suprimento, Vendas do turno, Fechar caixa.
- `F2` devolve o foco ao código; `Esc` fecha o painel aberto. Atalhos escritos ao lado dos botões.
- Mensagens de erro numa faixa abaixo do campo, sem tirar o foco; o campo é limpo após cada tentativa.
- Valores em formato brasileiro via `fmt.py`.

**Produtos:** formulários ganham "Código de barras", "Preço de venda" e "Custo"; tabela mostra o preço; detalhe avisa quando não há preço.

**Visão geral:** faixa "Vendas" com faturamento de hoje e do mês, ticket médio do mês, margem bruta do mês (com "calculada sobre N de M itens com custo cadastrado") e gráfico de barras por forma de pagamento no mês (Chart.js já vendorizado). Considera só vendas `completed`, no fuso da loja.

**Regras de UI do projeto:** sem botões pílula, sem emoji como ícone, sem números inventados, movimento só funcional e respeitando `prefers-reduced-motion`.

## 6. Relatórios e previsão

`reports.py` passa a usar **venda líquida**: `sale` soma e `sale_cancel` subtrai, no dia local do `occurred_at` de cada movimento. Isso vale para saída do dia/mês, série diária e previsão (`forecast.py` recebe as mesmas séries já líquidas, sem mudar a fórmula). Como o cancelamento só é possível com o turno aberto, o estorno cai, na prática, no mesmo período da venda.

O formulário "Registrar" continua permitindo venda manual.

## 7. Erros

Novos em `errors.py`, todos `DomainError` com mensagem pt-BR e o mesmo mapeamento HTTP atual (422 para regra, 404 para não encontrado, 409 para conflito de estado):

| Erro | Quando |
|---|---|
| `NoOpenSession` | vender, sangrar, suprir ou fechar sem turno aberto |
| `SessionAlreadyOpen` | abrir segundo turno |
| `PaymentMismatch` | pagamentos não fecham o total; excedente fora do dinheiro |

Reusados: `InvalidTransition` (venda já concluída/cancelada, cancelar venda de turno fechado), `InsufficientStock` (ganha atributo opcional `shortages` com a lista de faltas), `InvalidInput` (quantidade, peso, código de barras inválido ou duplicado, produto sem preço), `NotFound`.

Qualquer erro na conclusão desfaz a transação inteira; a venda continua `open` com os itens intactos.

## 8. Migração

`0003_pos`: colunas novas em `products` e `stock_movements`; as 5 tabelas; índices únicos parciais (turno aberto, venda aberta, `barcode`); `CHECK`s de valores; troca do `ck_movements_kind`. Downgrade reverte tudo (falha se já houver movimentos `sale_cancel`, com mensagem clara). Produtos existentes ficam sem preço até serem editados.

## 9. Dados de demonstração

O seed passa a preencher `sale_price`, `cost_price` e alguns `barcode` dos produtos de demonstração e cria alguns turnos fechados com vendas (`is_demo = true`), exibidos como "Dados de demonstração" no histórico. Nada é apresentado como resultado real.

## 10. Testes

TDD; testes rodam no Docker (o Windows bloqueia `.pyd` da venv).

- **Unit:** arredondamento de linha em kg; validação de pagamento (dividido, troco, excesso em cartão/Pix); parse de `N*CODIGO` com vírgula decimal; validação de EAN-8/12/13; máquina de estados da venda.
- **Integração (Postgres real):** concluir baixa o estoque com `sale_id`; concluir com falta não grava nada; ajuste e nova tentativa; cancelar devolve o estoque e sai da venda líquida; esperado do fechamento; índices únicos impedem 2 turnos e 2 vendas abertas; dois "concluir" concorrentes resultam em uma só baixa; fechamento recusado com venda aberta; relatórios e previsão com cancelamento.
- **API e web:** rotas JSON e fragmentos HTMX (status, mensagens, campo limpo e focado).
- **E2E smoke:** abrir caixa, adicionar itens por código, pagar dividido com troco, cancelar uma venda, fechar com diferença; limpa os dados no fim, como o e2e atual.

## 11. Versões

O projeto continua em Python 3.14 (regra do workspace: migração para 3.15 só de propósito). Qualquer dependência nova é fixada na versão estável vigente, verificada no momento da implementação.
