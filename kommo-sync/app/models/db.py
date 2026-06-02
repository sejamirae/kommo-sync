# app/models/db.py
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text,
    DateTime, Boolean, ForeignKey, func
)
from app.database import Base


class KommoToken(Base):
    """Armazena access/refresh token OAuth da Kommo."""
    __tablename__ = "kommo_tokens"

    id             = Column(Integer, primary_key=True)
    domain         = Column(String(255), unique=True, nullable=False)
    access_token   = Column(Text, nullable=False)
    refresh_token  = Column(Text, nullable=False)
    expires_at     = Column(DateTime(timezone=True), nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())


class Lead(Base):
    """Espelho local dos leads da Kommo."""
    __tablename__ = "leads"

    id             = Column(Integer, primary_key=True)           # ID do lead na Kommo
    name           = Column(String(500))
    status_id      = Column(BigInteger)                          # ID da etapa atual
    pipeline_id    = Column(BigInteger)
    responsible_id = Column(BigInteger)                          # ID do usuário responsável
    price          = Column(BigInteger, default=0)
    created_at_kommo = Column(DateTime(timezone=True))
    updated_at_kommo = Column(DateTime(timezone=True))
    synced_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Contact(Base):
    """Espelho local dos contatos da Kommo."""
    __tablename__ = "contacts"

    id             = Column(Integer, primary_key=True)
    name           = Column(String(500))
    first_name     = Column(String(255))
    last_name      = Column(String(255))
    responsible_id = Column(BigInteger)
    created_at_kommo = Column(DateTime(timezone=True))
    updated_at_kommo = Column(DateTime(timezone=True))
    synced_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ContactPhone(Base):
    __tablename__ = "contact_phones"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"))
    value      = Column(String(100))
    enum_code  = Column(String(50))   # WORK, MOBILE, etc.


class ContactEmail(Base):
    __tablename__ = "contact_emails"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"))
    value      = Column(String(255))
    enum_code  = Column(String(50))


class SyncLog(Base):
    """Registro de cada evento recebido via webhook ou sincronização."""
    __tablename__ = "sync_log"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    event_type  = Column(String(100))   # webhook:lead_status, api:lead_update, etc.
    entity_type = Column(String(50))    # lead | contact
    entity_id   = Column(BigInteger)
    payload     = Column(Text)          # JSON bruto do evento
    status      = Column(String(20), default="ok")   # ok | error
    message     = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class ExpansionField(Base):
    """Campos extras do pipeline Expansão — salvos no banco, compartilhados pela equipe."""
    __tablename__ = "expansion_fields"

    lead_id             = Column(BigInteger, primary_key=True)
    nome_completo       = Column(String(500))
    crm                 = Column(String(100))
    telefone            = Column(String(100))
    cliente             = Column(String(255))
    especialidade       = Column(String(255))
    unidade             = Column(String(255))
    dia_semana          = Column(String(100))
    frequencia          = Column(String(100))
    horario             = Column(String(100))
    horas               = Column(String(50))
    data_envio          = Column(String(20))
    data_fechamento     = Column(String(20))
    previsao_inicio     = Column(String(20))
    unidade_pagamento   = Column(String(100))
    valor_mirae         = Column(String(50))
    valor_medico        = Column(String(50))
    onboarding          = Column(String(20))
    origem              = Column(String(255))
    gestor              = Column(String(255))
    doctorid            = Column(String(50))
    pendencias          = Column(Text)
    status_lead         = Column(String(255))
    observacoes         = Column(Text)
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ExpansionNote(Base):
    """Histórico de interações de cada lead do pipeline Expansão."""
    __tablename__ = "expansion_notes"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    lead_id    = Column(BigInteger, nullable=False, index=True)
    type       = Column(String(50))   # nota, email, whatsapp, ligacao, reuniao
    text       = Column(Text, nullable=False)
    author     = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
