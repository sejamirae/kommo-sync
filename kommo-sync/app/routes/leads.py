# app/routes/leads.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import Lead
from app.services.kommo import update_lead_status, create_lead
from app.services.sync import sync_leads, upsert_lead_from_raw

router = APIRouter(prefix="/leads", tags=["Leads"])


class MoveLeadRequest(BaseModel):
    lead_id: int
    status_id: int


class CreateLeadRequest(BaseModel):
    name: str
    status_id: int
    pipeline_id: int
    price: int = 0


@router.get("/", summary="Lista leads do banco local")
async def list_leads(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).order_by(Lead.updated_at_kommo.desc()))
    leads = result.scalars().all()
    return [
        {
            "id":          l.id,
            "name":        l.name,
            "status_id":   l.status_id,
            "pipeline_id": l.pipeline_id,
            "price":       l.price,
            "updated_at":  l.updated_at_kommo,
        }
        for l in leads
    ]


@router.post("/move", summary="Move lead para outra etapa (SQL → Kommo)")
async def move_lead(body: MoveLeadRequest, db: AsyncSession = Depends(get_db)):
    """
    Atualiza a etapa na Kommo e reflete no banco local.
    Este é o endpoint SQL → Kommo principal.
    """
    try:
        response = await update_lead_status(body.lead_id, body.status_id, db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na API Kommo: {e}")

    # Atualiza banco local
    result = await db.execute(select(Lead).where(Lead.id == body.lead_id))
    lead = result.scalar_one_or_none()
    if lead:
        lead.status_id = body.status_id
        await db.commit()

    return {"success": True, "kommo_response": response}


@router.post("/create", summary="Cria lead na Kommo e salva no banco")
async def create_new_lead(body: CreateLeadRequest, db: AsyncSession = Depends(get_db)):
    try:
        response = await create_lead(
            name=body.name,
            status_id=body.status_id,
            pipeline_id=body.pipeline_id,
            price=body.price,
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na API Kommo: {e}")

    # Persiste o lead retornado
    leads_raw = response.get("_embedded", {}).get("leads", [])
    if leads_raw:
        await upsert_lead_from_raw(leads_raw[0], db)
        await db.commit()

    return {"success": True, "lead": leads_raw[0] if leads_raw else {}}


@router.post("/sync", summary="Sincronização manual: Kommo → banco")
async def manual_sync(db: AsyncSession = Depends(get_db)):
    total = await sync_leads(db)
    return {"synced": total}
