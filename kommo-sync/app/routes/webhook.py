# app/routes/webhook.py
import re
import json
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.services.sync import process_webhook_payload

router = APIRouter(prefix="/webhook", tags=["Webhook"])
settings = get_settings()


async def _process_in_background(payload: dict):
    """Processa o webhook em background, com sessão própria."""
    from app.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            await process_webhook_payload(payload, db)
    except Exception as e:
        print(f"[webhook background] erro: {e}")


@router.post("/kommo", summary="Recebe eventos da Kommo via webhook")
async def kommo_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    # Responde 200 IMEDIATAMENTE para a Kommo não desativar o webhook.
    # O processamento real acontece em background.
    try:
        if settings.WEBHOOK_SECRET:
            secret = request.headers.get("X-Kommo-Secret", "")
            if secret != settings.WEBHOOK_SECRET:
                # Mesmo com secret inválido, retorna 200 para não desativar
                return {"status": "ignored"}

        form = await request.form()
        payload: dict = {}
        for key, value in form.multi_items():
            _set_nested(payload, key, value)

        background_tasks.add_task(_process_in_background, payload)
    except Exception as e:
        print(f"[webhook] erro ao receber: {e}")

    # SEMPRE retorna 200 rápido
    return {"status": "ok"}


def _set_nested(d: dict, key: str, value):
    """
    Converte 'leads[status][0][id]' → {'leads': {'status': {'0': {'id': value}}}}
    Lida corretamente com índices numéricos e valores já existentes.
    """
    parts = [p for p in re.split(r"[\[\]]+", key) if p]
    ref = d
    for part in parts[:-1]:
        existing = ref.get(part)
        if existing is None:
            ref[part] = {}
            ref = ref[part]
        elif isinstance(existing, dict):
            ref = existing
        else:
            # Já existe um valor escalar — transforma em dict
            ref[part] = {"_val": existing}
            ref = ref[part]
    last = parts[-1]
    if last not in ref or not isinstance(ref[last], dict):
        ref[last] = value
