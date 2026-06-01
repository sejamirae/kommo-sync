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
