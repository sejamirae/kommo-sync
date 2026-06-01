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
    """Sincronização automática a cada 30 minutos."""
    async with AsyncSessionLocal() as db:
        leads_count = await sync_leads(db)
        contacts_count = await sync_contacts(db)
        print(f"[Scheduler] Sync: {leads_count} leads, {contacts_count} contatos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cria tabelas (em prod use Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Inicia scheduler de sincronização
    scheduler.add_job(scheduled_sync, IntervalTrigger(minutes=30), id="sync_job")
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
async def health():
    return {"status": "healthy"}
