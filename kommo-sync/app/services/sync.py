# app/services/sync.py
"""
Sincroniza dados da Kommo para o PostgreSQL local.
Chamado manualmente, via scheduler ou via webhook.
"""
import json
from datetime import datetime, timezone
import httpx

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.db import Lead, Contact, ContactPhone, ContactEmail, SyncLog
from app.services import kommo as kommo_svc


def _parse_dt(ts: int | None) -> datetime | None:
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


async def _log(db: AsyncSession, event_type: str, entity_type: str,
               entity_id: int, payload: dict, status: str = "ok", message: str = ""):
    # Skip logging sync events to save disk space (only log errors and webhooks)
    if event_type.startswith("sync:") and status == "ok":
        return
    db.add(SyncLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload="",  # Don't store payload to save space
        status=status,
        message=message,
    ))
    await db.flush()


# ─────────────────────────────────────────────
# Leads
# ─────────────────────────────────────────────

async def sync_leads(db: AsyncSession) -> int:
    """Sync desabilitado — use /leads/sync-pipeline?pipeline_id=13865228 no lugar."""
    # O sync geral baixava 12.539 leads de todos os pipelines enchendo o banco.
    # Use o endpoint sync-pipeline que filtra só o pipeline Expansão.
    return 0


async def upsert_lead_from_raw(raw: dict, db: AsyncSession) -> Lead:
    from sqlalchemy import text as _text
    # Use ON CONFLICT to avoid duplicate key errors when webhook and batch run simultaneously
    await db.execute(_text("""
        INSERT INTO leads (id, name, status_id, pipeline_id, price)
        VALUES (:id, :name, :status_id, :pipeline_id, :price)
        ON CONFLICT (id) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, leads.name),
            status_id = COALESCE(EXCLUDED.status_id, leads.status_id),
            pipeline_id = COALESCE(EXCLUDED.pipeline_id, leads.pipeline_id)
    """), {
        "id": raw["id"],
        "name": raw.get("name"),
        "status_id": raw.get("status_id"),
        "pipeline_id": raw.get("pipeline_id"),
        "price": raw.get("price", 0),
    })
    result = await db.execute(select(Lead).where(Lead.id == raw["id"]))
    lead = result.scalar_one_or_none()
    if not lead:
        lead = Lead(id=raw["id"])
        db.add(lead)

    lead.name            = raw.get("name")
    lead.status_id       = raw.get("status_id")
    lead.pipeline_id     = raw.get("pipeline_id")
    lead.responsible_id  = raw.get("responsible_user_id")
    lead.price           = raw.get("price", 0)
    lead.created_at_kommo = _parse_dt(raw.get("created_at"))
    lead.updated_at_kommo = _parse_dt(raw.get("updated_at"))

    await _log(db, "sync:lead_upsert", "lead", raw["id"], raw)
    return lead


# ─────────────────────────────────────────────
# Contatos
# ─────────────────────────────────────────────

async def sync_contacts(db: AsyncSession) -> int:
    """Sync de contatos desabilitado — enche o banco com contatos de outros pipelines."""
    return 0


async def upsert_contact_from_raw(raw: dict, db: AsyncSession) -> Contact:
    result = await db.execute(select(Contact).where(Contact.id == raw["id"]))
    contact = result.scalar_one_or_none()

    if not contact:
        contact = Contact(id=raw["id"])
        db.add(contact)

    contact.name            = raw.get("name")
    contact.first_name      = raw.get("first_name", "")
    contact.last_name       = raw.get("last_name", "")
    contact.responsible_id  = raw.get("responsible_user_id")
    contact.created_at_kommo = _parse_dt(raw.get("created_at"))
    contact.updated_at_kommo = _parse_dt(raw.get("updated_at"))

    # Custom fields: telefones e emails
    custom = raw.get("custom_fields_values") or []
    phones, emails = [], []
    for field in custom:
        code = field.get("field_code", "")
        for v in field.get("values", []):
            if code == "PHONE":
                phones.append({"value": v.get("value", ""), "enum_code": v.get("enum_code", "")})
            elif code == "EMAIL":
                emails.append({"value": v.get("value", ""), "enum_code": v.get("enum_code", "")})

    # Recria phones/emails para simplificar (delete + insert)
    await db.execute(
        ContactPhone.__table__.delete().where(ContactPhone.contact_id == contact.id)
    )
    await db.execute(
        ContactEmail.__table__.delete().where(ContactEmail.contact_id == contact.id)
    )
    for p in phones:
        db.add(ContactPhone(contact_id=raw["id"], **p))
    for e in emails:
        db.add(ContactEmail(contact_id=raw["id"], **e))

    await _log(db, "sync:contact_upsert", "contact", raw["id"], raw)
    return contact


# ─────────────────────────────────────────────
# Webhook → banco
# ─────────────────────────────────────────────

async def _fetch_lead_from_kommo(lead_id: int, access_token: str) -> dict | None:
    """Busca dados completos de um lead direto na Kommo API."""
    try:
        from app.config import get_settings
        settings = get_settings()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://{settings.KOMMO_DOMAIN}/api/v4/leads/{lead_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None


def _extract_items(data) -> list:
    """
    Normaliza estrutura de webhook da Kommo.
    Pode vir como lista ou como dict com chaves numéricas {'0': {...}, '1': {...}}.
    """
    if not data:
        return []
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    if isinstance(data, dict):
        # Chaves numéricas {'0': {id:...}, '1': {id:...}}
        items = []
        for k in sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            v = data[k]
            if isinstance(v, dict):
                items.append(v)
        return items
    return []


async def process_webhook_payload(payload: dict, db: AsyncSession):
    """
    Processa payload recebido via webhook da Kommo.
    Suporta eventos de lead (status, add, update) e contato.
    Quando o lead não existe no banco, busca dados completos na Kommo.
    """
    from app.services.kommo import get_valid_token
    access_token = await get_valid_token(db)

    EXPANSAO_PIPELINE_ID = 13865228

    # Leads
    for event in ("add", "update", "status", "delete"):
        for raw in _extract_items(payload.get("leads", {}).get(event)):
            lead_id = int(raw.get("id", 0))
            if not lead_id:
                continue

            if event == "delete":
                # Só deleta se for do pipeline Expansão
                result = await db.execute(select(Lead).where(Lead.id == lead_id))
                lead = result.scalar_one_or_none()
                if lead and lead.pipeline_id == EXPANSAO_PIPELINE_ID:
                    # Deleta campos e notas associados
                    from sqlalchemy import text as _text
                    await db.execute(_text("DELETE FROM expansion_fields WHERE lead_id = :id"), {"id": lead_id})
                    await db.execute(_text("DELETE FROM expansion_notes WHERE lead_id = :id"), {"id": lead_id})
                    await db.delete(lead)
            else:
                # SEMPRE verifica se o lead é do pipeline Expansão
                # Primeiro checa se já existe no banco
                result = await db.execute(select(Lead).where(Lead.id == lead_id))
                lead = result.scalar_one_or_none()

                if lead:
                    # Lead já existe — só atualiza se for do pipeline Expansão
                    if lead.pipeline_id != EXPANSAO_PIPELINE_ID:
                        continue
                    pipeline_id_raw = int(raw.get("pipeline_id", 0))
                    if pipeline_id_raw and pipeline_id_raw != EXPANSAO_PIPELINE_ID:
                        # Foi movido para outro pipeline — remove do banco
                        await db.delete(lead)
                        continue
                    if "status_id" in raw:
                        lead.status_id = int(raw["status_id"])
                    if "name" in raw:
                        lead.name = raw["name"]
                else:
                    # Lead novo — busca na Kommo para verificar pipeline
                    full_data = await _fetch_lead_from_kommo(lead_id, access_token)
                    if not full_data:
                        continue
                    if int(full_data.get("pipeline_id", 0)) != EXPANSAO_PIPELINE_ID:
                        continue  # Não é do pipeline Expansão, ignora
                    await upsert_lead_from_raw(full_data, db)

    # Contatos — não salvamos contatos de outros pipelines
    # Os contatos do pipeline Expansão são criados diretamente no import-batch

    await db.commit()
