"""Execução de indexação versionada (T06; correção T6-01/T6-08 do REVIEW_T06.md).

Uma edição pode ter várias `IndexRun`s ao longo do tempo (parâmetros de
chunking diferentes, novo modelo de embedding, `--force`); as passagens de
execuções antigas nunca são apagadas — permanecem associadas à sua
`IndexRun` original para que um `AnswerRun` histórico continue reproduzível
(SPEC §6). No máximo uma `IndexRun` por edição é `is_active` — é ela que
define o conjunto de passagens usado pela recuperação, nunca inferido pela
mera existência de qualquer passagem.
"""

from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from rag.domain.versions import utcnow


class IndexRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    edition_id: UUID
    extraction_version_id: UUID
    chunking_version_id: UUID
    embedding_version_id: UUID
    model_endpoint_version_id: UUID
    is_active: bool = True
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @property
    def identity(self) -> tuple[UUID, UUID, UUID, UUID]:
        """Chave de reprodutibilidade (SPEC §6/AC-15): mesma edição + mesmas
        quatro versões implica mesmo conjunto de passagens produzido."""
        return (
            self.extraction_version_id,
            self.chunking_version_id,
            self.embedding_version_id,
            self.model_endpoint_version_id,
        )
