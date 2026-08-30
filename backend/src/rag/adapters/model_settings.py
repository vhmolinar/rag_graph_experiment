"""Configuração compartilhada dos adapters HTTP de modelos (SPEC §11, T07).

Três mixins reaproveitados por embedding, geração e reranking:

- `ModelAuthSettings`: chave por variável de ambiente OU secret file
  (convenção `_FILE`, comum em Docker/Kubernetes secrets) — nunca ambos.
- `ResilienceSettings`: limites de retry, circuit breaker e concorrência,
  todos validados na construção (T7-05). Nenhuma dependência nova
  (NOTES.md §10.1): implementação própria sobre asyncio, no mesmo espírito
  do rate limiting de token bucket (NOTES.md §10.2 item 6).
- `HttpEndpointSettings`: validações comuns de endpoint HTTP (T7-02/T7-05) —
  URL http(s) válida e recusa de credencial sobre `http://` (a chave
  trafegaria em texto claro). Este mixin herda `ModelAuthSettings`, e a
  ordem de MRO garante que a chave de `api_key_file` já esteja resolvida
  quando a regra de https é avaliada (verificado pelos testes).

Nenhuma mensagem de erro menciona o valor do segredo, a URL configurada ou o
caminho do secret file (AC-16, T7-02/T7-05).
"""

from pathlib import Path
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelAuthSettings(BaseSettings):
    """Autenticação por ambiente ou secret file (SPEC §11).

    Mensagens de erro não incluem o caminho do secret file (AC-16/T7-05);
    `hide_input_in_errors` impede que o `str()` do `ValidationError` ecoe o
    input de validação (que conteria o `Path` do secret file).
    """

    model_config = SettingsConfigDict(hide_input_in_errors=True)

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
                "não foi possível ler o secret file de api_key"
            ) from exc
        if not content:
            raise ValueError("secret file de api_key está vazio")
        self.api_key = SecretStr(content)
        return self


class ResilienceSettings(BaseSettings):
    """Retries transitórios, circuit breaker e limite de concorrência.

    Valores inválidos (negativos, nulos, multiplicador < 1 etc.) são
    rejeitados na construção com `ValidationError` previsível (T7-05), em vez
    de falhar depois com erro genérico de `httpx`/`asyncio`.
    """

    max_retries: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=0.2, gt=0)
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0)
    max_concurrency: int = Field(default=8, gt=0)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_seconds: float = Field(default=30.0, gt=0)


class HttpEndpointSettings(ModelAuthSettings, ResilienceSettings):
    """Base dos settings de endpoints HTTP de modelo (SPEC §11).

    Valida a URL e impede o envio de `Authorization: Bearer` por `http://`
    (credencial em texto claro). Subclasses definem o default de `base_url`.
    """

    base_url: str

    @field_validator("base_url")
    @classmethod
    def _valid_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url deve ser uma URL http(s) válida")
        return value

    @model_validator(mode="after")
    def _https_required_with_credential(self) -> Self:
        # T6-04, aplicado aqui a todos os três endpoints (T7-02): nunca enviar
        # Authorization: Bearer para um endpoint http:// — a credencial
        # trafegaria em texto claro numa rede não confiável.
        if self.api_key.get_secret_value() and not self.base_url.lower().startswith("https://"):
            raise ValueError("base_url deve usar https:// quando api_key está definido")
        return self
