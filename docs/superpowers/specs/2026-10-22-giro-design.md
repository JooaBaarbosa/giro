# Giro — Design (MVP)

**Data:** 2026-10-22
**Status:** aprovado em conversa; aguardando revisão da spec escrita

Dashboard de estoque para pequeno comércio, com alerta de item abaixo do mínimo e pedido ao fornecedor via WhatsApp, orquestrado por n8n e aprovado pelo dono.

## 1. Objetivo e contexto

- **Propósito:** projeto de portfólio (LinkedIn) **desenhado para uso real** por um comércio pequeno depois.
- **Sucesso do MVP:**
  1. Dono vê lista de itens com estoque atual, saída no dia e no mês, e previsão da próxima compra.
  2. Item abaixo do mínimo gera pedido sugerido (agrupado por fornecedor) e alerta no WhatsApp do dono.
  3. Dono aprova no dashboard; o pedido é enviado ao fornecedor via WhatsApp (ou simulado em dry-run).
  4. `git clone` + `docker compose up` sobe tudo funcionando em dry-run com dados de demonstração.

### Decisões tomadas

| Tema | Decisão |
|---|---|
| Entrada de dados | Registro manual no dashboard; API REST pronta para planilha/PDV futuros |
| Envio do pedido | Sempre com aprovação do dono, via botão no dashboard |
| WhatsApp | Meta WhatsApp Cloud API oficial, com modo dry-run (sem conta Meta ainda) |
| Stack | Python 3.14 + FastAPI + HTMX/Jinja + Chart.js; PostgreSQL; n8n; `docker compose` |
| Fornecedor | 1 fornecedor por produto |
| Autenticação de usuário | Fora do MVP (limitação documentada; obrigatória antes de expor à internet) |

### Fora do escopo do MVP

Login/multiusuário, multiloja, importação de planilha, integração com PDV/e-commerce, múltiplos fornecedores por produto, previsão com sazonalidade, interpretação de respostas do WhatsApp.

## 2. Arquitetura

```
┌───────────── api (FastAPI) ─────────────┐     ┌──── n8n ────┐
│  /        dashboard HTMX + Chart.js      │◄────┤ cron 15 min │
│  /api/... REST JSON (mesmas regras)      │────►│ webhooks    │──► WhatsApp Cloud API
└──────────────────┬───────────────────────┘     └──────┬──────┘    (ou dry-run)
                   ▼                                    ▼
             postgres (db "giro")                postgres (db "n8n")
```

- **A API é a fonte da verdade.** Toda regra de negócio fica na API. O n8n só orquestra (agenda, chama a API, envia mensagens) e não acessa o banco da aplicação.
- Rotas HTMX (`web/`) e JSON (`api/`) chamam a **mesma camada de serviço**.
- Um único Postgres com dois bancos: `giro` (aplicação) e `n8n`.

## 3. Modelo de dados

| Tabela | Campos |
|---|---|
| `suppliers` | `id`, `name`, `whatsapp` (E.164), `active`, `created_at` |
| `products` | `id`, `sku` (único), `name`, `unit` (`un`/`kg`/`cx`), `supplier_id`, `min_stock`, `cover_days` (padrão 15), `current_stock`, `active`, `created_at` |
| `stock_movements` | `id`, `product_id`, `kind` (`sale`/`purchase`/`adjustment`), `quantity`, `occurred_at`, `note`, `purchase_order_id` (nulo), `created_at` |
| `purchase_orders` | `id`, `supplier_id`, `status`, `message_text`, `dry_run`, `whatsapp_message_id`, `error`, `created_at`, `approved_at`, `claimed_at`, `sent_at`, `received_at` |
| `purchase_order_items` | `id`, `order_id`, `product_id`, `quantity` |

Regras:

- **Quantidades** em `NUMERIC(12,3)`. `sale` e `purchase` têm quantidade > 0; `adjustment` aceita valor com sinal (≠ 0).
- **`stock_movements` é um livro-razão só de inserção.** Correções são feitas com `adjustment`, nunca editando ou apagando registros.
- **`current_stock` é um cache** atualizado na mesma transação do movimento, com `SELECT ... FOR UPDATE` na linha do produto.
- **Estoque nunca fica negativo:** venda ou ajuste que deixaria saldo < 0 é recusado com `InsufficientStock`.
- **Máquina de estados do pedido** (`orders/state.py`):

```
sugerido ──aprovar──► aprovado ──claim──► enviando ──sent──► enviado ──receber──► recebido
   │                     │                    │
   └──cancelar──► cancelado ◄──cancelar──┘    ├──failed──► falhou ──reenviar──► aprovado
                                              └──timeout 10 min──► falhou
```

  Status no banco (em inglês): `suggested`, `approved`, `sending`, `sent`, `received`, `failed`, `cancelled`. Transições válidas (todas as outras geram `InvalidTransition`):
  - `suggested → approved | cancelled`
  - `approved → sending | cancelled`
  - `sending → sent | failed`
  - `sent → received`
  - `failed → approved` (reenviar) `| cancelled`
- **Recebimento:** ao marcar `received`, o sistema cria um movimento `purchase` por item, com o `purchase_order_id` preenchido, na mesma transação.
- **Pedido em aberto** = status `suggested`, `approved`, `sending`, `sent` ou `failed`. Um produto que já está em pedido aberto não entra em nova sugestão.
- Antes da aprovação, o dono pode editar a quantidade dos itens ou removê-los. Pedido sem itens não pode ser aprovado.

## 4. Dashboard

Sem autenticação no MVP. Interface em pt-BR.

1. **Visão geral (`/`)**
   - Indicadores: itens abaixo do mínimo, saída hoje (soma de quantidade de `sale`), saída no mês, pedidos aguardando aprovação.
   - Tabela: nome, estoque atual, mínimo, saída hoje, saída no mês, consumo médio/dia, dias restantes, data da próxima compra e status. Ordenada por urgência, com busca por nome/SKU.
   - Status: 🔴 `current_stock < min_stock`; 🟡 próxima compra em ≤ 7 dias; 🟢 demais.
   - Gráfico (Chart.js): saídas por dia nos últimos 30 dias, total da loja.
2. **Detalhe do item (`/products/{id}`)**: gráfico diário de saídas do item (30 dias), histórico de movimentações, edição de mínimo, cobertura e fornecedor.
3. **Registrar movimento (`/movements/new`)**: busca do item, quantidade, tipo e observação. Enter salva; o formulário limpa e mantém o foco.
4. **Pedidos (`/orders`)**: lista por status.
   - `suggested`: itens com quantidade editável, prévia da mensagem, **Aprovar**, **Cancelar**.
   - `approved`/`sending`: "aguardando envio".
   - `sent`: mensagem enviada, selo **simulado** se `dry_run`, botão **Marcar recebido**.
   - `failed`: erro e botão **Reenviar**.
5. **Cadastros (`/products`, `/suppliers`)**: CRUD simples. Exclusão = desativar (`active = false`).

"Hoje" e "mês" usam o fuso `America/Sao_Paulo` (configurável por `STORE_TZ`).

## 5. Previsão (`forecast.py`)

Função pura, sem acesso ao banco: recebe o estoque, o mínimo, a cobertura e as vendas diárias, e devolve o resultado.

```
janela         = dias entre a primeira venda do produto e hoje, limitado a 30 (mínimo 1)
consumo_medio  = soma das vendas na janela ÷ janela
dias_restantes = (current_stock − min_stock) ÷ consumo_medio
proxima_compra = hoje + floor(dias_restantes)   (hoje, se dias_restantes ≤ 0)
qtd_sugerida   = ceil(consumo_medio × cover_days + min_stock − current_stock), mínimo 1
```

- Sem vendas nos últimos 30 dias: `consumo_medio = 0` e a previsão vem marcada como "sem previsão". Se o item já estiver abaixo do mínimo, `qtd_sugerida = ceil(min_stock − current_stock)` (mínimo 1).
- Janela < 7 dias: resultado com a flag `low_history` (selo "pouco histórico").
- Para unidade `un`/`cx`, a quantidade sugerida é inteira. Para `kg`, arredonda para cima com 1 casa decimal.

## 6. Automação (n8n)

### Contrato da API para automação

Todas as rotas exigem o header `X-Service-Token: $SERVICE_TOKEN`. Sem ele, a resposta é `401`.

| Método e rota | Efeito |
|---|---|
| `POST /api/purchase-orders/suggest` | Cria pedidos `suggested` para itens abaixo do mínimo que não estão em pedido aberto, agrupados por fornecedor (fornecedor inativo é ignorado). Devolve **só os recém-criados**, com itens, fornecedor e link. Idempotente. |
| `GET /api/purchase-orders?status=approved` | Pedidos aprovados ainda não enviados. |
| `GET /api/purchase-orders/{id}` | Pedido completo (itens, fornecedor, texto da mensagem). |
| `POST /api/purchase-orders/{id}/claim` | Transição atômica `approved → sending`. Retorna `409` se o pedido não estiver `approved`. |
| `POST /api/purchase-orders/{id}/sent` | Corpo `{message_text, dry_run, whatsapp_message_id?}` → `sent`. |
| `POST /api/purchase-orders/{id}/failed` | Corpo `{error}` → `failed`. |

Ao aprovar no dashboard, a API faz `POST $N8N_WEBHOOK_URL/pedido-aprovado` com `{order_id}` (timeout de 3 s). Se o n8n falhar ou não responder, o pedido continua `approved` e o WF1 recupera.

### WF1: Verificar estoque (cron a cada 15 min)

1. `POST /suggest`. Para cada pedido novo, envia ao `OWNER_WHATSAPP` o template `alerta_estoque_baixo` com a quantidade de itens, a lista, o fornecedor e o link do pedido.
2. `GET ?status=approved`. Para cada pedido, dispara o WF2.
3. Pedidos `sending` com `claimed_at` há mais de 10 min passam para `failed` com o erro "envio não confirmado; verifique antes de reenviar". Essa varredura roda **na API**, dentro de `/suggest`, e não no n8n.

### WF2: Enviar pedido (webhook `pedido-aprovado`)

1. `POST /claim`. Se receber `409`, encerra sem enviar (outro disparo já pegou o pedido).
2. `GET /{id}` e monta os parâmetros do template `pedido_compra`.
3. Se `WHATSAPP_DRY_RUN=true`, não chama a Meta. Caso contrário, nó *WhatsApp Business Cloud* para o número do fornecedor.
4. Sucesso → `POST /sent`. Erro → `POST /failed` com a mensagem de erro da Meta.

### Templates Meta

- Parâmetros de template **não aceitam quebra de linha**. A lista de itens vai em uma linha: `Arroz 5kg: 30 un · Óleo 900ml: 24 un`.
- O texto dos templates e o passo a passo de submissão ficam em `docs/templates-meta.md`. A criação da conta Meta e do número de teste fica em `docs/whatsapp-setup.md`.
- A API gera `message_text` (prévia exibida no dashboard) com o mesmo formato do template.

### Versionamento

Workflows exportados em `n8n/workflows/*.json`, importados na subida do container (`n8n import:workflow`) e ativados automaticamente. A credencial da Meta vem de variáveis de ambiente.

## 7. Configuração (`.env`)

`DATABASE_URL`, `SERVICE_TOKEN`, `N8N_WEBHOOK_URL`, `APP_BASE_URL`, `STORE_NAME`, `STORE_TZ`, `OWNER_WHATSAPP`, `WHATSAPP_DRY_RUN` (padrão `true`), `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`. Um `.env.example` fica versionado.

## 8. Tratamento de erros

- **Validação:** Pydantic (quantidade, telefone E.164, SKU único). API → `422`; HTMX → erro ao lado do campo.
- **Domínio:** `InsufficientStock`, `InvalidTransition`, `EmptyOrder` → `409` na API, mensagem amigável na tela.
- **Não encontrado** → `404`.
- **Falhas do WhatsApp:** erro da Meta salvo em `purchase_orders.error`, exibido na tela com **Reenviar**.
- **Logs:** JSON estruturado, com `order_id` em toda operação de pedido.

## 9. Testes

- **Unitários:** `forecast.py` (sem histórico, pouco histórico, abaixo do mínimo, arredondamento `un`/`kg`) e a máquina de estados (todas as transições válidas e inválidas).
- **Integração (Postgres via testcontainers):** venda concorrente com lock; recusa de estoque negativo; `suggest` idempotente e respeitando pedido aberto; `claim` concorrente (só um vence); recebimento gerando entradas; timeout `sending → failed`.
- **API/web:** TestClient (`httpx`) nos endpoints JSON, no `X-Service-Token` e nos fragmentos HTMX principais.
- **Ponta a ponta (fumaça):** com o compose em dry-run, aprovar um pedido e verificar que ele chega a `sent` com `dry_run = true`.
- **CI:** GitHub Actions com `ruff`, `mypy` e `pytest` (unitários e integração; o teste ponta a ponta roda à parte).

## 10. Estrutura

```
giro/
├── pyproject.toml            # requires-python = ">=3.14,<3.15" · uv
├── .python-version           # 3.14.8
├── docker-compose.yml        # api · postgres · n8n
├── .env.example
├── src/giro/
│   ├── main.py               # app FastAPI
│   ├── config.py             # settings (pydantic-settings)
│   ├── db.py · models.py     # SQLAlchemy 2
│   ├── inventory/            # produtos, fornecedores, movimentos (serviço)
│   ├── forecast.py           # previsão (função pura)
│   ├── orders/               # pedidos, state.py, suggest, mensagem
│   ├── api/                  # rotas JSON
│   └── web/                  # rotas HTMX, templates Jinja, static
├── migrations/               # Alembic
├── scripts/seed.py           # mercearia fictícia: ~25 produtos, 4 fornecedores, 90 dias de vendas
├── n8n/workflows/            # wf1-verificar-estoque.json, wf2-enviar-pedido.json
├── docs/                     # whatsapp-setup.md, templates-meta.md, specs/
└── tests/
```

**Seed:** vendas com variação semanal (fim de semana maior). Alguns itens nascem abaixo do mínimo para que a demo mostre alerta e pedido logo após o `docker compose up`.

## 11. Evoluções (pós-MVP)

Login, importação CSV, webhook de PDV, previsão com sazonalidade, múltiplos fornecedores, resposta "SIM" pelo WhatsApp e troca da UI por React consumindo a mesma API.
