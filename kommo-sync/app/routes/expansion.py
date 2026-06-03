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

# IDs dos custom fields criados via setup-fields
FIELD_IDS = {
    "Nome Completo":      4330963,
    "CRM":                4330965,
    "Telefone Médico":    4330967,
    "Unidade":            4330969,
    "Dia da Semana":      4330971,
    "Frequência":         4330973,
    "Horário":            4330975,
    "Nº de Horas":       4330977,
    "Data Envio":         4330979,
    "Data Fechamento":    4330981,
    "Previsão Início":    4330983,
    "Unidade Pagamento":  4330985,
    "Valor Mirae":        4330987,
    "Valor Médico":       4330989,
    "Onboarding":         4330991,
    "Origem":             4330993,
    "Gestor":             4330995,
    "DoctorID":           4330997,
    "Pendências":         4330999,
    "Observações":        4331001,
}

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
    status_lead: Optional[str] = None
    observacoes: Optional[str] = None


class NoteIn(BaseModel):
    type: str
    text: str
    author: Optional[str] = "Equipe"


# ── Setup: cria custom fields no pipeline ────────────────────────────

@router.post("/migrate-db", summary="Adiciona colunas novas ao banco (rodar quando necessário)")
async def migrate_db(db: AsyncSession = Depends(get_db)):
    """Adiciona colunas que foram adicionadas ao model mas não existem no banco."""
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE expansion_fields ADD COLUMN IF NOT EXISTS status_lead VARCHAR(255)",
        "ALTER TABLE expansion_fields ADD COLUMN IF NOT EXISTS pendencias TEXT",
    ]
    results = []
    for sql in migrations:
        try:
            await db.execute(text(sql))
            results.append({"sql": sql, "ok": True})
        except Exception as ex:
            results.append({"sql": sql, "error": str(ex)})
    await db.commit()
    return {"migrations": results}


@router.post("/setup-fields", summary="Cria custom fields do pipeline Expansão na Kommo (rodar 1x)")
async def setup_custom_fields(db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)
    headers = {"Authorization": f"Bearer {access_token}"}

    # Busca TODOS os custom fields de leads (sem filtro de pipeline)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE}/leads/custom_fields", headers=headers, params={"limit": 250})
        all_fields = resp.json().get("_embedded", {}).get("custom_fields", [])
        existing = {f["name"]: f["id"] for f in all_fields}

    created, skipped, errors = [], [], []
    async with httpx.AsyncClient() as client:
        for field in CUSTOM_FIELDS:
            if field["name"] in existing:
                skipped.append(f"{field['name']} (id:{existing[field['name']]})")
                continue
            # Cria sem pipeline_id — campos globais de leads
            r = await client.post(
                f"{BASE}/leads/custom_fields",
                headers=headers,
                json=[{"name": field["name"], "type": field["type"]}],
            )
            if r.status_code in (200, 201):
                data = r.json().get("_embedded", {}).get("custom_fields", [])
                if data:
                    created.append(f"{field['name']} (id:{data[0]['id']})")
            else:
                errors.append(f"{field['name']}: {r.status_code} {r.text[:100]}")

    return {"created": created, "skipped": skipped, "errors": errors}


@router.get("/contact-fields", summary="Lista campos padrão dos contatos na Kommo")
async def get_contact_fields(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx
    access_token = await get_valid_token(db)
    async with _httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE}/contacts/custom_fields",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return resp.json()


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


@router.post("/fields/{lead_id}", summary="Salva campos extras — banco + Kommo custom fields + contato")
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

    access_token = await get_valid_token(db)
    headers = {"Authorization": f"Bearer {access_token}"}
    data = body.model_dump(exclude_none=True)

    def to_ts(date_str):
        if not date_str: return None
        try: return int(datetime.datetime.strptime(date_str, "%Y-%m-%d").timestamp())
        except: return None

    async with httpx.AsyncClient(timeout=30) as client:
        # 2. Atualiza custom fields do lead usando IDs fixos
        name_to_val = {
            "Nome Completo":     data.get("nome_completo"),
            "CRM":               data.get("crm"),
            "Telefone Médico":   data.get("telefone"),
            "Unidade":           data.get("unidade"),
            "Dia da Semana":     data.get("dia_semana"),
            "Frequência":        data.get("frequencia"),
            "Horário":           data.get("horario"),
            "Nº de Horas":      data.get("horas"),
            "Data Envio":        to_ts(data.get("data_envio")),
            "Data Fechamento":   to_ts(data.get("data_fechamento")),
            "Previsão Início":   to_ts(data.get("previsao_inicio")),
            "Unidade Pagamento": data.get("unidade_pagamento"),
            "Valor Mirae":       data.get("valor_mirae"),
            "Valor Médico":      data.get("valor_medico"),
            "Onboarding":        data.get("onboarding"),
            "Origem":            data.get("origem"),
            "Gestor":            data.get("gestor"),
            "DoctorID":          data.get("doctorid"),
            "Pendências":        data.get("pendencias"),
            "Observações":       data.get("observacoes"),
        }
        cfv = [
            {"field_id": FIELD_IDS[fname], "values": [{"value": fval}]}
            for fname, fval in name_to_val.items()
            if fval is not None and fval != "" and fname in FIELD_IDS
        ]
        if cfv:
            await client.patch(f"{BASE}/leads", headers=headers,
                               json=[{"id": lead_id, "custom_fields_values": cfv}])

        # 3. Cria/atualiza contato vinculado com Tel. comercial
        telefone = data.get("telefone", "")
        nome = data.get("nome_completo", "")

        if telefone or nome:
            contact_id = None

            # Busca contato já vinculado ao lead
            r_lead = await client.get(
                f"{BASE}/leads/{lead_id}",
                headers=headers,
                params={"with": "contacts"},
            )
            if r_lead.status_code == 200:
                embedded = r_lead.json().get("_embedded", {})
                linked = embedded.get("contacts", [])
                if linked:
                    contact_id = linked[0]["id"]

            contact_payload = {"name": nome or "Médico"}
            if telefone:
                contact_payload["custom_fields_values"] = [
                    {"field_id": 3058666, "values": [{"value": telefone, "enum_id": 7088034}]}
                ]

            if contact_id:
                await client.patch(
                    f"{BASE}/contacts",
                    headers=headers,
                    json=[{"id": contact_id, **contact_payload}],
                )
            else:
                # Cria novo contato e vincula
                rc = await client.post(
                    f"{BASE}/contacts",
                    headers=headers,
                    json=[contact_payload],
                )
                if rc.status_code in (200, 201):
                    new_contacts = rc.json().get("_embedded", {}).get("contacts", [])
                    if new_contacts:
                        contact_id = new_contacts[0]["id"]
                        await client.post(
                            f"{BASE}/contacts/{contact_id}/links",
                            headers=headers,
                            json=[{"to_entity_id": lead_id, "to_entity_type": "leads"}],
                        )

    return {"ok": True}


# ── Notas ─────────────────────────────────────────────────────────────

@router.get("/notes/{lead_id}", summary="Notas manuais do banco")
async def get_notes(lead_id: int, db: AsyncSession = Depends(get_db)):
    import pytz
    tz_br = pytz.timezone('America/Sao_Paulo')
    result = await db.execute(
        select(ExpansionNote).where(ExpansionNote.lead_id == lead_id).order_by(ExpansionNote.created_at)
    )
    def fmt_dt(dt):
        if not dt: return ""
        try: return pytz.utc.localize(dt).astimezone(tz_br).strftime("%d/%m/%Y %H:%M")
        except: return dt.strftime("%d/%m/%Y %H:%M")
    return [
        {"id": n.id, "type": n.type, "text": n.text, "author": n.author,
         "date": fmt_dt(n.created_at), "source": "local"}
        for n in result.scalars().all()
    ]


@router.post("/notes/{lead_id}", summary="Adiciona nota")
async def add_note(lead_id: int, body: NoteIn, db: AsyncSession = Depends(get_db)):
    note = ExpansionNote(lead_id=lead_id, type=body.type, text=body.text, author=body.author)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return {"id": note.id, "type": note.type, "text": note.text, "author": note.author,
            "date": (note.created_at.replace(tzinfo=__import__('pytz').utc).astimezone(__import__('pytz').timezone('America/Sao_Paulo'))).strftime("%d/%m/%Y %H:%M") if note.created_at else "", "source": "local"}


@router.get("/kommo-notes/{lead_id}", summary="Histórico de notas da Kommo")
async def get_kommo_notes(lead_id: int, db: AsyncSession = Depends(get_db)):
    access_token = await get_valid_token(db)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE}/leads/{lead_id}/notes",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": 50, "order[id]": "asc"},
        )
        if resp.status_code in (404, 204):
            return []
        if not resp.content:
            return []
        try:
            data = resp.json()
        except Exception:
            return []

    type_map = {1:"ligacao",2:"email",3:"nota",10:"whatsapp",25:"whatsapp",102:"email"}
    result = []
    for n in data.get("_embedded", {}).get("notes", []):
        note_type = n.get("note_type", 3)
        params = n.get("params", {})
        text = params.get("text") or params.get("description") or params.get("body") or n.get("text","") or f"[evento tipo {note_type}]"
        created = n.get("created_at", 0)
        import pytz
        tz_br = pytz.timezone("America/Sao_Paulo")
        dt = datetime.datetime.fromtimestamp(created, tz=tz_br).strftime("%d/%m/%Y %H:%M") if created else ""
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

@router.post("/import-batch", summary="Importa múltiplos leads de uma vez (mais rápido)")
async def import_batch(leads_data: list[dict], db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    from app.models.db import Lead
    import httpx as _httpx

    access_token = await get_valid_token(db)
    headers_kommo = {"Authorization": f"Bearer {access_token}"}
    PIPELINE_ID_BATCH = 13865228
    STATUS_ID_BATCH = 107011876

    if not leads_data:
        return {"created": 0, "errors": []}

    created_ids = []
    errors = []
    batch_size = 50

    async with _httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(leads_data), batch_size):
            batch = leads_data[i:i+batch_size]
            payload = []
            for l in batch:
                lead_data = {
                    "name": l.get("nome_completo") or l.get("name", "Lead"),
                    "status_id": STATUS_ID_BATCH,
                    "pipeline_id": PIPELINE_ID_BATCH,
                    "price": 0,
                }
                # Embed contact with phone directly in lead creation
                telefone = l.get("telefone", "")
                nome = l.get("nome_completo", "")
                if telefone or nome:
                    contact = {"name": nome or "Médico"}
                    if telefone:
                        contact["custom_fields_values"] = [
                            {"field_id": 3058666, "values": [{"value": str(telefone), "enum_id": 7088034}]}
                        ]
                    lead_data["_embedded"] = {"contacts": [contact]}
                payload.append(lead_data)
            resp = await client.post(
                f"{BASE}/leads",
                headers=headers_kommo,
                json=payload,
            )
            if resp.status_code not in (200, 201):
                errors.append(f"Batch {i//batch_size+1}: HTTP {resp.status_code} {resp.text[:100]}")
                continue

            new_leads = resp.json().get("_embedded", {}).get("leads", [])
            for idx2, lead_raw in enumerate(new_leads):
                lead_id = lead_raw["id"]
                created_ids.append(lead_id)

                # Upsert lead no banco - ignora se já existe (webhook pode ter chegado primeiro)
                from sqlalchemy import text as _text
                await db.execute(_text("""
                    INSERT INTO leads (id, name, status_id, pipeline_id, price)
                    VALUES (:id, :name, :status_id, :pipeline_id, :price)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        status_id = EXCLUDED.status_id,
                        pipeline_id = EXCLUDED.pipeline_id
                """), {
                    "id": lead_id,
                    "name": lead_raw.get("name"),
                    "status_id": lead_raw.get("status_id"),
                    "pipeline_id": lead_raw.get("pipeline_id"),
                    "price": lead_raw.get("price", 0),
                })

                # Salva campos extras
                if idx2 < len(batch):
                    extra = batch[idx2]
                    row_ef = await db.execute(
                        select(ExpansionField).where(ExpansionField.lead_id == lead_id)
                    )
                    ef = row_ef.scalar_one_or_none()
                    if not ef:
                        ef = ExpansionField(lead_id=lead_id)
                        db.add(ef)
                    for field in ['nome_completo','crm','telefone','cliente','especialidade',
                                 'unidade','dia_semana','frequencia','horario','horas',
                                 'unidade_pagamento','valor_mirae','valor_medico','onboarding',
                                 'origem','gestor','doctorid','status_lead','previsao_inicio']:
                        val = extra.get(field)
                        if val is not None and str(val).strip():
                            setattr(ef, field, str(val).strip())

                    # Atualiza custom fields na Kommo
                    cfv = []
                    field_map = {
                        'nome_completo': 4330963, 'crm': 4330965, 'telefone': 4330967,
                        'unidade': 4330969, 'dia_semana': 4330971, 'frequencia': 4330973,
                        'horario': 4330975, 'horas': 4330977, 'unidade_pagamento': 4330985,
                        'valor_mirae': 4330987, 'valor_medico': 4330989, 'onboarding': 4330991,
                        'origem': 4330993, 'gestor': 4330995, 'doctorid': 4330997,
                        # Novos campos — IDs serão preenchidos após setup-fields
                        # 'cliente': ID_CLIENTE, 'especialidade': ID_ESPECIALIDADE,
                    }
                    for fname, fid in field_map.items():
                        v = extra.get(fname)
                        if v and str(v).strip():
                            cfv.append({"field_id": fid, "values": [{"value": str(v).strip()}]})
                    if cfv:
                        await client.patch(
                            f"{BASE}/leads",
                            headers=headers_kommo,
                            json=[{"id": lead_id, "custom_fields_values": cfv}],
                        )

                    # Contato já foi criado e vinculado no payload do lead acima

            try:
                await db.commit()
            except Exception as ex:
                await db.rollback()
                errors.append(f"DB commit error batch {i//batch_size+1}: {str(ex)[:100]}")

    return {"created": len(created_ids), "errors": errors}

@router.post("/fix-contacts", summary="Cria contatos para leads que ainda não têm Tel. comercial")
async def fix_contacts(db: AsyncSession = Depends(get_db)):
    """Varre todos os leads do pipeline e cria contato vinculado com telefone."""
    from app.services.kommo import get_valid_token
    from app.models.db import Lead
    import httpx as _httpx

    access_token = await get_valid_token(db)
    headers_kommo = {"Authorization": f"Bearer {access_token}"}

    # Busca todos os leads do pipeline com telefone preenchido
    result = await db.execute(
        select(ExpansionField).where(
            ExpansionField.telefone.isnot(None),
            ExpansionField.telefone != ""
        )
    )
    fields = result.scalars().all()

    fixed = 0
    errors = []

    async with _httpx.AsyncClient(timeout=30) as client:
        for ef in fields:
            try:
                # Verifica se já tem contato vinculado
                r_lead = await client.get(
                    f"{BASE}/leads/{ef.lead_id}",
                    headers=headers_kommo,
                    params={"with": "contacts"},
                )
                if r_lead.status_code != 200:
                    continue

                linked = r_lead.json().get("_embedded", {}).get("contacts", [])

                contact_payload = {"name": ef.nome_completo or "Médico"}
                if ef.telefone:
                    contact_payload["custom_fields_values"] = [
                        {"field_id": 3058666, "values": [{"value": ef.telefone, "enum_id": 7088034}]}
                    ]

                if linked:
                    # Atualiza contato existente
                    contact_id = linked[0]["id"]
                    await client.patch(
                        f"{BASE}/contacts",
                        headers=headers_kommo,
                        json=[{"id": contact_id, **contact_payload}],
                    )
                else:
                    # Cria e vincula novo contato
                    rc = await client.post(
                        f"{BASE}/contacts",
                        headers=headers_kommo,
                        json=[contact_payload],
                    )
                    if rc.status_code in (200, 201):
                        new_contacts = rc.json().get("_embedded", {}).get("contacts", [])
                        if new_contacts:
                            cid = new_contacts[0]["id"]
                            await client.post(
                                f"{BASE}/leads/{ef.lead_id}/links",
                                headers=headers_kommo,
                                json=[{"to_entity_id": cid, "to_entity_type": "contacts"}],
                            )
                            fixed += 1
            except Exception as ex:
                errors.append(f"lead {ef.lead_id}: {str(ex)[:80]}")

    return {"fixed": fixed, "errors": errors}
