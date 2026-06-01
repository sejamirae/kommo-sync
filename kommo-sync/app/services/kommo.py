# app/services/kommo.py
"""
Serviço central para autenticação OAuth e chamadas à API da Kommo.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.db import KommoToken

settings = get_settings()
BASE_URL = f"https://{settings.KOMMO_DOMAIN}/api/v4"


# ─────────────────────────────────────────────
# OAuth
# ─────────────────────────────────────────────

def get_authorization_url(state: str = "init") -> str:
    """Gera URL para redirecionar o usuário e iniciar o fluxo OAuth."""
    return (
        f"https://www.kommo.com/oauth?"
        f"client_id={settings.KOMMO_CLIENT_ID}"
        f"&state={state}"
        f"&mode=post_message"
    )


async def exchange_code_for_token(code: str, db: AsyncSession) -> KommoToken:
    """Troca o code OAuth por access_token + refresh_token e salva no banco."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{settings.KOMMO_DOMAIN}/oauth2/access_token",
            json={
                "client_id":     settings.KOMMO_CLIENT_ID,
                "client_secret": settings.KOMMO_CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  settings.KOMMO_REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return await _save_token(data, db)


async def _refresh_token(token: KommoToken, db: AsyncSession) -> KommoToken:
    """Renova o access_token usando o refresh_token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{settings.KOMMO_DOMAIN}/oauth2/access_token",
            json={
                "client_id":     settings.KOMMO_CLIENT_ID,
                "client_secret": settings.KOMMO_CLIENT_SECRET,
                "grant_type":    "refresh_token",
                "refresh_token": token.refresh_token,
                "redirect_uri":  settings.KOMMO_REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return await _save_token(data, db)


async def _save_token(data: dict, db: AsyncSession) -> KommoToken:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"] - 60)

    result = await db.execute(
        select(KommoToken).where(KommoToken.domain == settings.KOMMO_DOMAIN)
    )
    token = result.scalar_one_or_none()

    if token:
        token.access_token  = data["access_token"]
        token.refresh_token = data["refresh_token"]
        token.expires_at    = expires_at
    else:
        token = KommoToken(
            domain        = settings.KOMMO_DOMAIN,
            access_token  = data["access_token"],
            refresh_token = data["refresh_token"],
            expires_at    = expires_at,
        )
        db.add(token)

    await db.commit()
    await db.refresh(token)
    return token


async def get_valid_token(db: AsyncSession) -> Optional[str]:
    """Retorna um access_token válido, renovando automaticamente se necessário."""
    result = await db.execute(
        select(KommoToken).where(KommoToken.domain == settings.KOMMO_DOMAIN)
    )
    token = result.scalar_one_or_none()

    if not token:
        return None

    if token.expires_at <= datetime.now(timezone.utc):
        token = await _refresh_token(token, db)

    return token.access_token


# ─────────────────────────────────────────────
# Leads
# ─────────────────────────────────────────────

async def get_leads(db: AsyncSession, page: int = 1, limit: int = 250) -> list[dict]:
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/leads",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"page": page, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json().get("_embedded", {}).get("leads", [])


async def update_lead_status(lead_id: int, status_id: int, db: AsyncSession) -> dict:
    """Move um lead para outra etapa no pipeline."""
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{BASE_URL}/leads",
            headers={"Authorization": f"Bearer {access_token}"},
            json=[{"id": lead_id, "status_id": status_id}],
        )
        resp.raise_for_status()
        return resp.json()


async def create_lead(name: str, status_id: int, pipeline_id: int,
                      price: int = 0, db: AsyncSession = None) -> dict:
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/leads",
            headers={"Authorization": f"Bearer {access_token}"},
            json=[{
                "name":        name,
                "status_id":   status_id,
                "pipeline_id": pipeline_id,
                "price":       price,
            }],
        )
        resp.raise_for_status()
        return resp.json()


# ─────────────────────────────────────────────
# Contatos
# ─────────────────────────────────────────────

async def get_contacts(db: AsyncSession, page: int = 1, limit: int = 250) -> list[dict]:
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/contacts",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"page": page, "limit": limit, "with": "leads"},
        )
        resp.raise_for_status()
        return resp.json().get("_embedded", {}).get("contacts", [])


async def upsert_contact(contact_data: dict, db: AsyncSession) -> dict:
    """Cria ou atualiza um contato na Kommo."""
    access_token = await get_valid_token(db)
    contact_id = contact_data.get("id")

    async with httpx.AsyncClient() as client:
        if contact_id:
            resp = await client.patch(
                f"{BASE_URL}/contacts",
                headers={"Authorization": f"Bearer {access_token}"},
                json=[contact_data],
            )
        else:
            resp = await client.post(
                f"{BASE_URL}/contacts",
                headers={"Authorization": f"Bearer {access_token}"},
                json=[contact_data],
            )
        resp.raise_for_status()
        return resp.json()
