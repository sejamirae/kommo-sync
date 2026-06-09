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
    "First Name":         4331519,
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
    "Especialidade":      4331377,
    "Cliente":            4331379,
    "Vaga":               4331511,
    "Descrição da Vaga":  4331513,
}

# Campos que queremos criar/manter na Kommo
CUSTOM_FIELDS = [
    {"name": "First Name",           "type": "text"},
    {"name": "Nome Completo",       "type": "text"},
    {"name": "CRM",                 "type": "text"},
    {"name": "Telefone Médico",     "type": "text"},
    {"name": "Especialidade",       "type": "text"},
    {"name": "Vaga",                 "type": "text"},
    {"name": "Descrição da Vaga",    "type": "textarea"},
    {"name": "Cliente",             "type": "text"},
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
    primeiro_nome: Optional[str] = None
    vaga: Optional[str] = None
    descricao_vaga: Optional[str] = None
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
        "ALTER TABLE expansion_fields ADD COLUMN IF NOT EXISTS primeiro_nome VARCHAR(255)",
        "ALTER TABLE expansion_fields ADD COLUMN IF NOT EXISTS vaga VARCHAR(500)",
        "ALTER TABLE expansion_fields ADD COLUMN IF NOT EXISTS descricao_vaga TEXT",
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
            "First Name":        data.get("primeiro_nome"),
            "Nome Completo":     data.get("nome_completo"),
            "CRM":               data.get("crm"),
            "Telefone Médico":   data.get("telefone"),
            "Especialidade":     data.get("especialidade"),
            "Cliente":           data.get("cliente"),
            "Vaga":              data.get("vaga"),
            "Descrição da Vaga": data.get("descricao_vaga"),
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
        try:
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt)
            return dt.astimezone(tz_br).strftime("%d/%m/%Y %H:%M")
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
            "date": (__import__('pytz').utc.localize(note.created_at) if note.created_at and note.created_at.tzinfo is None else note.created_at).astimezone(__import__('pytz').timezone('America/Sao_Paulo')).strftime("%d/%m/%Y %H:%M") if note.created_at else "", "source": "local"}


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

@router.post("/import-batch", summary="Importa múltiplos leads em lote com upsert inteligente")
async def import_batch(leads_data: list[dict], db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx
    from sqlalchemy import text as _text
    from datetime import datetime, timezone
    import pytz

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}
    PIPELINE_ID_BATCH = 13865228
    STATUS_ID_BATCH = 107011876
    FIELD_MAP = {
        'primeiro_nome': 4331519, 'nome_completo': 4330963, 'crm': 4330965, 'telefone': 4330967,
        'especialidade': 4331377, 'cliente': 4331379,
        'unidade': 4330969, 'dia_semana': 4330971, 'frequencia': 4330973,
        'horario': 4330975, 'horas': 4330977, 'unidade_pagamento': 4330985,
        'valor_mirae': 4330987, 'valor_medico': 4330989, 'onboarding': 4330991,
        'origem': 4330993, 'gestor': 4330995, 'doctorid': 4330997,
        'vaga': 4331511, 'descricao_vaga': 4331513,
    }
    tz_br = pytz.timezone('America/Sao_Paulo')

    if not leads_data:
        return {"created": 0, "updated": 0, "errors": []}

    created_ids = []
    updated_ids = []
    errors = []
    BATCH = 50

    # Busca todos os expansion_fields existentes para fazer match
    existing_ef = await db.execute(select(ExpansionField))
    all_ef = existing_ef.scalars().all()
    # Índice por chave CRM+primeiro_nome e fallback por nome_completo
    ef_index = {}
    for ef in all_ef:
        crm = (ef.crm or "").strip()
        pnome = (ef.primeiro_nome or "").strip().lower()
        nome = (ef.nome_completo or "").strip().lower()
        if crm and pnome:
            ef_index[f"{crm}_{pnome}"] = ef
        elif nome:
            ef_index[nome] = ef

    async with _httpx.AsyncClient(timeout=60) as client:
        # Separa novos de existentes
        to_create = []
        to_update = []

        for l in leads_data:
            nome = str(l.get("nome_completo", "")).strip()
            crm = str(l.get("crm", "")).strip()
            pnome = str(l.get("primeiro_nome") or "").strip()
            if not pnome and nome:
                p = nome.split()[0]
                pnome = p[0].upper() + p[1:].lower()
                l["primeiro_nome"] = pnome

            # Gera chave de match
            key = f"{crm}_{pnome.lower()}" if crm and pnome else nome.lower()
            existing = ef_index.get(key)

            if existing:
                to_update.append((existing, l))
            else:
                to_create.append(l)

        # ── CRIAR LEADS NOVOS ──────────────────────────────────────
        for i in range(0, len(to_create), BATCH):
            batch = to_create[i:i+BATCH]
            payload = []
            for l in batch:
                nome = str(l.get("nome_completo", "")).strip()
                telefone = str(l.get("telefone", "")).strip()
                pnome = l.get("primeiro_nome") or nome
                contact = {"name": nome.title() if nome else "Médico"}
                if telefone:
                    contact["custom_fields_values"] = [
                        {"field_id": 3058666, "values": [{"value": telefone, "enum_id": 7088034}]}
                    ]
                payload.append({
                    "name": pnome,
                    "status_id": STATUS_ID_BATCH,
                    "pipeline_id": PIPELINE_ID_BATCH,
                    "price": 0,
                    "_embedded": {"contacts": [contact]}
                })

            resp = await client.post(f"{BASE}/leads/complex", headers=H, json=payload)
            if resp.status_code not in (200, 201):
                resp2 = await client.post(f"{BASE}/leads", headers=H, json=[
                    {"name": l.get("primeiro_nome") or str(l.get("nome_completo","")).strip() or "Lead",
                     "status_id": STATUS_ID_BATCH, "pipeline_id": PIPELINE_ID_BATCH, "price": 0}
                    for l in batch])
                if resp2.status_code not in (200, 201):
                    errors.append(f"Batch criação: HTTP {resp.status_code}")
                    continue
                resp = resp2

            resp_data = resp.json()
            new_leads = resp_data if isinstance(resp_data, list) else resp_data.get("_embedded", {}).get("leads", [])

            for idx2, lead_raw in enumerate(new_leads):
                lead_id = lead_raw["id"]
                extra = batch[idx2] if idx2 < len(batch) else {}
                created_ids.append((lead_id, extra))

                try:
                    await db.execute(_text("""
                        INSERT INTO leads (id, name, status_id, pipeline_id, price)
                        VALUES (:id, :name, :status_id, :pipeline_id, :price)
                        ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,
                            status_id=EXCLUDED.status_id, pipeline_id=EXCLUDED.pipeline_id
                    """), {"id": lead_id,
                           "name": extra.get("primeiro_nome") or str(extra.get("nome_completo","")).strip() or "Lead",
                           "status_id": STATUS_ID_BATCH, "pipeline_id": PIPELINE_ID_BATCH, "price": 0})

                    ef = ExpansionField(lead_id=lead_id)
                    db.add(ef)
                    for field in ['nome_completo','primeiro_nome','crm','telefone','cliente','especialidade',
                                 'vaga','descricao_vaga','unidade','dia_semana','frequencia','horario','horas',
                                 'unidade_pagamento','valor_mirae','valor_medico','onboarding',
                                 'origem','gestor','doctorid','status_lead','previsao_inicio']:
                        val = extra.get(field)
                        if val is not None and str(val).strip():
                            setattr(ef, field, str(val).strip())

                    await db.commit()
                except Exception as ex:
                    await db.rollback()
                    errors.append(f"DB create {lead_id}: {str(ex)[:80]}")

        # ── ATUALIZAR LEADS EXISTENTES ─────────────────────────────
        cfv_update = []
        for (ef, l) in to_update:
            lead_id = ef.lead_id
            now_br = datetime.now(tz_br).strftime("%d/%m/%Y %H:%M")
            changes = []

            # Especialidade — acumula
            esp_nova = str(l.get("especialidade", "")).strip()
            if esp_nova:
                esps_atuais = [e.strip() for e in (ef.especialidade or "").split(",") if e.strip()]
                if esp_nova not in esps_atuais:
                    esps_atuais.append(esp_nova)
                    changes.append(f"Especialidade adicionada: {esp_nova}")
                    ef.especialidade = ", ".join(esps_atuais)

            # Campos que atualizam e registram mudança no histórico
            for field, label in [
                ('vaga', 'Vaga'),
                ('descricao_vaga', 'Descrição da Vaga'),
                ('cliente', 'Cliente'),
                ('origem', 'Origem'),
                ('gestor', 'Gestor'),
            ]:
                novo = str(l.get(field, "")).strip()
                atual = str(getattr(ef, field) or "").strip()
                if novo and novo != atual:
                    if atual:
                        changes.append(f'{label}: "{atual}" → "{novo}"')
                    else:
                        changes.append(f'{label}: "{novo}"')
                    setattr(ef, field, novo)

            # Campos cadastrais — atualiza direto
            for field in ['nome_completo', 'primeiro_nome', 'crm', 'telefone']:
                novo = str(l.get(field, "")).strip()
                if novo:
                    setattr(ef, field, novo)

            # Agenda e Financeiro — limpa e registra no histórico
            agenda_fields = ['unidade','dia_semana','frequencia','horario','horas','previsao_inicio']
            fin_fields = ['unidade_pagamento','valor_mirae','valor_medico','onboarding']
            agenda_vals = {f: str(getattr(ef, f) or "").strip() for f in agenda_fields if getattr(ef, f)}
            fin_vals = {f: str(getattr(ef, f) or "").strip() for f in fin_fields if getattr(ef, f)}

            if agenda_vals:
                agenda_str = ", ".join(f'{k}: "{v}"' for k,v in agenda_vals.items())
                changes.append(f"Agenda resetada ({agenda_str})")
                for f in agenda_fields:
                    setattr(ef, f, None)

            if fin_vals:
                fin_str = ", ".join(f'{k}: "{v}"' for k,v in fin_vals.items())
                changes.append(f"Financeiro resetado ({fin_str})")
                for f in fin_fields:
                    setattr(ef, f, None)

            try:
                await db.commit()
                updated_ids.append(lead_id)

                # Registra no histórico
                if changes:
                    texto = f"[Importacao {now_br}]\n" + "\n".join(changes)
                    note = ExpansionNote(lead_id=lead_id, type="nota", text=texto, author="Sistema")
                    db.add(note)
                    await db.commit()

                # Atualiza campos na Kommo
                cfv = [
                    {"field_id": fid, "values": [{"value": str(getattr(ef, fname) or "").strip()}]}
                    for fname, fid in FIELD_MAP.items()
                    if getattr(ef, fname, None) and str(getattr(ef, fname) or "").strip()
                ]
                if cfv:
                    cfv_update.append({"id": lead_id, "custom_fields_values": cfv})

            except Exception as ex:
                await db.rollback()
                errors.append(f"DB update {lead_id}: {str(ex)[:80]}")

        # PATCH campos novos na Kommo (criados)
        cfv_batch_new = []
        name_patch = []
        for lead_id, extra in created_ids:
            pnome = extra.get("primeiro_nome") or ""
            if not pnome:
                nc = str(extra.get("nome_completo","")).strip()
                if nc:
                    p = nc.split()[0]
                    pnome = p[0].upper() + p[1:].lower()
            if pnome:
                name_patch.append({"id": lead_id, "name": pnome})

            cfv = [
                {"field_id": fid, "values": [{"value": str(extra.get(fname,"")).strip()}]}
                for fname, fid in FIELD_MAP.items()
                if extra.get(fname) and str(extra.get(fname,"")).strip()
            ]
            if cfv:
                cfv_batch_new.append({"id": lead_id, "custom_fields_values": cfv})

        for i in range(0, len(name_patch), BATCH):
            await client.patch(f"{BASE}/leads", headers=H, json=name_patch[i:i+BATCH])
        for i in range(0, len(cfv_batch_new), BATCH):
            await client.patch(f"{BASE}/leads", headers=H, json=cfv_batch_new[i:i+BATCH])
        for i in range(0, len(cfv_update), BATCH):
            await client.patch(f"{BASE}/leads", headers=H, json=cfv_update[i:i+BATCH])

    return {"created": len(created_ids), "updated": len(updated_ids), "errors": errors}

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

@router.post("/test-contact-link", summary="Testa criação de lead com contato vinculado")
async def test_contact_link(db: AsyncSession = Depends(get_db)):
    """Cria 1 lead de teste e tenta vincular contato — retorna resposta completa da Kommo."""
    from app.services.kommo import get_valid_token
    import httpx as _httpx
    import logging

    access_token = await get_valid_token(db)
    headers = {"Authorization": f"Bearer {access_token}"}
    results = {}

    async with _httpx.AsyncClient(timeout=15) as client:
        # Método 1: /leads/complex
        payload_complex = [{
            "name": "TESTE CONTATO VINCULADO",
            "status_id": 107011876,
            "pipeline_id": 13865228,
            "price": 0,
            "_embedded": {
                "contacts": [{
                    "name": "DR TESTE",
                    "custom_fields_values": [
                        {"field_id": 3058666, "values": [{"value": "(11) 99999-0000", "enum_id": 7088034}]}
                    ]
                }]
            }
        }]
        r1 = await client.post(f"{BASE}/leads/complex", headers=headers, json=payload_complex)
        results["complex"] = {"status": r1.status_code, "body": r1.text[:500]}

    return results

# ─────────────────────────────────────────────
# Equipe: Gestores e Usuários (compartilhados)
# ─────────────────────────────────────────────

class TeamMemberIn(BaseModel):
    name: str
    role: str  # 'gestor' ou 'usuario'

@router.get("/team", summary="Lista gestores e usuários")
async def get_team(db: AsyncSession = Depends(get_db)):
    from app.models.db import TeamMember
    result = await db.execute(select(TeamMember).order_by(TeamMember.name))
    members = result.scalars().all()
    gestores = [m.name for m in members if m.role == "gestor"]
    usuarios = [m.name for m in members if m.role == "usuario"]
    return {"gestores": gestores, "usuarios": usuarios}

@router.post("/team", summary="Adiciona gestor ou usuário")
async def add_team_member(body: TeamMemberIn, db: AsyncSession = Depends(get_db)):
    from app.models.db import TeamMember
    name = body.name.strip()
    if not name:
        return {"ok": False, "error": "Nome vazio"}
    # Verifica duplicata
    existing = await db.execute(
        select(TeamMember).where(TeamMember.name == name, TeamMember.role == body.role)
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "duplicate": True}
    member = TeamMember(name=name, role=body.role)
    db.add(member)
    await db.commit()
    return {"ok": True}

@router.delete("/team", summary="Remove gestor ou usuário")
async def remove_team_member(name: str, role: str, db: AsyncSession = Depends(get_db)):
    from app.models.db import TeamMember
    from sqlalchemy import delete
    await db.execute(
        delete(TeamMember).where(TeamMember.name == name, TeamMember.role == role)
    )
    await db.commit()
    return {"ok": True}

# ─────────────────────────────────────────────
# Migração: campos texto → multiselect
# ─────────────────────────────────────────────

# Mapa de normalização de especialidades compostas → lista de opções
ESPECIALIDADE_SPLIT = {
    "CIRURGIA GERAL /CANCEROLOGIA CIRÚRGICA": ["CIRURGIA GERAL", "CANCEROLOGIA CIRÚRGICA"],
    "CIRURGIA GERAL / Ecografia Vascular com DOPPLER": ["CIRURGIA GERAL", "ECOGRAFIA VASCULAR COM DOPPLER"],
}

# Opções iniciais de cada campo multiselect
MULTISELECT_OPTIONS = {
    "Especialidade ▾": ["CIRURGIA GERAL", "CANCEROLOGIA CIRÚRGICA", "ECOGRAFIA VASCULAR COM DOPPLER",
                         "CLÍNICA MÉDICA", "DERMATOLOGIA", "PEDIATRIA", "MÉDICO SEM ESPECIALIDADE REGISTRADA"],
    "Cliente ▾": ["DR. CONSULTA", "HAPVIDA", "HOSPITAL SANTA CASA DE MAUÁ - MAUÁ/SP",
                  "HOSPITAL SÃO FRANCISCO - COTIA/SP"],
    "Gestor ▾": ["ALESSANDRA", "ANDRÉ", "FELIPE", "JÉSSICA", "PEDRO HENRIQUE"],
    "Vaga ▾": ["*Dermatologia* em diversas localidades de São Paulo/SP",
               "*Porta*: 12 MESES de experiência em P.A. com ACLS ativo. & *Médico Chefe*: 5 anos de experiência OU Residência/Pós-graduação na área com ACLS ativo. Em *COTIA-SP*.",
               "*Porta*: 12 MESES de experiência em P.A. & *Médico Chefe*: 1 ano de formação + experiencia em emergência. Em *MAUÁ-SP*.",
               "UTI em Santo André"],
}

# Mapa coluna do banco → nome do campo multiselect
COL_TO_MULTI = {
    "especialidade": "Especialidade ▾",
    "cliente": "Cliente ▾",
    "gestor": "Gestor ▾",
    "vaga": "Vaga ▾",
}


def _normalize_value(col, raw):
    """Normaliza um valor de texto para lista de opções do multiselect."""
    if not raw:
        return []
    raw = raw.strip()
    if col == "especialidade":
        # Verifica se é composta
        if raw in ESPECIALIDADE_SPLIT:
            return ESPECIALIDADE_SPLIT[raw]
        return [raw.upper()]
    elif col == "vaga":
        # Mantém o valor EXATO, sem normalizar
        return [raw]
    else:
        # cliente, gestor → maiúsculo
        return [raw.upper()]


@router.post("/create-multiselect-fields", summary="Cria campos multiselect na Kommo (rodar 1x)")
async def create_multiselect_fields(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    # Busca campos existentes
    async with _httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{BASE}/leads/custom_fields", headers=H, params={"limit": 250})
        all_fields = resp.json().get("_embedded", {}).get("custom_fields", [])
        existing = {f["name"]: f for f in all_fields}

        created = []
        result_ids = {}

        for fname, options in MULTISELECT_OPTIONS.items():
            if fname in existing:
                fid = existing[fname]["id"]
                result_ids[fname] = fid
                created.append(f"{fname} JÁ EXISTE (id:{fid})")
                continue

            # Cria campo multiselect com enums
            enums = [{"value": opt, "sort": i} for i, opt in enumerate(options)]
            payload = [{
                "name": fname,
                "type": "multiselect",
                "enums": enums,
            }]
            r = await client.post(f"{BASE}/leads/custom_fields", headers=H, json=payload)
            if r.status_code in (200, 201):
                new_field = r.json().get("_embedded", {}).get("custom_fields", [{}])[0]
                fid = new_field.get("id")
                result_ids[fname] = fid
                created.append(f"{fname} CRIADO (id:{fid})")
            else:
                created.append(f"{fname} ERRO: HTTP {r.status_code} - {r.text[:100]}")

    return {"created": created, "field_ids": result_ids}


@router.post("/migrate-to-multiselect", summary="Migra dados texto → multiselect (rodar após criar campos)")
async def migrate_to_multiselect(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    async with _httpx.AsyncClient(timeout=60) as client:
        # Busca os campos multiselect e seus enums
        resp = await client.get(f"{BASE}/leads/custom_fields", headers=H, params={"limit": 250})
        all_fields = resp.json().get("_embedded", {}).get("custom_fields", [])

        # Mapa: nome do campo → {id, {valor_upper: enum_id}}
        multi_fields = {}
        for f in all_fields:
            if f["name"] in MULTISELECT_OPTIONS:
                enum_map = {}
                for e in (f.get("enums") or []):
                    enum_map[e["value"].upper()] = e["id"]
                multi_fields[f["name"]] = {"id": f["id"], "enums": enum_map}

        if len(multi_fields) < 4:
            return {"error": "Campos multiselect não encontrados. Rode /create-multiselect-fields primeiro.",
                    "found": list(multi_fields.keys())}

    # Lê os expansion_fields do banco
    result = await db.execute(select(ExpansionField))
    rows = result.scalars().all()

    patches = []
    new_enums_needed = {}  # campo → set de valores que faltam

    for ef in rows:
        cfv = []
        for col, fname in COL_TO_MULTI.items():
            raw = getattr(ef, col, None)
            if not raw:
                continue
            opcoes = _normalize_value(col, raw)
            enum_map = multi_fields[fname]["enums"]
            enum_ids = []
            for opt in opcoes:
                eid = enum_map.get(opt.upper())
                if eid:
                    enum_ids.append(eid)
                else:
                    new_enums_needed.setdefault(fname, set()).add(opt)
            if enum_ids:
                cfv.append({
                    "field_id": multi_fields[fname]["id"],
                    "values": [{"enum_id": eid} for eid in enum_ids]
                })
        if cfv:
            patches.append({"id": ef.lead_id, "custom_fields_values": cfv})

    # Aplica os patches em lotes de 50
    BATCH = 50
    patched = 0
    errors = []
    async with _httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(patches), BATCH):
            chunk = patches[i:i+BATCH]
            r = await client.patch(f"{BASE}/leads", headers=H, json=chunk)
            if r.status_code in (200, 201):
                patched += len(chunk)
            else:
                errors.append(f"Lote {i}: HTTP {r.status_code} - {r.text[:80]}")

    return {
        "patched": patched,
        "total_leads": len(rows),
        "missing_enums": {k: list(v) for k, v in new_enums_needed.items()},
        "errors": errors,
    }

@router.post("/fix-vaga-multiselect", summary="Recria opções da Vaga com texto exato e remigra")
async def fix_vaga_multiselect(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    vaga_options = MULTISELECT_OPTIONS["Vaga ▾"]

    async with _httpx.AsyncClient(timeout=60) as client:
        # Encontra o campo Vaga ▾
        resp = await client.get(f"{BASE}/leads/custom_fields", headers=H, params={"limit": 250})
        all_fields = resp.json().get("_embedded", {}).get("custom_fields", [])
        vaga_field = next((f for f in all_fields if f["name"] == "Vaga ▾"), None)
        if not vaga_field:
            return {"error": "Campo Vaga ▾ não encontrado"}

        vaga_id = vaga_field["id"]

        # Atualiza os enums do campo com os valores exatos
        enums = [{"value": opt, "sort": i} for i, opt in enumerate(vaga_options)]
        r = await client.patch(
            f"{BASE}/leads/custom_fields/{vaga_id}",
            headers=H,
            json={"enums": enums},
        )
        if r.status_code not in (200, 201):
            return {"error": f"Erro ao atualizar enums: HTTP {r.status_code} - {r.text[:200]}"}

        # Busca os enums atualizados
        resp2 = await client.get(f"{BASE}/leads/custom_fields/{vaga_id}", headers=H)
        updated_field = resp2.json()
        enum_map = {e["value"]: e["id"] for e in (updated_field.get("enums") or [])}

    # Remigra a Vaga para todos os leads
    result = await db.execute(select(ExpansionField))
    rows = result.scalars().all()

    patches = []
    missing = set()
    for ef in rows:
        raw = (ef.vaga or "").strip()
        if not raw:
            continue
        eid = enum_map.get(raw)
        if eid:
            patches.append({
                "id": ef.lead_id,
                "custom_fields_values": [
                    {"field_id": vaga_id, "values": [{"enum_id": eid}]}
                ]
            })
        else:
            missing.add(raw)

    BATCH = 50
    patched = 0
    errors = []
    async with _httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(patches), BATCH):
            chunk = patches[i:i+BATCH]
            r = await client.patch(f"{BASE}/leads", headers=H, json=chunk)
            if r.status_code in (200, 201):
                patched += len(chunk)
            else:
                errors.append(f"Lote {i}: HTTP {r.status_code} - {r.text[:80]}")

    return {
        "enums_atualizados": list(enum_map.keys()),
        "patched": patched,
        "missing": list(missing),
        "errors": errors,
    }

@router.post("/delete-old-text-fields", summary="Apaga os 4 campos de texto antigos (Especialidade, Cliente, Gestor, Vaga)")
async def delete_old_text_fields(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    # IDs dos campos de TEXTO antigos (NÃO os multiselect com ▾)
    OLD_FIELDS = {
        "Especialidade": 4331377,
        "Cliente": 4331379,
        "Gestor": 4330995,
        "Vaga": 4331511,
    }

    deleted = []
    errors = []
    async with _httpx.AsyncClient(timeout=60) as client:
        for name, fid in OLD_FIELDS.items():
            r = await client.delete(f"{BASE}/leads/custom_fields/{fid}", headers=H)
            if r.status_code in (200, 204):
                deleted.append(f"{name} (id:{fid})")
            else:
                errors.append(f"{name} (id:{fid}): HTTP {r.status_code} - {r.text[:100]}")

    return {"deleted": deleted, "errors": errors}

@router.post("/rename-gestor-options", summary="Renomeia as opções do campo Gestor para nomes completos")
async def rename_gestor_options(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    # Mapa: valor antigo (maiúsculo) → nome novo
    RENAME = {
        "ALESSANDRA": "Alessandra Loreta",
        "PEDRO HENRIQUE": "Pedro Henrique",
        "JÉSSICA": "Jéssica Moya",
        "ANDRÉ": "André Martins",
        "FELIPE": "Felipe Queiroz",
    }

    async with _httpx.AsyncClient(timeout=60) as client:
        # Encontra o campo Gestor ▾
        resp = await client.get(f"{BASE}/leads/custom_fields", headers=H, params={"limit": 250})
        all_fields = resp.json().get("_embedded", {}).get("custom_fields", [])
        gestor_field = next((f for f in all_fields if f["name"] == "Gestor ▾"), None)
        if not gestor_field:
            return {"error": "Campo Gestor ▾ não encontrado"}

        gestor_id = gestor_field["id"]
        current_enums = gestor_field.get("enums") or []

        # Monta os enums atualizados mantendo os IDs
        new_enums = []
        renamed = []
        for e in current_enums:
            old_value = e["value"]
            new_value = RENAME.get(old_value.upper(), old_value)
            new_enums.append({"id": e["id"], "value": new_value, "sort": e.get("sort", 0)})
            if new_value != old_value:
                renamed.append(f"{old_value} → {new_value}")

        # Atualiza o campo
        r = await client.patch(
            f"{BASE}/leads/custom_fields/{gestor_id}",
            headers=H,
            json={"enums": new_enums},
        )
        if r.status_code not in (200, 201):
            return {"error": f"HTTP {r.status_code} - {r.text[:200]}"}

    return {"renamed": renamed, "field_id": gestor_id}

@router.post("/create-status-obs-fields", summary="Cria campos Status (select) e Observações (textarea) na Kommo")
async def create_status_obs_fields(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    STATUS_OPTIONS = [
        "🔴 Lead Captado",
        "🔴 Adicionado no Corpo Clínico",
        "🔵 Cadastro Enviado para Mirae",
        "🔵 Cadastro Mirae Aprovado",
        "🔵 Cadastro Mirae Negado",
        "🔵 Cadastro do Cliente Enviado",
        "🔵 Cadastro do Cliente Incompleto",
        "🔵 Cadastro do Cliente Completo",
        "🟢 Agenda Solicitada",
        "🟢 Agenda Negada",
        "🟢 Agenda Confirmada",
        "🟠 Desistência",
    ]

    async with _httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{BASE}/leads/custom_fields", headers=H, params={"limit": 250})
        all_fields = resp.json().get("_embedded", {}).get("custom_fields", [])
        existing = {f["name"]: f["id"] for f in all_fields}

        created = []
        result_ids = {}

        # Status (select)
        if "Status" in existing:
            result_ids["Status"] = existing["Status"]
            created.append(f"Status JÁ EXISTE (id:{existing['Status']})")
        else:
            enums = [{"value": opt, "sort": i} for i, opt in enumerate(STATUS_OPTIONS)]
            r = await client.post(f"{BASE}/leads/custom_fields", headers=H,
                                  json=[{"name": "Status", "type": "select", "enums": enums}])
            if r.status_code in (200, 201):
                fid = r.json().get("_embedded", {}).get("custom_fields", [{}])[0].get("id")
                result_ids["Status"] = fid
                created.append(f"Status CRIADO (id:{fid})")
            else:
                created.append(f"Status ERRO: HTTP {r.status_code} - {r.text[:100]}")

        # Observações (textarea)
        if "Observações" in existing:
            result_ids["Observações"] = existing["Observações"]
            created.append(f"Observações JÁ EXISTE (id:{existing['Observações']})")
        else:
            r = await client.post(f"{BASE}/leads/custom_fields", headers=H,
                                  json=[{"name": "Observações", "type": "textarea"}])
            if r.status_code in (200, 201):
                fid = r.json().get("_embedded", {}).get("custom_fields", [{}])[0].get("id")
                result_ids["Observações"] = fid
                created.append(f"Observações CRIADO (id:{fid})")
            else:
                created.append(f"Observações ERRO: HTTP {r.status_code} - {r.text[:100]}")

    return {"created": created, "field_ids": result_ids}

@router.post("/fix-status-options", summary="Corrige/recria as opções do campo Status")
async def fix_status_options(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    STATUS_ID = 4331833
    STATUS_OPTIONS = [
        "🔴 Lead Captado",
        "🔴 Adicionado no Corpo Clínico",
        "🔵 Cadastro Enviado para Mirae",
        "🔵 Cadastro Mirae Aprovado",
        "🔵 Cadastro Mirae Negado",
        "🔵 Cadastro do Cliente Enviado",
        "🔵 Cadastro do Cliente Incompleto",
        "🔵 Cadastro do Cliente Completo",
        "🟢 Agenda Solicitada",
        "🟢 Agenda Negada",
        "🟢 Agenda Confirmada",
        "🟠 Desistência",
    ]

    async with _httpx.AsyncClient(timeout=60) as client:
        enums = [{"value": opt, "sort": i} for i, opt in enumerate(STATUS_OPTIONS)]
        r = await client.patch(
            f"{BASE}/leads/custom_fields/{STATUS_ID}",
            headers=H,
            json={"enums": enums},
        )
        result_status = r.status_code
        result_text = r.text[:300]

        # Confirma lendo de volta
        resp = await client.get(f"{BASE}/leads/custom_fields/{STATUS_ID}", headers=H)
        field = resp.json()
        current_enums = [e["value"] for e in (field.get("enums") or [])]

    return {
        "patch_status": result_status,
        "patch_response": result_text,
        "enums_atuais": current_enums,
    }

@router.post("/recreate-status-clean", summary="Recria o campo Status sem emoji para testar")
async def recreate_status_clean(db: AsyncSession = Depends(get_db)):
    from app.services.kommo import get_valid_token
    import httpx as _httpx

    access_token = await get_valid_token(db)
    H = {"Authorization": f"Bearer {access_token}"}

    OLD_STATUS_ID = 4331833
    STATUS_OPTIONS = [
        "Lead Captado",
        "Adicionado no Corpo Clínico",
        "Cadastro Enviado para Mirae",
        "Cadastro Mirae Aprovado",
        "Cadastro Mirae Negado",
        "Cadastro do Cliente Enviado",
        "Cadastro do Cliente Incompleto",
        "Cadastro do Cliente Completo",
        "Agenda Solicitada",
        "Agenda Negada",
        "Agenda Confirmada",
        "Desistência",
    ]

    async with _httpx.AsyncClient(timeout=60) as client:
        # Apaga o campo antigo
        await client.delete(f"{BASE}/leads/custom_fields/{OLD_STATUS_ID}", headers=H)

        # Cria novo campo com enums no formato correto (sem id, só value e sort)
        enums = [{"value": opt, "sort": i + 1} for i, opt in enumerate(STATUS_OPTIONS)]
        r = await client.post(
            f"{BASE}/leads/custom_fields",
            headers=H,
            json=[{"name": "Status", "type": "select", "enums": enums}],
        )
        status_code = r.status_code
        body = r.json() if r.status_code in (200, 201) else r.text

        new_id = None
        confirmed = []
        if r.status_code in (200, 201):
            new_field = r.json().get("_embedded", {}).get("custom_fields", [{}])[0]
            new_id = new_field.get("id")
            # Lê de volta
            resp = await client.get(f"{BASE}/leads/custom_fields/{new_id}", headers=H)
            field = resp.json()
            raw_enums = field.get("enums")
            if isinstance(raw_enums, dict):
                confirmed = [v["value"] for v in raw_enums.values()]
            elif isinstance(raw_enums, list):
                confirmed = [e["value"] for e in raw_enums]

    return {"new_status_id": new_id, "http": status_code, "enums_confirmados": confirmed}

