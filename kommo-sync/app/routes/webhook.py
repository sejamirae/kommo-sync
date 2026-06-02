# app/routes/webhook.py
import re
import json
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.services.sync import process_webhook_payload

router = APIRouter(prefix="/webhook", tags=["Webhook"])
settings = get_settings()


@router.post("/kommo", summary="Recebe eventos da Kommo via webhook")
async def kommo_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if settings.WEBHOOK_SECRET:
        secret = request.headers.get("X-Kommo-Secret", "")
        if secret != settings.WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # Kommo envia form-encoded: leads[status][0][id]=123&leads[status][0][pipeline_id]=456
    form = await request.form()
    payload: dict = {}
    for key, value in form.multi_items():
        _set_nested(payload, key, value)

    await process_webhook_payload(payload, db)
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
