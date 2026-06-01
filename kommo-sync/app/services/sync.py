# app/services/sync.py
"""
Sincroniza dados da Kommo para o PostgreSQL local.
Chamado manualmente, via scheduler ou via webhook.
"""
import json
from datetime import datetime, timezone

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
    db.add(SyncLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=json.dumps(payload, ensure_ascii=False),
        status=status,
        message=message,
    ))
    await db.flush()


# ─────────────────────────────────────────────
# Leads
# ─────────────────────────────────────────────

async def sync_leads(db: AsyncSession) -> int:
    """Baixa todos os leads da Kommo e persiste/atualiza no banco. Retorna contagem."""
    page, total = 1, 0
    while True:
        leads = await kommo_svc.get_leads(db, page=page)
        if not leads:
            break
        for raw in leads:
            await upsert_lead_from_raw(raw, db)
            total += 1
        page += 1
        if len(leads) < 250:
            break
    await db.commit()
    return total


async def upsert_lead_from_raw(raw: dict, db: AsyncSession) -> Lead:
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
    page, total = 1, 0
    while True:
        contacts = await kommo_svc.get_contacts(db, page=page)
        if not contacts:
            break
        for raw in contacts:
            await upsert_contact_from_raw(raw, db)
            total += 1
        page += 1
        if len(contacts) < 250:
            break
    await db.commit()
    return total


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

async def process_webhook_payload(payload: dict, db: AsyncSession):
    """
    Processa payload recebido via webhook da Kommo.
    Suporta eventos de lead (status, add, update) e contato.
    """
    # Leads
    for event in ("add", "update", "status", "delete"):
        for raw in payload.get("leads", {}).get(event, []):
            lead_id = int(raw.get("id", 0))
            if event == "delete":
                result = await db.execute(select(Lead).where(Lead.id == lead_id))
                lead = result.scalar_one_or_none()
                if lead:
                    await db.delete(lead)
                await _log(db, f"webhook:lead_{event}", "lead", lead_id, raw)
            else:
                # Para webhook de status, o payload é mais enxuto
                # Faz um upsert com o que vier
                result = await db.execute(select(Lead).where(Lead.id == lead_id))
                lead = result.scalar_one_or_none()
                if not lead:
                    lead = Lead(id=lead_id)
                    db.add(lead)
                if "name" in raw:
                    lead.name = raw["name"]
                if "status_id" in raw:
                    lead.status_id = int(raw["status_id"])
                if "pipeline_id" in raw:
                    lead.pipeline_id = int(raw["pipeline_id"])
                if "price" in raw:
                    lead.price = int(raw["price"])
                await _log(db, f"webhook:lead_{event}", "lead", lead_id, raw)

    # Contatos
    for event in ("add", "update", "delete"):
        for raw in payload.get("contacts", {}).get(event, []):
            contact_id = int(raw.get("id", 0))
            if event == "delete":
                result = await db.execute(select(Contact).where(Contact.id == contact_id))
                contact = result.scalar_one_or_none()
                if contact:
                    await db.delete(contact)
            else:
                await upsert_contact_from_raw(raw, db)
            await _log(db, f"webhook:contact_{event}", "contact", contact_id, raw)

    await db.commit()
