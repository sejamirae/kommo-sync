# app/routes/oauth.py
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.kommo import exchange_code_for_token, get_authorization_url

router = APIRouter(prefix="/oauth", tags=["OAuth"])


@router.get("/authorize", summary="Inicia fluxo OAuth com a Kommo")
async def authorize():
    url = get_authorization_url(state="kommo-sync")
    return HTMLResponse(f"""
    <html><body>
    <h2>Conectar Kommo</h2>
    <a href="{url}" target="_blank">
        <button style="padding:12px 24px;font-size:16px;background:#2563eb;color:#fff;border:none;border-radius:8px;cursor:pointer">
            Autorizar no Kommo
        </button>
    </a>
    <p style="color:#666;font-size:13px">Após autorizar, você será redirecionado de volta automaticamente.</p>
    </body></html>
    """)


@router.get("/callback", summary="Callback OAuth — recebe o code da Kommo")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    try:
        token = await exchange_code_for_token(code, db)
        return HTMLResponse(f"""
        <html><body>
        <h2 style="color:green">✅ Kommo conectado!</h2>
        <p>Token salvo. Expira em: <b>{token.expires_at}</b></p>
        <p>Domínio: <b>{token.domain}</b></p>
        <p>Você já pode fechar esta janela.</p>
        </body></html>
        """)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
