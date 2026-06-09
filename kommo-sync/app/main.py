# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import engine, Base, AsyncSessionLocal
from fastapi.middleware.cors import CORSMiddleware
from app.routes import oauth, webhook, leads, contacts, logs, pipelines, expansion
from app.services.sync import sync_leads, sync_contacts


scheduler = AsyncIOScheduler()


async def scheduled_sync():
    """Limpa do banco leads que não existem mais na Kommo (só APAGA, nunca adiciona)."""
    import httpx as _httpx
    from sqlalchemy import select as _select, text as _text
    from app.database import AsyncSessionLocal
    from app.models.db import ExpansionField
    from app.services.kommo import get_valid_token
    from app.config import settings

    BASE = f"https://{settings.KOMMO_DOMAIN}/api/v4"
    PIPELINE = 13865228

    try:
        async with AsyncSessionLocal() as db:
            access_token = await get_valid_token(db)
            H = {"Authorization": f"Bearer {access_token}"}

            # IDs que existem na Kommo
            kommo_ids = set()
            async with _httpx.AsyncClient(timeout=60) as client:
                page = 1
                while True:
                    r = await client.get(f"{BASE}/leads", headers=H,
                                        params={"filter[pipeline_id]": PIPELINE, "page": page, "limit": 250})
                    if r.status_code != 200:
                        break
                    data = r.json()
                    leads = data.get("_embedded", {}).get("leads", [])
                    if not leads:
                        break
                    for l in leads:
                        kommo_ids.add(l["id"])
                    if not data.get("_links", {}).get("next"):
                        break
                    page += 1

            # Se não retornou nada da Kommo, NÃO apaga nada (segurança)
            if not kommo_ids:
                return

            # IDs no banco
            result = await db.execute(_select(ExpansionField))
            rows = result.scalars().all()
            orphans = [ef.lead_id for ef in rows if ef.lead_id not in kommo_ids]

            for lid in orphans:
                await db.execute(_text("DELETE FROM expansion_fields WHERE lead_id = :id"), {"id": lid})
                await db.execute(_text("DELETE FROM expansion_notes WHERE lead_id = :id"), {"id": lid})
                await db.execute(_text("DELETE FROM leads WHERE id = :id"), {"id": lid})
            if orphans:
                await db.commit()
    except Exception as e:
        print(f"[scheduled_sync cleanup] erro: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cria tabelas (em prod use Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Inicia scheduler de sincronização
    scheduler.add_job(scheduled_sync, IntervalTrigger(hours=6), id="cleanup_job")
    scheduler.start()

    yield

    scheduler.shutdown()


app = FastAPI(
    title="Kommo Sync API",
    description="Bridge entre Kommo CRM e PostgreSQL",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — permite que o GitHub Pages consuma a API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Em prod, substitua pelo domínio do GitHub Pages
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(oauth.router)
app.include_router(webhook.router)
app.include_router(leads.router)
app.include_router(contacts.router)
app.include_router(logs.router)
app.include_router(pipelines.router)
app.include_router(expansion.router)


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "service": "kommo-sync"}


@app.get("/health", tags=["Health"])
@app.head("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}
