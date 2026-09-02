"""Configuração da camada HTTP (T14; SPEC §10, §14).

`CORS_ALLOWED_ORIGINS` e `RATE_LIMIT_PER_MINUTE` já constam no `.env.example`
desde T01; esta classe os tipa e valida. Sem segredos aqui — chaves de modelos
vivem nas settings dos adapters (`EMBEDDING_*`, `RERANKER_*`, ...).
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Configuração de segurança e operação da API.

    Env vars sem prefixo (``CORS_ALLOWED_ORIGINS``, ``RATE_LIMIT_PER_MINUTE``)
    porque já eram convenção do `.env.example` antes desta tarefa.
    """

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    cors_allowed_origins: str = "http://localhost:5173"
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    def rate_refill_per_second(self) -> float:
        return self.rate_limit_per_minute / 60.0
