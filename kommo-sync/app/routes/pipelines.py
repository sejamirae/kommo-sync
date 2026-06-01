# app/routes/pipelines.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.database import get_db
from app.services.kommo import get_valid_token
from app.config import get_settings

router = APIRouter(prefix="/pipelines", tags=["Pipelines"])
settings = get_settings()
BASE_URL = f"https://{settings.KOMMO_DOMAIN}/api/v4"


@router.get("/", summary="Lista todos os pipelines com etapas e IDs")
async def list_pipelines(db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)
    if not access_token:
        raise HTTPException(status_code=401, detail="Token não encontrado. Faça OAuth primeiro.")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/leads/pipelines",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"with": "statuses"},
        )
        resp.raise_for_status()
        data = resp.json()

    pipelines = data.get("_embedded", {}).get("pipelines", [])
    result = []
    for p in pipelines:
        result.append({
            "pipeline_id": p["id"],
            "pipeline_name": p["name"],
            "is_archive": p.get("is_archive", False),
            "stages": [
                {
                    "status_id": s["id"],
                    "name": s["name"],
                    "color": s.get("color", ""),
                    "type": s.get("type", 0),  # 0=normal, 142=won, 143=lost
                }
                for s in p.get("_embedded", {}).get("statuses", [])
            ]
        })

    return result
