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
        r1 = await db.execute(text("DELETE FROM sync_log"))
        results["sync_log_deleted"] = r1.rowcount
        # Limpa contatos de outros pipelines (não são necessários)
        r2 = await db.execute(text("""
            DELETE FROM contact_phones WHERE contact_id NOT IN (
                SELECT DISTINCT contact_id FROM leads l
                JOIN contacts c ON c.id = l.responsible_user_id
                WHERE l.pipeline_id = 13865228
            )
        """))
        results["contact_phones_deleted"] = r2.rowcount
        r3 = await db.execute(text("""
            DELETE FROM contacts WHERE id NOT IN (
                SELECT DISTINCT responsible_user_id FROM leads WHERE pipeline_id = 13865228
            )
        """))
        results["contacts_deleted"] = r3.rowcount
        await db.commit()
    except Exception as e:
        await db.rollback()
        try:
            await db.execute(text("TRUNCATE TABLE sync_log"))
            await db.execute(text("TRUNCATE TABLE contact_phones"))
            await db.commit()
            results["truncated"] = True
        except Exception as e2:
            results["error"] = str(e2)
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
