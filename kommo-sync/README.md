# Kommo Sync API

Bridge entre **Kommo CRM** e **PostgreSQL**, hospedada no Render.

## Funcionalidades

| Direção | O que faz |
|---|---|
| Kommo → SQL | Webhook recebe eventos (mover etapa, criar/atualizar lead, criar/atualizar contato) e persiste no banco |
| SQL → Kommo | Endpoints REST para mover etapa, criar lead, upsert contato |
| Auto-sync | Scheduler roda a cada 30 min sincronizando tudo |

---

## Estrutura

```
kommo-sync/
├── app/
│   ├── main.py          # FastAPI + lifespan + scheduler
│   ├── config.py        # Settings (pydantic-settings)
│   ├── database.py      # SQLAlchemy async engine
│   ├── models/
│   │   └── db.py        # Lead, Contact, KommoToken, SyncLog
│   ├── services/
│   │   ├── kommo.py     # OAuth + chamadas API Kommo
│   │   └── sync.py      # Lógica de sincronização
│   └── routes/
│       ├── oauth.py     # /oauth/authorize + /oauth/callback
│       ├── webhook.py   # /webhook/kommo
│       ├── leads.py     # /leads/*
│       └── contacts.py  # /contacts/*
├── render.yaml
├── requirements.txt
└── .env.example
```

---

## Deploy no Render

### 1. Criar integração OAuth na Kommo

1. Acesse: https://www.kommo.com/developers/
2. Crie uma nova integração (tipo: **Widget** ou **External**)
3. Em **Redirect URI** coloque: `https://seu-app.onrender.com/oauth/callback`
4. Anote: `Client ID` e `Client Secret`

### 2. Subir no GitHub

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/SEU_USER/kommo-sync.git
git push -u origin main
```

### 3. Deploy no Render

1. Acesse https://render.com → **New → Blueprint**
2. Conecte o repositório GitHub
3. O `render.yaml` cria automaticamente o **Web Service** + **PostgreSQL**
4. Preencha as variáveis de ambiente (marcadas com `sync: false`):
   - `KOMMO_CLIENT_ID`
   - `KOMMO_CLIENT_SECRET`
   - `KOMMO_REDIRECT_URI` → `https://seu-app.onrender.com/oauth/callback`
   - `KOMMO_DOMAIN` → `suaconta.kommo.com` (sem https)
   - `WEBHOOK_SECRET` → string aleatória (opcional)

### 4. Autorizar OAuth

Acesse: `https://seu-app.onrender.com/oauth/authorize`

Clique em **Autorizar no Kommo** → você será redirecionado de volta e o token será salvo no banco.

### 5. Configurar Webhook na Kommo

1. Settings → Integrations → Webhooks
2. URL: `https://seu-app.onrender.com/webhook/kommo`
3. Eventos: **Lead status changed**, **Lead added**, **Lead updated**, **Contact added**, **Contact updated**

---

## Endpoints principais

| Método | Rota | Descrição |
|---|---|---|
| GET | `/oauth/authorize` | Inicia fluxo OAuth |
| GET | `/oauth/callback` | Callback OAuth (automático) |
| POST | `/webhook/kommo` | Recebe eventos da Kommo |
| GET | `/leads/` | Lista leads do banco |
| POST | `/leads/move` | Move lead de etapa |
| POST | `/leads/create` | Cria lead |
| POST | `/leads/sync` | Sync manual leads |
| GET | `/contacts/` | Lista contatos |
| GET | `/contacts/{id}` | Detalhe + telefones/emails |
| POST | `/contacts/upsert` | Cria ou atualiza contato |
| POST | `/contacts/sync` | Sync manual contatos |

Documentação interativa: `https://seu-app.onrender.com/docs`

---

## Mover etapa via código (SQL → Kommo)

```python
import httpx

# Move lead 12345 para etapa 67890
httpx.post("https://seu-app.onrender.com/leads/move", json={
    "lead_id": 12345,
    "status_id": 67890
})
```

---

## Observações

- O free tier do Render **hiberna** após 15 min de inatividade. Para webhooks em produção, considere o plano pago ($7/mês) ou mantenha o serviço ativo com um ping periódico (ex: UptimeRobot).
- O PostgreSQL free tier do Render expira após **90 dias** — exporte o dump antes.
- O scheduler de 30 min serve como fallback; o webhook é a forma primária de manter o banco atualizado em tempo real.
