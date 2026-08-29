"""Configuração compartilhada dos adapters HTTP de modelos (SPEC §11, T07).

Dois mixins reaproveitados por embedding, geração e reranking:

- `ModelAuthSettings`: chave por variável de ambiente OU secret file
  (convenção `_FILE`, comum em Docker/Kubernetes secrets) — nunca ambos.
- `ResilienceSettings`: limites de retry, circuit breaker e concorrência.
  Nenhuma dependência nova (NOTES.md §10.1): implementação própria sobre
  asyncio, no mesmo espírito do rate limiting de token bucket
  (NOTES.md §10.2 item 6).
"""

from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings


class ModelAuthSettings(BaseSettings):
    """Autenticação por ambiente ou secret file (SPEC §11)."""

    api_key: SecretStr = SecretStr("")
    api_key_file: Path | None = None

    @model_validator(mode="after")
    def _resolve_api_key(self) -> Self:
        if self.api_key_file is None:
            return self
        if self.api_key.get_secret_value():
            raise ValueError(
                "defina api_key OU api_key_file para o endpoint de modelo, nunca ambos."
            )
        try:
            content = self.api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                f"não foi possível ler o secret file de api_key: {self.api_key_file}"
            ) from exc
        if not content:
            raise ValueError(f"secret file de api_key está vazio: {self.api_key_file}")
        self.api_key = SecretStr(content)
        return self


class ResilienceSettings(BaseSettings):
    """Retries transitórios, circuit breaker e limite de concorrência."""

    max_retries: int = 2
    retry_backoff_seconds: float = 0.2
    retry_backoff_multiplier: float = 2.0
    max_concurrency: int = 8
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_reset_seconds: float = 30.0
