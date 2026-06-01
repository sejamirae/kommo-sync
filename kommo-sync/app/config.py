# app/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # PostgreSQL (Render fornece automaticamente)
    DATABASE_URL: str

    # Kommo OAuth
    KOMMO_CLIENT_ID: str
    KOMMO_CLIENT_SECRET: str
    KOMMO_REDIRECT_URI: str          # ex: https://seu-app.onrender.com/oauth/callback
    KOMMO_DOMAIN: str                # ex: suaconta.kommo.com

    # Segurança
    WEBHOOK_SECRET: str = ""         # string aleatória para validar webhooks

    class Config:
        env_file = ".env"

@lru_cache
def get_settings():
    return Settings()
