"""Configuração por ambiente. Segredos via SecretStr; nunca logados."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    db: str = "ragbooks"
    user: str = "ragbooks"
    password: SecretStr = SecretStr("")

    @property
    def dsn(self) -> str:
        """DSN para psycopg. Nunca incluir em logs."""
        return (
            f"host={self.host} port={self.port} dbname={self.db} "
            f"user={self.user} password={self.password.get_secret_value()}"
        )

    @property
    def sqlalchemy_url(self) -> str:
        """URL para o Alembic (ferramenta de migration; fora do código de aplicação)."""
        return (
            f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.db}"
        )


class StorageSettings(BaseSettings):
    """Armazenamento local de artefatos (SPEC §Artefatos; T04)."""

    model_config = SettingsConfigDict(env_prefix="ARTIFACT_", extra="ignore")

    root: Path = Path("./artifacts")
    max_size_bytes: int = 256 * 1024 * 1024  # 256 MiB por artefato
    temp_max_age_hours: int = 24


class ChunkingSettings(BaseSettings):
    """Parâmetros de chunking configuráveis por ambiente (T6-07,
    REVIEW_T06.md) — `rag index` não fica preso aos defaults de
    `ChunkingParams`; a CLI pode sobrepor individualmente cada valor."""

    model_config = SettingsConfigDict(env_prefix="CHUNKING_", extra="ignore")

    child_target_tokens: int = Field(default=400, gt=0)
    child_overlap_tokens: int = Field(default=60, ge=0)
    parent_target_tokens: int = Field(default=1600, gt=0)
