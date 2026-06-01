# app/routes/expansion.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import datetime

from app.database import get_db
from app.models.db import ExpansionField, ExpansionNote
from app.services.kommo import get_valid_token
from app.config import get_settings

router = APIRouter(prefix="/expansion", tags=["Expansão"])
settings = get_settings()
BASE = f"https://{settings.KOMMO_DOMAIN}/api/v4"

PIPELINE_ID = 13865228

# Campos que queremos criar/manter na Kommo
CUSTOM_FIELDS = [
    {"name": "Nome Completo",       "type": "text"},
    {"name": "CRM",                 "type": "text"},
    {"name": "Telefone Médico",     "type": "text"},
    {"name": "Unidade",             "type": "text"},
    {"name": "Dia da Semana",       "type": "text"},
    {"name": "Frequência",          "type": "text"},
    {"name": "Horário",             "type": "text"},
    {"name": "Nº de Horas",        "type": "text"},
    {"name": "Data Envio",          "type": "date"},
    {"name": "Data Fechamento",     "type": "date"},
    {"name": "Previsão Início",     "type": "date"},
    {"name": "Unidade Pagamento",   "type": "text"},
    {"name": "Valor Mirae",         "type": "text"},
    {"name": "Valor Médico",        "type": "text"},
    {"name": "Onboarding",          "type": "text"},
    {"name": "Origem",              "type": "text"},
    {"name": "Gestor",              "type": "text"},
    {"name": "DoctorID",            "type": "text"},
    {"name": "Pendências",          "type": "text"},
    {"name": "Observações",         "type": "textarea"},
]


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


# ── Setup: cria custom fields no pipeline ────────────────────────────

@router.post("/setup-fields", summary="Cria custom fields do pipeline Expansão na Kommo (rodar 1x)")
async def setup_custom_fields(db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)

    # Busca campos já existentes
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE}/leads/custom_fields",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"filter[pipeline_id]": PIPELINE_ID},
        )
        existing = {f["name"]: f["id"] for f in resp.json().get("_embedded", {}).get("custom_fields", [])}

    created, skipped = [], []
    async with httpx.AsyncClient() as client:
        for field in CUSTOM_FIELDS:
            if field["name"] in existing:
                skipped.append(field["name"])
                continue
            payload = {"name": field["name"], "type": field["type"], "pipeline_id": PIPELINE_ID}
            r = await client.post(
                f"{BASE}/leads/custom_fields",
                headers={"Authorization": f"Bearer {access_token}"},
                json=[payload],
            )
            if r.status_code in (200, 201):
                created.append(field["name"])

    return {"created": created, "skipped": skipped}


@router.get("/field-ids", summary="Retorna IDs dos custom fields do pipeline Expansão")
async def get_field_ids(db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE}/leads/custom_fields",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"filter[pipeline_id]": PIPELINE_ID},
        )
        fields = resp.json().get("_embedded", {}).get("custom_fields", [])
    return {f["name"]: f["id"] for f in fields}


# ── Campos extras (banco local) ───────────────────────────────────────

@router.get("/fields", summary="Busca campos de todos os leads de uma vez")
async def get_all_fields(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExpansionField))
    rows = result.scalars().all()
    out = {}
    for row in rows:
        out[str(row.lead_id)] = {
            c.name: getattr(row, c.name)
            for c in ExpansionField.__table__.columns
            if c.name not in ('lead_id', 'updated_at')
        }
    return out


@router.get("/fields/{lead_id}", summary="Busca campos extras de um lead")
async def get_fields(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExpansionField).where(ExpansionField.lead_id == lead_id))
    row = result.scalar_one_or_none()
    if not row:
        return {}
    return {c.name: getattr(row, c.name) for c in ExpansionField.__table__.columns if c.name != 'lead_id'}


@router.post("/fields/{lead_id}", summary="Salva campos extras — banco + Kommo custom fields")
async def save_fields(lead_id: int, body: FieldsIn, db: AsyncSession = Depends(get_db)):
    # 1. Salva no banco local
    result = await db.execute(select(ExpansionField).where(ExpansionField.lead_id == lead_id))
    row = result.scalar_one_or_none()
    if not row:
        row = ExpansionField(lead_id=lead_id)
        db.add(row)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(row, field, val)
    await db.commit()

    # 2. Atualiza custom fields na Kommo
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        # Busca IDs dos campos
        resp = await client.get(
            f"{BASE}/leads/custom_fields",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"filter[pipeline_id]": PIPELINE_ID},
        )
        field_map = {f["name"]: f["id"] for f in resp.json().get("_embedded", {}).get("custom_fields", [])};

        def to_ts(date_str):
            if not date_str: return None
            try: return int(datetime.datetime.strptime(date_str, "%Y-%m-%d").timestamp())
            except: return None

        data = body.model_dump(exclude_none=True)
        name_to_field = {
            "Nome Completo": data.get("nome_completo"),
            "CRM": data.get("crm"),
            "Telefone Médico": data.get("telefone"),
            "Unidade": data.get("unidade"),
            "Dia da Semana": data.get("dia_semana"),
            "Frequência": data.get("frequencia"),
            "Horário": data.get("horario"),
            "Nº de Horas": data.get("horas"),
            "Data Envio": to_ts(data.get("data_envio")),
            "Data Fechamento": to_ts(data.get("data_fechamento")),
            "Previsão Início": to_ts(data.get("previsao_inicio")),
            "Unidade Pagamento": data.get("unidade_pagamento"),
            "Valor Mirae": data.get("valor_mirae"),
            "Valor Médico": data.get("valor_medico"),
            "Onboarding": data.get("onboarding"),
            "Origem": data.get("origem"),
            "Gestor": data.get("gestor"),
            "DoctorID": data.get("doctorid"),
            "Pendências": data.get("pendencias"),
            "Observações": data.get("observacoes"),
        }

        custom_fields_values = []
        for fname, fval in name_to_field.items():
            fid = field_map.get(fname)
            if fid and fval is not None and fval != "":
                custom_fields_values.append({"field_id": fid, "values": [{"value": fval}]})

        if custom_fields_values:
            await client.patch(
                f"{BASE}/leads",
                headers={"Authorization": f"Bearer {access_token}"},
                json=[{"id": lead_id, "custom_fields_values": custom_fields_values}],
            )

    return {"ok": True}


# ── Notas ─────────────────────────────────────────────────────────────

@router.get("/notes/{lead_id}", summary="Notas manuais do banco")
async def get_notes(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExpansionNote).where(ExpansionNote.lead_id == lead_id).order_by(ExpansionNote.created_at)
    )
    return [
        {"id": n.id, "type": n.type, "text": n.text, "author": n.author,
         "date": n.created_at.strftime("%d/%m/%Y %H:%M") if n.created_at else "", "source": "local"}
        for n in result.scalars().all()
    ]


@router.post("/notes/{lead_id}", summary="Adiciona nota")
async def add_note(lead_id: int, body: NoteIn, db: AsyncSession = Depends(get_db)):
    note = ExpansionNote(lead_id=lead_id, type=body.type, text=body.text, author=body.author)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return {"id": note.id, "type": note.type, "text": note.text, "author": note.author,
            "date": note.created_at.strftime("%d/%m/%Y %H:%M") if note.created_at else "", "source": "local"}


@router.get("/kommo-notes/{lead_id}", summary="Histórico de notas da Kommo")
async def get_kommo_notes(lead_id: int, db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE}/leads/{lead_id}/notes",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": 50, "order[id]": "asc"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()

    type_map = {1:"ligacao",2:"email",3:"nota",10:"whatsapp",25:"whatsapp",102:"email"}
    result = []
    for n in data.get("_embedded", {}).get("notes", []):
        note_type = n.get("note_type", 3)
        params = n.get("params", {})
        text = params.get("text") or params.get("description") or params.get("body") or n.get("text","") or f"[evento tipo {note_type}]"
        created = n.get("created_at", 0)
        dt = datetime.datetime.fromtimestamp(created).strftime("%d/%m/%Y %H:%M") if created else ""
        result.append({"id": n.get("id"), "type": type_map.get(note_type,"nota"),
                       "text": str(text)[:500], "author": str(n.get("created_by","Kommo")),
                       "date": dt, "source": "kommo"})
    return result


@router.get("/pipeline-stages", summary="Etapas do pipeline")
async def get_expansion_stages(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE}/leads/pipelines/{pipeline_id}",
                                headers={"Authorization": f"Bearer {access_token}"},
                                params={"with": "statuses"})
        resp.raise_for_status()
        data = resp.json()
    stages = data.get("_embedded", {}).get("statuses", [])
    return {"pipeline_id": pipeline_id, "pipeline_name": data.get("name"),
            "stages": [{"id": s["id"], "name": s["name"]} for s in stages]}
