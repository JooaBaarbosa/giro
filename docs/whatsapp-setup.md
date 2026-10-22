# Ligando o WhatsApp de verdade (Meta Cloud API)

Enquanto `WHATSAPP_DRY_RUN=true` (padrão), nada sai para a Meta: o pedido é marcado como **enviado (simulado)** e o dashboard mostra a mensagem que seria enviada. Para enviar de verdade:

## 1. Conta e app

1. Acesse https://developers.facebook.com e entre com uma conta do Facebook.
2. **Meus apps → Criar app →** tipo **Empresa (Business)**.
3. No painel do app, adicione o produto **WhatsApp** e vincule (ou crie) uma conta do **Meta Business**.

## 2. Número de teste e destinatários

1. Em **WhatsApp → Configuração da API (API Setup)** a Meta oferece um **número de teste** gratuito.
2. Anote o **Phone number ID**. Ele vai em `WHATSAPP_PHONE_NUMBER_ID`.
3. Em **Para (To)**, adicione até **5 números** que poderão receber mensagens no modo de teste: o seu (dono) e o do fornecedor de teste. Cada número confirma por um código enviado no WhatsApp.

## 3. Token de acesso

- **Para testar:** o painel gera um *token temporário* (vale cerca de 24 h).
- **Para uso contínuo:** em **Configurações do negócio → Usuários do sistema**, crie um usuário do sistema, atribua o app com a permissão `whatsapp_business_messaging` e gere um **token permanente**.

O token vai em `WHATSAPP_ACCESS_TOKEN`. Ele é um segredo: fica só no `.env`, que está no `.gitignore`.

## 4. Templates

Crie e aguarde a aprovação dos dois templates descritos em [templates-meta.md](templates-meta.md). Sem template aprovado, a Meta recusa o envio e o pedido aparece como **falhou**, com o erro da Meta e o botão **Reenviar**.

## 5. Configurar o Giro

No `.env`:

```env
OWNER_WHATSAPP=+5511987654321        # seu número (recebe os alertas)
WHATSAPP_DRY_RUN=false
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_ACCESS_TOKEN=EAAG...
WHATSAPP_GRAPH_VERSION=v23.0
```

Cadastre no dashboard o fornecedor com o mesmo número que você liberou no passo 2 e rode:

```bash
docker compose up -d
```

## Conferindo

- Aprove um pedido: o status vai para **enviado**, sem o selo "simulado", e o fornecedor recebe a mensagem.
- Se der erro, o card do pedido mostra a mensagem da Meta. Os erros mais comuns:
  - `(#131030) Recipient phone number not in allowed list`: o número não foi adicionado no passo 2.
  - `(#132001) Template name does not exist in the translation`: o template não foi aprovado ou está com outro nome/idioma.
  - `Invalid OAuth access token`: o token expirou (o temporário dura cerca de 24 h).
- O histórico de execuções fica no n8n (http://localhost:5678 → Executions).
