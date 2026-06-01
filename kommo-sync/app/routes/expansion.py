# app/routes/expansion.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import ExpansionField, ExpansionNote

router = APIRouter(prefix="/expansion", tags=["Expansão"])


# ── Schemas ──────────────────────────────────────────────────────────

class FieldsIn(BaseModel):
    nome_completo: Optional[str] = None
    crm: Optional[str] = None
    telefone: Optional[str] = None
    cliente: Optional[str] = None
    especialidade: Optional[str] = None
    unidade: Optional[str] = None
    dia_semana: Optional[str] = None
    frequencia: Optional[str] = None
    horario: Optional[str] = None
    horas: Optional[str] = None
    data_envio: Optional[str] = None
    data_fechamento: Optional[str] = None
    previsao_inicio: Optional[str] = None
    unidade_pagamento: Optional[str] = None
    valor_mirae: Optional[str] = None
    valor_medico: Optional[str] = None
    onboarding: Optional[str] = None
    origem: Optional[str] = None
    gestor: Optional[str] = None
    doctorid: Optional[str] = None
    pendencias: Optional[str] = None
    observacoes: Optional[str] = None


class NoteIn(BaseModel):
    type: str
    text: str
    author: Optional[str] = "Equipe"


# ── Campos extras ─────────────────────────────────────────────────────

@router.get("/fields/{lead_id}", summary="Busca campos extras de um lead")
async def get_fields(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExpansionField).where(ExpansionField.lead_id == lead_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return {}
    return {c.name: getattr(row, c.name) for c in ExpansionField.__table__.columns if c.name != 'lead_id'}


@router.post("/fields/{lead_id}", summary="Salva campos extras de um lead")
async def save_fields(lead_id: int, body: FieldsIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExpansionField).where(ExpansionField.lead_id == lead_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        row = ExpansionField(lead_id=lead_id)
        db.add(row)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(row, field, val)
    await db.commit()
    return {"ok": True}


@router.get("/fields", summary="Busca campos de todos os leads de uma vez")
async def get_all_fields(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExpansionField))
    rows = result.scalars().all()
    out = {}
    for row in rows:
        out[row.lead_id] = {
            c.name: getattr(row, c.name)
            for c in ExpansionField.__table__.columns
            if c.name not in ('lead_id', 'updated_at')
        }
    return out


# ── Notas / histórico ─────────────────────────────────────────────────

@router.get("/notes/{lead_id}", summary="Busca histórico de um lead")
async def get_notes(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExpansionNote)
        .where(ExpansionNote.lead_id == lead_id)
        .order_by(ExpansionNote.created_at)
    )
    notes = result.scalars().all()
    return [
        {
            "id": n.id,
            "type": n.type,
            "text": n.text,
            "author": n.author,
            "date": n.created_at.strftime("%d/%m/%Y %H:%M") if n.created_at else "",
        }
        for n in notes
    ]


@router.post("/notes/{lead_id}", summary="Adiciona nota ao histórico de um lead")
async def add_note(lead_id: int, body: NoteIn, db: AsyncSession = Depends(get_db)):
    note = ExpansionNote(
        lead_id=lead_id,
        type=body.type,
        text=body.text,
        author=body.author,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return {
        "id": note.id,
        "type": note.type,
        "text": note.text,
        "author": note.author,
        "date": note.created_at.strftime("%d/%m/%Y %H:%M") if note.created_at else "",
    }


@router.get("/pipeline-stages", summary="Busca etapas de um pipeline")
async def get_expansion_stages(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    import httpx
    from app.services.kommo import get_valid_token
    from app.config import get_settings
    settings = get_settings()
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://{settings.KOMMO_DOMAIN}/api/v4/leads/pipelines/{pipeline_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"with": "statuses"},
        )
        resp.raise_for_status()
        data = resp.json()
    stages = data.get("_embedded", {}).get("statuses", [])
    return {
        "pipeline_id": pipeline_id,
        "pipeline_name": data.get("name"),
        "stages": [{"id": s["id"], "name": s["name"]} for s in stages],
    }
