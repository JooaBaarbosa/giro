# Templates do WhatsApp (Meta)

Mensagens que **o negócio inicia** (fora da janela de 24 h de conversa) precisam de um template aprovado pela Meta. O Giro usa dois. Crie-os em **WhatsApp Manager → Modelos de mensagem → Criar modelo**.

| Campo | `pedido_compra` | `alerta_estoque_baixo` |
|---|---|---|
| Categoria | Utilidade (Utility) | Utilidade (Utility) |
| Idioma | Português (BR), `pt_BR` | Português (BR), `pt_BR` |
| Destinatário | fornecedor | dono da loja (`OWNER_WHATSAPP`) |

O nome e o idioma precisam ser **exatamente** esses: os workflows do n8n os referenciam.

## `pedido_compra`

Corpo:

```
Olá {{1}}, aqui é da {{2}}. Gostaríamos de fazer o seguinte pedido: {{3}}. Por favor, confirme o recebimento. Obrigado!
```

Exemplos para a submissão:

| Variável | Exemplo |
|---|---|
| `{{1}}` fornecedor | Distribuidora Grão de Ouro |
| `{{2}}` loja | Mercearia Demo |
| `{{3}}` itens | Café torrado 500g: 49 un · Óleo de soja 900ml: 62 un |

## `alerta_estoque_baixo`

Corpo:

```
⚠️ {{1}} itens abaixo do mínimo: {{2}}. Pedido sugerido para {{3}}: {{4}}
```

| Variável | Exemplo |
|---|---|
| `{{1}}` quantidade de itens | 2 |
| `{{2}}` nomes | Café torrado 500g, Óleo de soja 900ml |
| `{{3}}` fornecedor | Distribuidora Grão de Ouro |
| `{{4}}` link do pedido | http://localhost:8000/orders/7 |

## Regras que o Giro já respeita

- **Parâmetros sem quebra de linha, tab ou 4+ espaços seguidos.** A Meta rejeita esses parâmetros, então a API normaliza todos e a lista de itens vai numa linha só, separada por ` · `.
- A prévia mostrada no dashboard (`message_preview`) é gerada pela API com o mesmo texto do template, e é ela que fica registrada no pedido após o envio.
- O link do alerta usa `APP_BASE_URL`. Em produção, aponte para um endereço acessível pelo celular do dono.
