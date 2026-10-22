# Giro: estoque com pedidos automáticos via WhatsApp

Dashboard de estoque para pequeno comércio. Mostra o que saiu no dia e no mês, prevê quando cada item vai precisar de reposição e, quando algo fica abaixo do mínimo, monta o pedido para o fornecedor. **O dono aprova com um clique** e o n8n envia o pedido pelo WhatsApp (Meta Cloud API).

> Projeto de portfólio pensado para uso real: começa com uma mercearia fictícia e funciona inteiro em modo de simulação (dry-run), sem nenhuma conta externa.

## O que ele faz

- **Visão geral:** estoque atual, saída hoje e no mês, consumo médio, dias restantes, data da próxima compra e status (🔴 abaixo do mínimo · 🟡 comprar em breve · 🟢 ok).
- **Registro rápido** de venda, entrada ou ajuste. Funciona no balcão: Enter salva e o formulário limpa.
- **Previsão explicável:** média móvel de 30 dias → dias até o mínimo → data e quantidade sugerida para cobrir N dias.
- **Pedido sugerido** por fornecedor, sem duplicar. Os itens são editáveis antes de aprovar, e há uma prévia da mensagem.
- **Automação com n8n:** a cada 15 min, verifica o estoque e alerta o dono no WhatsApp. Na aprovação, envia o template ao fornecedor e registra o resultado (enviado, simulado ou falhou com o erro da Meta).

## Arquitetura

```
┌───────────── api (FastAPI) ─────────────┐     ┌──── n8n ────┐
│  /        dashboard HTMX + Chart.js      │◄────┤ cron 15 min │
│  /api/... REST JSON (mesmas regras)      │────►│ webhooks    │──► WhatsApp Cloud API
└──────────────────┬───────────────────────┘     └──────┬──────┘    (ou dry-run)
                   ▼                                    ▼
             postgres (db "giro")                postgres (db "n8n")
```

- **A API é a fonte da verdade.** O n8n só orquestra: chama a API, envia mensagens e devolve callbacks. Nenhuma regra de negócio mora no workflow.
- **Movimentações são um livro-razão** (só inserção). O saldo é atualizado na mesma transação, com `SELECT … FOR UPDATE`, e duas vendas simultâneas nunca deixam o estoque negativo.
- **O fornecedor nunca recebe o pedido duas vezes:** a transição `approved → sending` é um `UPDATE … WHERE status='approved'` atômico. Um envio sem confirmação por 10 min vira `falhou` e o pedido **não** é reenviado sozinho.
- **Resiliência:** se o n8n estiver fora do ar na aprovação, o pedido fica "aguardando envio" e o cron recupera.

Stack: Python 3.14 · FastAPI · SQLAlchemy 2 · PostgreSQL 17 · HTMX · Chart.js · n8n 2.44.6 · Docker Compose · pytest + testcontainers.

## Rodando

Pré-requisito: Docker.

```bash
git clone <este repositório> && cd giro
docker compose up -d --build
```

Depois:

1. **Dashboard:** http://localhost:8000. A mercearia de demonstração já vem com 5 itens abaixo do mínimo.
2. **n8n:** http://localhost:5678 (leva uns 30 s a mais para subir na primeira vez, enquanto importa os workflows). Crie o usuário dono no primeiro acesso; os dois workflows já estão importados e ativos.
3. Vá em **Pedidos**. Se ainda não houver sugestões (o cron roda a cada 15 min), gere na hora:

   ```bash
   curl -X POST -H "X-Service-Token: troque-este-token" http://localhost:8000/api/purchase-orders/suggest
   ```

   Aprove um pedido: em segundos ele aparece como **enviado (simulado)**, com a mensagem que iria ao fornecedor.

Para personalizar (nome da loja, fuso, token, WhatsApp), copie `.env.example` para `.env` antes de subir.

### Ligar o WhatsApp de verdade

Siga [docs/whatsapp-setup.md](docs/whatsapp-setup.md) (conta Meta, número de teste, token) e [docs/templates-meta.md](docs/templates-meta.md) (os dois templates a aprovar). Depois, `WHATSAPP_DRY_RUN=false` no `.env` e `docker compose up -d`.

## Desenvolvimento

```bash
uv sync
uv run pytest                 # unitários + integração (Postgres real via testcontainers; precisa de Docker)
uv run pytest -m e2e          # ponta a ponta contra o compose em execução
uv run ruff check . && uv run mypy src
```

API JSON documentada em http://localhost:8000/docs. Todas as rotas `/api/*` exigem o header `X-Service-Token`.

Design e plano de implementação: [docs/superpowers/specs](docs/superpowers/specs) e [docs/superpowers/plans](docs/superpowers/plans).

## Limitações do MVP

- **Sem login nas telas.** Não exponha a porta 8000 à internet sem colocar autenticação (ou um proxy com autenticação) na frente.
- Um fornecedor por produto, uma loja.
- Previsão por média móvel simples (sem sazonalidade).

## Próximos passos

Login, importação de planilha (CSV), webhook de PDV/e-commerce, previsão com sazonalidade, múltiplos fornecedores por produto, resposta "SIM" pelo WhatsApp e UI em React consumindo a mesma API.
