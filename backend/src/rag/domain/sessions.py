"""Sessões efêmeras (SPEC §10.3, T14/T15).

A fase 1 implementa o contrato mínimo de sessão: identidade e timestamps
(`POST/GET/DELETE /sessions`). O histórico de contexto e a reescrita de
follow-up para pergunta autônoma (AC-13) são T15, que alimentará
`session_entries` (tabela já existente desde T03).

Nenhum tipo de ORM, FastAPI ou SDK de modelo atravessa esta fronteira
(exercitado em `tests/unit/test_domain_purity.py`).
"""

from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from rag.domain.versions import utcnow


class Session(BaseModel):
    """Sessão efêmera de consulta. Sem memória persistente (SPEC §3.2)."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)
    last_activity_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _timestamps_coherent(self) -> Self:
        if self.last_activity_at < self.created_at:
            raise ValueError("last_activity_at não pode preceder created_at")
        return self
