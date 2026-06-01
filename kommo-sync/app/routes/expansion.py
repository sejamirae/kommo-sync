# app/routes/expansion.py
"""
Importação da planilha de Expansão de Agendas → leads no pipeline Kommo.
Cada linha da planilha vira um lead. Se o lead já existe (mesmo nome),
apenas move a etapa conforme o status atual.
"""
import io
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.database import get_db
from app.services.kommo import get_valid_token
from app.models.db import Lead, SyncLog
from app.config import get_settings

router = APIRouter(prefix="/expansion", tags=["Expansão de Agendas"])
settings = get_settings()
BASE_URL = f"https://{settings.KOMMO_DOMAIN}/api/v4"


# ─── Mapeamento Status planilha → nome da etapa Kommo ───────────────
STATUS_MAP = {
    "Disponível":      "ENVIADAS",
    "Em Negociação":   "EM NEGOCIAÇÃO",
    "Fechado":         "FECHADAS",
    "Fechada":         "FECHADAS",
    "Negado":          "NEGADAS",
    "Negada":          "NEGADAS",
    "Perdido":         "NEGADAS",
}


class PipelineConfig(BaseModel):
    pipeline_id: int
    stage_map: dict  # {"ENVIADAS": 111, "EM NEGOCIAÇÃO": 222, "FECHADAS": 333, "NEGADAS": 444}


@router.get("/pipeline-stages", summary="Busca etapas do pipeline Expansão para configurar mapeamento")
async def get_expansion_stages(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna as etapas de um pipeline específico com IDs prontos para mapear."""
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/leads/pipelines/{pipeline_id}",
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
        "stage_map_template": {s["name"].upper(): s["id"] for s in stages},
    }


@router.post("/import", summary="Importa planilha Excel e cria/atualiza leads no pipeline")
async def import_spreadsheet(
    pipeline_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Recebe o arquivo .xlsx, lê cada linha e cria leads no pipeline informado.
    O status da planilha determina a etapa inicial do lead.
    """
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl não instalado no servidor")

    # Lê o arquivo enviado
    content = await file.read()
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="Planilha vazia")

    # Header na primeira linha
    headers = [str(h).strip() if h else "" for h in rows[0]]
    data_rows = rows[1:]

    # Busca etapas do pipeline para mapear nomes → IDs
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/leads/pipelines/{pipeline_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"with": "statuses"},
        )
        resp.raise_for_status()
        pipeline_data = resp.json()

    stages = pipeline_data.get("_embedded", {}).get("statuses", [])
    # Mapa: nome_upper → status_id
    stage_name_to_id = {s["name"].upper(): s["id"] for s in stages}

    created, updated, skipped = 0, 0, 0
    errors = []

    # Processa em lotes de 50 (limite da API Kommo)
    batch = []

    for i, row in enumerate(data_rows, start=2):
        row_dict = dict(zip(headers, row))

        status_planilha = str(row_dict.get("Status", "") or "").strip()
        cliente      = str(row_dict.get("Cliente", "") or "").strip()
        especialidade = str(row_dict.get("Especialidade", "") or "").strip()
        unidade      = str(row_dict.get("Unidade", "") or "").strip()
        dia          = str(row_dict.get("Dia da Semana", "") or "").strip()
        frequencia   = str(row_dict.get("Frequencia", "") or "").strip()
        horario      = str(row_dict.get("Horário", "") or "").strip()
        observacao   = str(row_dict.get("Observação", "") or "").strip()

        if not cliente or not especialidade:
            skipped += 1
            continue

        # Nome do lead: "Cliente - Especialidade - Unidade"
        lead_name = f"{cliente} · {especialidade} · {unidade}" if unidade else f"{cliente} · {especialidade}"

        # Resolve etapa
        etapa_kommo = STATUS_MAP.get(status_planilha, "ENVIADAS")
        status_id = stage_name_to_id.get(etapa_kommo)
        if not status_id:
            # Tenta primeira etapa disponível
            status_id = stages[0]["id"] if stages else None

        if not status_id:
            errors.append(f"Linha {i}: etapa '{etapa_kommo}' não encontrada no pipeline")
            continue

        # Observação como nota do lead
        note = " | ".join(filter(None, [dia, frequencia, horario, observacao]))

        batch.append({
            "name":        lead_name,
            "pipeline_id": pipeline_id,
            "status_id":   status_id,
            "_metadata":   {"note": note, "row": i},
        })

        # Envia em lotes de 50
        if len(batch) >= 50:
            c, u, e = await _flush_batch(batch, access_token, db)
            created += c; updated += u; errors += e
            batch = []

    # Flush do restante
    if batch:
        c, u, e = await _flush_batch(batch, access_token, db)
        created += c; updated += u; errors += e

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors":  errors[:20],  # primeiros 20 erros
        "total_rows": len(data_rows),
    }


async def _flush_batch(batch: list, access_token: str, db: AsyncSession):
    """Envia um lote de leads para a Kommo via POST."""
    payload = [
        {
            "name":        item["name"],
            "pipeline_id": item["pipeline_id"],
            "status_id":   item["status_id"],
        }
        for item in batch
    ]

    created, updated, errors = 0, 0, []

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"https://{settings.KOMMO_DOMAIN}/api/v4/leads",
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )

    if resp.status_code in (200, 201):
        result = resp.json()
        new_leads = result.get("_embedded", {}).get("leads", [])
        created += len(new_leads)

        # Salva no banco local
        for lead_raw in new_leads:
            from app.services.sync import upsert_lead_from_raw
            await upsert_lead_from_raw(lead_raw, db)
        await db.commit()
    else:
        errors.append(f"Erro API Kommo {resp.status_code}: {resp.text[:200]}")

    return created, updated, errors
