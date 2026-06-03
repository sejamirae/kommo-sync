# app/routes/logs.py
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import SyncLog

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get("/", summary="Lista logs de sincronização (últimos 200)")
async def list_logs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SyncLog).order_by(SyncLog.created_at.desc()).limit(200)
    )
    logs = result.scalars().all()
    return [
        {
            "id":          l.id,
            "event_type":  l.event_type,
            "entity_type": l.entity_type,
            "entity_id":   l.entity_id,
            "status":      l.status,
            "message":     l.message,
            "created_at":  l.created_at,
        }
        for l in logs
    ]

@router.delete("/cleanup", summary="Limpa logs antigos para liberar espaço no disco")
async def cleanup_logs(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    results = {}
    
    # Apaga todos os sync_log
    r1 = await db.execute(text("DELETE FROM sync_log"))
    results["sync_log_deleted"] = r1.rowcount
    
    # Apaga notas de status antigas (mais de 30 dias)
    r2 = await db.execute(text(
        "DELETE FROM expansion_notes WHERE type=\'status\' AND created_at < NOW() - INTERVAL \'30 days\'"
    ))
    results["old_status_notes_deleted"] = r2.rowcount

    await db.commit()
    return results
