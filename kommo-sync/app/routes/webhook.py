# app/routes/webhook.py
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
    # Validação de secret (opcional mas recomendado)
    if settings.WEBHOOK_SECRET:
        secret = request.headers.get("X-Kommo-Secret", "")
        if secret != settings.WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # Kommo envia form-encoded
    form = await request.form()
    # Converte MultiDict → dict aninhado
    payload: dict = {}
    for key, value in form.multi_items():
        _set_nested(payload, key, value)

    await process_webhook_payload(payload, db)
    return {"status": "ok"}


def _set_nested(d: dict, key: str, value):
    """Converte chaves 'leads[status][0][id]' em dict aninhado."""
    import re
    parts = re.split(r"[\[\]]+", key)
    parts = [p for p in parts if p]
    ref = d
    for part in parts[:-1]:
        if part not in ref:
            ref[part] = {}
        ref = ref[part]
    last = parts[-1]
    if last in ref and isinstance(ref[last], dict):
        pass  # já existe como dict
    else:
        ref[last] = value
