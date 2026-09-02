"""Resumos hierárquicos e conceitos (SPEC §5.1, §7.4).

Resumos e conceitos ajudam a localizar regiões, mas NUNCA são evidência primária.
Todo item abstrato sem passagem de suporte é rejeitado.
"""

from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import ConceptState, SummaryScope
from rag.domain.library import Section
from rag.domain.versions import utcnow


def descendant_section_ids(sections: list[Section], root_id: UUID) -> frozenset[UUID]:
    """IDs de `root_id` e todas as suas seções descendentes (função pura).

    Usada pelo serviço de enriquecimento (T11) para calcular o escopo de
    suporte de um resumo de capítulo: as passagens das seções descendentes
    ("resumos/trechos filhos", SPEC §7.4). O invariante de aciclicidade é
    garantido pelo banco (FK auto-referencial de `sections`).
    """
    by_parent: dict[UUID, list[UUID]] = {}
    for section in sections:
        if section.parent_section_id is not None:
            by_parent.setdefault(section.parent_section_id, []).append(section.id)
    result: set[UUID] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(by_parent.get(current, ()))
    return frozenset(result)


class Summary(BaseModel):
    """Síntese vinculada a uma edição (R01/RR01).

    Escopo referencialmente íntegro: 'section' e 'chapter' exigem `section_id`;
    'edition' refere-se à própria edição e não admite `section_id`. O banco
    impõe FK composta (section_id, edition_id), garantindo que a seção
    pertença à edição da síntese, e um trigger impõe que 'chapter' referencie
    uma Section de topo (level = 0) — o domínio não carrega a seção, então a
    regra estrutural vive no banco (R4-03).

    Frozen com suportes imutáveis (RRR01): uma síntese publicada não pode ter
    suas evidências removidas ou alteradas.
    """

    model_config = ConfigDict(frozen=True)

    edition_id: UUID
    scope_type: SummaryScope
    section_id: UUID | None = None
    text: str = Field(min_length=1)
    generator_version_id: UUID
    supporting_passage_ids: tuple[UUID, ...] = Field(min_length=1)
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _scope_coherent(self) -> Self:
        if self.scope_type is SummaryScope.EDITION:
            if self.section_id is not None:
                raise ValueError("scope 'edition' não referencia seção")
        elif self.section_id is None:
            raise ValueError("scopes 'section'/'chapter' exigem section_id")
        return self


class Concept(BaseModel):
    normalized_label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    state: ConceptState = ConceptState.PROPOSED
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    model_config = ConfigDict(str_strip_whitespace=True)


class ConceptAlias(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    concept_id: UUID
    expression: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)


class ConceptEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    concept_id: UUID
    passage_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    extractor_version_id: UUID


class EnrichmentRun(BaseModel):
    """Execução de enriquecimento concluída (T11, correção T11-03 e R2-T11-01).

    A identidade de uma execução é (edição, execução de indexação, versão de
    síntese): `index_run_id` referencia o conjunto de passagens efetivamente
    enviado ao provedor (R2-T11-01), `summarizer_version_id` a versão do
    modelo/prompt, e `extractor_version_id` a versão de extração da mesma
    execução. O registro existe MESMO quando nenhum item é publicado (todos os
    suportes rejeitados) — é ele, não a existência de sínteses, que torna
    idempotente a reexecução da mesma identidade (NOTES.md §10.12 item 5).
    Reindexar a edição (nova `IndexRun`) com o mesmo modelo NÃO é no-op: a
    identidade mudou, exige nova execução sobre o conjunto corrente.
    """

    model_config = ConfigDict(frozen=True)

    edition_id: UUID
    index_run_id: UUID
    summarizer_version_id: UUID
    extractor_version_id: UUID
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)
