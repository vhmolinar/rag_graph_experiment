"""Serviço de enriquecimento hierárquico (T11; SPEC §7.4, AC-12, AC-15).

Trás a indexação das passagens (T06), gera:
1. sínteses de seção (toda seção com passagens diretas);
2. sínteses de capítulo (seção de topo, level=0) SÓ a partir das passagens
   descendentes ("resumos/trechos filhos", SPEC §7.4);
3. síntese da edição;
4. conceitos, aliases e evidências, cada item ligado às passagens de suporte.

Regra central (SPEC §7.4): se o suporte não puder ser identificado, o item
abstrato não é publicado no índice. Falhas do provedor ou suportes fora do
escopo falham fechados — nunca se publica síntese sem evidências (AC-12).

Versionamento (AC-15): cada execução registra `PromptVersion` (hash do
template) e `ModelEndpointVersion` por papel; reexecução com a MESMA versão é
idempotente (no-op); com versão NOVA cria novos registros SEM sobrescrever
histórico (NOTES.md §10.12). Toda a persistência corre numa única transação —
falha rollbacka tudo, nunca publica estado parcial.
"""

from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

from rag.domain.enums import ConceptState, SummaryScope
from rag.domain.errors import IngestionError, ModelResponseError, NotFoundError
from rag.domain.identifiers import sha256_of_text
from rag.domain.knowledge import Concept, EnrichmentRun, Summary, descendant_section_ids
from rag.domain.library import Edition, Section, Work
from rag.domain.providers import (
    ConceptExtractRequest,
    EnrichmentProvider,
    PassageRef,
    SummaryRequest,
)
from rag.domain.versions import ModelEndpointVersion, PromptVersion, utcnow
from rag.infrastructure.repositories.content import SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.enrichment import (
    ConceptsRepository,
    EnrichmentRunsRepository,
    SummariesRepository,
)
from rag.infrastructure.repositories.index_runs import IndexRunsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_SUMMARY_POLICY = (
    "És um provedor de sínteses hierárquicas de um RAG de livros em português. "
    "Sintetiza EXCLUSIVAMENTE a partir dos trechos fornecidos. Nunca invente "
    "informação ausente dos trechos. Responde apenas em JSON válido."
)

_SUMMARY_OUTPUT_CONTRACT = (
    'Devolva um objeto JSON com "text" (síntese em português, concisa e fiel '
    'aos trechos) e "supporting_passage_ids" (lista de UUIDs de as passagens '
    "que sustentam a síntese; use os IDs EXACTAMENTE como informados; vazio "
    "se nenhuma passagem sustentar a síntese)."
)

_CONCEPT_POLICY = (
    "És um provedor de extração de conceitos de um RAG de livros em português. "
    "Extrai conceitos e aliases EXCLUSIVAMENTE dos trechos fornecidos. Nunca "
    "invente conceitos ausentes dos trechos. Responde apenas em JSON válido."
)

_CONCEPT_OUTPUT_CONTRACT = (
    'Devolva um objeto JSON com "concepts": lista de objetos com '
    '"normalized_label" (rótulo normalizado, minúsculas), "description" '
    '(descrição curta em português), "aliases" (lista de expressões '
    'alternativas) e "supporting_passage_ids" (lista de UUIDs de as passagens '
    "que sustentam o conceito; vazio se nenhuma passagem sustentar)."
)


class EnrichmentReport(BaseModel):
    """Resultado do enriquecimento. Nunca contém texto do livro."""

    model_config = ConfigDict(frozen=True)

    edition_id: str
    created: bool
    summaries_section: int
    summaries_chapter: int
    summaries_edition: int
    concepts: int
    concept_aliases: int
    concept_evidences: int
    summarizer_version_id: str
    extractor_version_id: str
    warnings: tuple[str, ...] = Field(default_factory=tuple)


class EnrichmentService:
    def __init__(self, provider: EnrichmentProvider) -> None:
        self._provider = provider

    async def enrich(
        self,
        conn: AsyncConnection,
        *,
        edition_id: UUID,
        model_name: str,
    ) -> EnrichmentReport:
        editions = EditionsRepository(conn)
        edition = await editions.get(edition_id)
        if edition is None:
            raise NotFoundError("Edição não encontrada.", context={"edition_id": str(edition_id)})
        work = await WorksRepository(conn).get(edition.work_id)
        if work is None:  # pragma: no cover - integridade referencial garante isto
            raise NotFoundError("Obra da edição não encontrada.")

        sections = await SectionsRepository(conn).list_by_edition(edition.id)
        # T11-02: o enriquecimento opera sobre a EXECUÇÃO ATIVA de indexação —
        # nunca sobre passagens de execuções inativas (histórico). Recuperação e
        # enriquecimento usam o mesmo conjunto corrente (T6-01), para que sínteses
        # e conceitos representem o índice vigente.
        active_run = await IndexRunsRepository(conn).get_active(edition.id)
        if active_run is None:
            raise IngestionError(
                "Edição sem passagens indexadas; rode `rag index` primeiro.",
                context={"edition_id": str(edition.id)},
            )
        passages = await PassagesRepository(conn).list_by_index_run(active_run.id)
        child_passages = [p for p in passages if p.embedding_version_id is not None]
        if not child_passages:
            raise IngestionError(
                "Edição sem passagens indexadas; rode `rag index` primeiro.",
                context={"edition_id": str(edition.id)},
            )

        (
            summary_prompt_version,
            concept_prompt_version,
            summarizer_version,
            extractor_version,
        ) = await self._register_versions(conn, model_name)
        summaries_repo = SummariesRepository(conn)
        concepts_repo = ConceptsRepository(conn)
        # Idempotência por (edição, execução de indexação, versão de síntese):
        # a identidade de uma execução é o conjunto de passagens efetivamente
        # enviado ao provedor (`index_run_id`, R2-T11-01) + versão de síntese,
        # registrada como `EnrichmentRun` na MESMA transação dos itens —
        # inclusive quando NENHUM item é publicado (T11-03). "Tem execução
        # desta identidade" implica a execução concluíu; conceitos podem
        # legitimamente ser zero em conteúdo. Reindexar (nova execução ativa)
        # com o mesmo modelo NÃO é no-op: a identidade mudou.
        runs_repo = EnrichmentRunsRepository(conn)
        already = await runs_repo.get_for_edition_run_version(
            edition.id, active_run.id, summarizer_version.id
        )
        if already is not None:
            return await self._existing_report(
                conn, edition.id, summarizer_version.id, extractor_version.id
            )

        section_by_id = {s.id: s for s in sections}
        refs_by_section: dict[UUID, list[PassageRef]] = {}
        all_refs: list[PassageRef] = []
        for passage in child_passages:
            section = section_by_id.get(passage.section_id) if passage.section_id else None
            ref = PassageRef(
                passage_id=passage.id,
                text=passage.text,
                section_path=tuple(section.path) if section is not None else (),
            )
            all_refs.append(ref)
            if passage.section_id is not None:
                refs_by_section.setdefault(passage.section_id, []).append(ref)
        all_ids = {ref.passage_id for ref in all_refs}

        warnings: list[str] = []
        pending_summaries: list[Summary] = []
        summaries_section = 0
        summaries_chapter = 0
        summaries_edition = 0

        # 1. Sínteses de seção: toda seção com passagens diretas.
        for section in sections:
            refs = refs_by_section.get(section.id, ())
            if not refs:
                continue
            result = await self._provider.summarize(
                SummaryRequest(
                    system_policy=_SUMMARY_POLICY,
                    output_contract=_SUMMARY_OUTPUT_CONTRACT,
                    scope_type=SummaryScope.SECTION,
                    scope_description=self._scope_description(
                        SummaryScope.SECTION, section, work, edition
                    ),
                    passages=tuple(refs),
                    prompt_version_id=summary_prompt_version.id,
                )
            )
            supports = self._validated_supports(
                result.supporting_passage_ids,
                allowed={ref.passage_id for ref in refs},
                item_label=f"seção {self._section_label(section)}",
                warnings=warnings,
            )
            if supports is None:
                continue
            pending_summaries.append(
                Summary(
                    edition_id=edition.id,
                    scope_type=SummaryScope.SECTION,
                    section_id=section.id,
                    text=result.text,
                    generator_version_id=summarizer_version.id,
                    supporting_passage_ids=supports,
                )
            )
            summaries_section += 1

        # 2. Sínteses de capítulo: seção de topo (level=0) com passagens descendentes.
        for chapter in (s for s in sections if s.level == 0):
            descendant_ids = descendant_section_ids(sections, chapter.id)
            chapter_refs: list[PassageRef] = []
            for section_id in descendant_ids:
                chapter_refs.extend(refs_by_section.get(section_id, ()))
            if not chapter_refs:
                continue
            result = await self._provider.summarize(
                SummaryRequest(
                    system_policy=_SUMMARY_POLICY,
                    output_contract=_SUMMARY_OUTPUT_CONTRACT,
                    scope_type=SummaryScope.CHAPTER,
                    scope_description=self._scope_description(
                        SummaryScope.CHAPTER, chapter, work, edition
                    ),
                    passages=tuple(chapter_refs),
                    prompt_version_id=summary_prompt_version.id,
                )
            )
            supports = self._validated_supports(
                result.supporting_passage_ids,
                allowed={ref.passage_id for ref in chapter_refs},
                item_label=f"capítulo {self._section_label(chapter)}",
                warnings=warnings,
            )
            if supports is None:
                continue
            pending_summaries.append(
                Summary(
                    edition_id=edition.id,
                    scope_type=SummaryScope.CHAPTER,
                    section_id=chapter.id,
                    text=result.text,
                    generator_version_id=summarizer_version.id,
                    supporting_passage_ids=supports,
                )
            )
            summaries_chapter += 1

        # 3. Síntese da edição.
        if all_refs:
            result = await self._provider.summarize(
                SummaryRequest(
                    system_policy=_SUMMARY_POLICY,
                    output_contract=_SUMMARY_OUTPUT_CONTRACT,
                    scope_type=SummaryScope.EDITION,
                    scope_description=self._scope_description(
                        SummaryScope.EDITION, None, work, edition
                    ),
                    passages=tuple(all_refs),
                    prompt_version_id=summary_prompt_version.id,
                )
            )
            supports = self._validated_supports(
                result.supporting_passage_ids,
                allowed=all_ids,
                item_label="edição",
                warnings=warnings,
            )
            if supports is not None:
                pending_summaries.append(
                    Summary(
                        edition_id=edition.id,
                        scope_type=SummaryScope.EDITION,
                        text=result.text,
                        generator_version_id=summarizer_version.id,
                        supporting_passage_ids=supports,
                    )
                )
                summaries_edition += 1

        # 4. Conceitos, aliases e evidências.
        pending_concepts: list[tuple[str, str, tuple[str, ...], tuple[UUID, ...]]] = []
        concepts_result = await self._provider.extract_concepts(
            ConceptExtractRequest(
                system_policy=_CONCEPT_POLICY,
                output_contract=_CONCEPT_OUTPUT_CONTRACT,
                scope_description=self._scope_description(
                    SummaryScope.EDITION, None, work, edition
                ),
                passages=tuple(all_refs),
                prompt_version_id=concept_prompt_version.id,
            )
        )
        for extracted in concepts_result.concepts:
            supports = self._validated_supports(
                extracted.supporting_passage_ids,
                allowed=all_ids,
                item_label=f"conceito {extracted.normalized_label}",
                warnings=warnings,
            )
            if supports is None:
                continue
            pending_concepts.append(
                (
                    extracted.normalized_label,
                    extracted.description,
                    tuple(extracted.aliases),
                    supports,
                )
            )

        # 5. Persistência numa única transação (model calls concluíram acima):
        # itens e a execução de enriquecimento (T11-03) vão juntos — falha
        # rollbacka tudo, inclusive o registro da execução; reexecutar a MESMA
        # versão repete nada.
        concepts_created = 0
        aliases_created = 0
        evidences_created = 0
        async with conn.transaction():
            if not await runs_repo.create_if_absent(
                EnrichmentRun(
                    edition_id=edition.id,
                    index_run_id=active_run.id,
                    summarizer_version_id=summarizer_version.id,
                    extractor_version_id=extractor_version.id,
                )
            ):
                # Corrida de concorrência: outra execução da mesma versão já foi
                # registrada — nada novo a publicar (comportamento idempotente).
                return await self._existing_report(
                    conn, edition.id, summarizer_version.id, extractor_version.id
                )
            for summary in pending_summaries:
                await summaries_repo.create(summary)
            for label, description, aliases, supports in pending_concepts:
                concept = await concepts_repo.get_or_create(
                    Concept(
                        normalized_label=label, description=description, state=ConceptState.PROPOSED
                    )
                )
                concepts_created += 1
                for alias in aliases:
                    await concepts_repo.add_alias(concept.id, alias, confidence=1.0)
                    aliases_created += 1
                for passage_id in supports:
                    await concepts_repo.add_evidence(
                        concept.id,
                        passage_id,
                        confidence=1.0,
                        extractor_version_id=extractor_version.id,
                    )
                    evidences_created += 1

        return EnrichmentReport(
            edition_id=str(edition.id),
            created=True,
            summaries_section=summaries_section,
            summaries_chapter=summaries_chapter,
            summaries_edition=summaries_edition,
            concepts=concepts_created,
            concept_aliases=aliases_created,
            concept_evidences=evidences_created,
            summarizer_version_id=str(summarizer_version.id),
            extractor_version_id=str(extractor_version.id),
            warnings=tuple(warnings),
        )

    @staticmethod
    async def _register_versions(
        conn: AsyncConnection, model_name: str
    ) -> tuple[PromptVersion, PromptVersion, ModelEndpointVersion, ModelEndpointVersion]:
        versions = VersionsRepository(conn)
        summary_prompt_version = await versions.get_or_create(
            PromptVersion(
                label="summary-prompt",
                template_sha256=sha256_of_text(_SUMMARY_POLICY + "\n" + _SUMMARY_OUTPUT_CONTRACT),
                params={"scopes": ["section", "chapter", "edition"]},
                created_at=utcnow(),
            )
        )
        concept_prompt_version = await versions.get_or_create(
            PromptVersion(
                label="concept-extract-prompt",
                template_sha256=sha256_of_text(_CONCEPT_POLICY + "\n" + _CONCEPT_OUTPUT_CONTRACT),
                params={"max_concepts": 500},
                created_at=utcnow(),
            )
        )
        summarizer_version = await versions.get_or_create(
            ModelEndpointVersion(
                label="summarizer",
                endpoint_kind="generator",
                provider="openai-compatible",
                model_name=model_name,
                params={"role": "summary", "prompt_version_id": str(summary_prompt_version.id)},
                created_at=utcnow(),
            )
        )
        extractor_version = await versions.get_or_create(
            ModelEndpointVersion(
                label="concept-extractor",
                endpoint_kind="generator",
                provider="openai-compatible",
                model_name=model_name,
                params={"role": "concept", "prompt_version_id": str(concept_prompt_version.id)},
                created_at=utcnow(),
            )
        )
        return summary_prompt_version, concept_prompt_version, summarizer_version, extractor_version

    @staticmethod
    def _validated_supports(
        supporting_ids: tuple[UUID, ...],
        *,
        allowed: set[UUID],
        item_label: str,
        warnings: list[str],
    ) -> tuple[UUID, ...] | None:
        """Suportes a publicar, ou None se o item for rejeitado (sem suporte).

        Falha fechada se o provedor devolver suporte fora do escopo — nunca se
        publica síntese/conceito com evidência de outra região (SPEC §7.4,
        AC-12). Deduplica preservando a ordem informada.
        """
        if not supporting_ids:
            warnings.append(f"{item_label}: sem passagens de suporte — item não publicado.")
            return None
        unknown = set(supporting_ids) - allowed
        if unknown:
            raise ModelResponseError(
                "Provedor de enriquecimento devolveu suporte fora do escopo.",
                context={
                    "item": item_label,
                    "fora_do_escopo": sorted(str(u) for u in unknown),
                },
            )
        seen: set[UUID] = set()
        ordered: list[UUID] = []
        for passage_id in supporting_ids:
            if passage_id not in seen:
                seen.add(passage_id)
                ordered.append(passage_id)
        return tuple(ordered)

    @staticmethod
    def _scope_description(
        scope: SummaryScope, section: Section | None, work: Work, edition: Edition
    ) -> str:
        base = f"Edição {edition.title} (obra {work.canonical_title})"
        if scope is SummaryScope.EDITION or section is None:
            return base
        path = " > ".join(section.path)
        return f"{base} — {scope.value} '{path}'"

    @staticmethod
    def _section_label(section: Section) -> str:
        return section.path[-1] if section.path else str(section.id)

    @staticmethod
    async def _existing_report(
        conn: AsyncConnection,
        edition_id: UUID,
        summarizer_version_id: UUID,
        extractor_version_id: UUID,
    ) -> EnrichmentReport:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
        section = sum(1 for s in summaries if s.scope_type is SummaryScope.SECTION)
        chapter = sum(1 for s in summaries if s.scope_type is SummaryScope.CHAPTER)
        edition = sum(1 for s in summaries if s.scope_type is SummaryScope.EDITION)
        concepts = await ConceptsRepository(conn).count_for_edition(edition_id)
        return EnrichmentReport(
            edition_id=str(edition_id),
            created=False,
            summaries_section=section,
            summaries_chapter=chapter,
            summaries_edition=edition,
            concepts=concepts,
            concept_aliases=0,
            concept_evidences=0,
            summarizer_version_id=str(summarizer_version_id),
            extractor_version_id=str(extractor_version_id),
        )
