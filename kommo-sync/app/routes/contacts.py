# app/routes/contacts.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import Contact, ContactPhone, ContactEmail
from app.services.kommo import upsert_contact
from app.services.sync import sync_contacts

router = APIRouter(prefix="/contacts", tags=["Contatos"])


@router.get("/", summary="Lista contatos do banco local")
async def list_contacts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contact).order_by(Contact.name))
    contacts = result.scalars().all()
    return [
        {
            "id":         c.id,
            "name":       c.name,
            "first_name": c.first_name,
            "last_name":  c.last_name,
            "updated_at": c.updated_at_kommo,
        }
        for c in contacts
    ]


@router.get("/{contact_id}", summary="Detalhe do contato com phones e emails")
async def get_contact(contact_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado")

    phones = (await db.execute(
        select(ContactPhone).where(ContactPhone.contact_id == contact_id)
    )).scalars().all()
    emails = (await db.execute(
        select(ContactEmail).where(ContactEmail.contact_id == contact_id)
    )).scalars().all()

    return {
        "id":         contact.id,
        "name":       contact.name,
        "first_name": contact.first_name,
        "last_name":  contact.last_name,
        "phones":     [{"value": p.value, "type": p.enum_code} for p in phones],
        "emails":     [{"value": e.value, "type": e.enum_code} for e in emails],
        "updated_at": contact.updated_at_kommo,
    }


class UpsertContactRequest(BaseModel):
    id: int | None = None
    name: str
    first_name: str = ""
    last_name: str = ""


@router.post("/upsert", summary="Cria ou atualiza contato na Kommo e no banco")
async def upsert_contact_endpoint(body: UpsertContactRequest, db: AsyncSession = Depends(get_db)):
    try:
        response = await upsert_contact(body.model_dump(exclude_none=True), db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na API Kommo: {e}")
    return {"success": True, "response": response}


@router.post("/sync", summary="Sincronização manual de contatos: Kommo → banco")
async def manual_sync(db: AsyncSession = Depends(get_db)):
    total = await sync_contacts(db)
    return {"synced": total}
