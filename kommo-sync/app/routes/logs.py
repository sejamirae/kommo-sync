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
@router.get("/cleanup-now", summary="Limpa logs via GET (use no browser)")
async def cleanup_logs(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    results = {}
    try:
        await db.execute(text("TRUNCATE TABLE sync_log, contact_phones, contact_emails, contacts CASCADE"))
        await db.commit()
        results["truncated"] = ["sync_log", "contact_phones", "contact_emails", "contacts"]
    except Exception as e:
        await db.rollback()
        results["error"] = str(e)
    return results

@router.get("/db-size", summary="Tamanho de cada tabela no banco")
async def db_size(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT 
            relname as tabela,
            pg_size_pretty(pg_total_relation_size(relid)) as tamanho,
            pg_total_relation_size(relid) as bytes
        FROM pg_catalog.pg_statio_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
    """))
    rows = result.fetchall()
    return [{"tabela": r[0], "tamanho": r[1], "bytes": r[2]} for r in rows]
