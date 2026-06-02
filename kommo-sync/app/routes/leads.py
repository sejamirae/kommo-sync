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
async def list_leads(pipeline_id: int = None, db: AsyncSession = Depends(get_db)):
    query = select(Lead).order_by(Lead.updated_at_kommo.desc())
    if pipeline_id:
        query = query.where(Lead.pipeline_id == pipeline_id)
    result = await db.execute(query)
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


@router.delete("/{lead_id}", summary="Exclui lead da Kommo e do banco")
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    from app.config import get_settings
    import httpx
    settings = get_settings()
    access_token = await get_valid_token(db)

    # Deleta na Kommo
    import logging
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"https://{settings.KOMMO_DOMAIN}/api/v4/leads/{lead_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        logging.warning(f"Kommo DELETE lead {lead_id}: status={resp.status_code} body={resp.text[:300]}")

    # Deleta no banco local
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if lead:
        await db.delete(lead)
        await db.commit()

    return {"ok": True, "deleted": lead_id}


@router.post("/sync-pipeline", summary="Sync rápido de um pipeline específico")
async def sync_pipeline(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """Sincroniza apenas os leads de um pipeline — muito mais rápido que sync total."""
    from app.services.kommo import get_valid_token
    from app.config import get_settings
    from app.services.sync import upsert_lead_from_raw
    from sqlalchemy import delete
    from app.models.db import Lead
    import httpx

    settings = get_settings()
    access_token = await get_valid_token(db)
    headers = {"Authorization": f"Bearer {access_token}"}

    # Busca todos os leads do pipeline na Kommo
    all_leads = []
    page = 1
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"https://{settings.KOMMO_DOMAIN}/api/v4/leads",
                headers=headers,
                params={"filter[pipeline_id]": pipeline_id, "page": page, "limit": 250},
            )
            if resp.status_code == 204 or not resp.content:
                break
            data = resp.json()
            leads = data.get("_embedded", {}).get("leads", [])
            if not leads:
                break
            all_leads.extend(leads)
            if len(leads) < 250:
                break
            page += 1

    # Busca IDs atuais no banco para esse pipeline
    kommo_ids = {l["id"] for l in all_leads}

    # Remove do banco leads que não existem mais na Kommo
    result = await db.execute(
        select(Lead).where(Lead.pipeline_id == pipeline_id)
    )
    db_leads = result.scalars().all()
    for lead in db_leads:
        if lead.id not in kommo_ids:
            await db.delete(lead)

    # Upsert dos leads atuais
    for raw in all_leads:
        await upsert_lead_from_raw(raw, db)

    await db.commit()
    return {"synced": len(all_leads), "pipeline_id": pipeline_id}
