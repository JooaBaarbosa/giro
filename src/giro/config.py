from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    service_token: str
    n8n_webhook_url: str = "http://n8n:5678/webhook"
    app_base_url: str = "http://localhost:8000"
    store_name: str = "Mercearia Demo"
    store_tz: str = "America/Sao_Paulo"
    owner_whatsapp: str = ""
    whatsapp_dry_run: bool = True
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
