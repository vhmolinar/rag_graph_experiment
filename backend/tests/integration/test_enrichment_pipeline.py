"""Pipeline de enriquecimento hierárquico contra PostgreSQL real (T11).

Ingestão real (T05) + indexação real (T06) primeiro; depois
`EnrichmentService` com `FakeEnrichmentProvider` (determinístico, sem rede)
gera as sínteses de seção/capítulo/edição e os conceitos. Cobre as evidências
de T11:
- hierarquia completa persistida com suportes (AC-12);
- síntese sem suporte é REJEITADA (não publicada) — SPEC §7.4;
- suporte fora do escopo falha fechado;
- conceito leva às passagens originais (AC-12);
- a resposta final nunca cita síntese (AC-12);
- reexecução com nova versão não sobrescreve histórico (AC-15);
- reexecução com a mesma versão é idempotente.
"""

import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub
from model_doubles import FakeEnrichmentProvider, summary_without_support

from rag.adapters.docling_adapter import DoclingExtractor
from rag.application.enrichment import EnrichmentReport, EnrichmentService
from rag.application.index import IndexingService
from rag.application.ingest import IngestionService, load_metadata
from rag.domain.chunking import ChunkingParams
from rag.domain.enums import ConceptState, SummaryScope
from rag.domain.errors import IngestionError, ModelResponseError, NotFoundError
from rag.domain.providers import ExtractedConcepts, SummaryResult
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.enrichment import ConceptsRepository, SummariesRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

pytestmark = pytest.mark.integration


class _FakeEmbeddingProvider:
    def __init__(self, dimensions: int = EMBEDDING_COLUMN_DIMENSIONS) -> None:
        self.dimensions = dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(i + 1)] * self.dimensions for i in range(len(texts))]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dimensions


def _extractor_pdf_no_model() -> DocumentConverter:
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(backend=PyPdfiumDocumentBackend)}
    )


def _write_metadata(path: Path) -> Path:
    path.write_text(
        "title: Livro Fixture\nauthors: [Autor Fixture]\nedition_label: 1ª ed.\n",
        encoding="utf-8",
    )
    return path


async def _ingest_epub(db: Database, tmp_path: Path) -> UUID:
    epub = tmp_path / "livro.epub"
    epub.write_bytes(
        make_epub(
            [
                ("Capítulo I", ["Primeira frase do capitulo um.", "Segunda frase do capitulo um."]),
                ("Capítulo II", ["Primeira frase do capitulo dois."]),
            ]
        )
    )
    meta = load_metadata(_write_metadata(tmp_path / "livro.yaml"))
    service = IngestionService(ArtifactStore(tmp_path / "artifacts"), DoclingExtractor())
    async with db.connection() as conn:
        report = await service.ingest(conn, file_path=epub, metadata=meta)
    return UUID(report.edition_id)


async def _index(db: Database, tmp_path: Path, edition_id: UUID) -> None:
    service = IndexingService(
        ArtifactStore(tmp_path / "artifacts"),
        DoclingExtractor(converter=_extractor_pdf_no_model()),
        _FakeEmbeddingProvider(),
    )
    async with db.connection() as conn:
        await service.index_edition(
            conn,
            edition_id=edition_id,
            chunking_params=ChunkingParams(),
            embedding_model_name="fake-model",
            embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
        )


async def _enrich(
    db: Database,
    edition_id: UUID,
    provider: FakeEnrichmentProvider,
    model_name: str = "fake-model",
) -> EnrichmentReport:
    service = EnrichmentService(provider)
    async with db.connection() as conn:
        return await service.enrich(conn, edition_id=edition_id, model_name=model_name)


async def _seeded_edition(db: Database, tmp_path: Path) -> UUID:
    edition_id = await _ingest_epub(db, tmp_path)
    await _index(db, tmp_path, edition_id)
    return edition_id


async def test_full_hierarchy_and_concepts(db: Database, tmp_path: Path) -> None:
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider()
    report = await _enrich(db, edition_id, provider)

    assert report.created
    assert report.summaries_section == 2
    assert report.summaries_chapter == 2
    assert report.summaries_edition == 1
    assert report.concepts >= 1
    assert report.concept_aliases >= 1
    assert report.concept_evidences >= 1

    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
        concepts = await ConceptsRepository(conn).list_all()
    assert len(summaries) == 5
    scopes = {summary.scope_type for summary in summaries}
    assert scopes == {SummaryScope.SECTION, SummaryScope.CHAPTER, SummaryScope.EDITION}
    for summary in summaries:
        assert summary.supporting_passage_ids  # todo item publicado tem suporte (AC-12)
    assert concepts
    assert all(concept.state is ConceptState.PROPOSED for concept in concepts)
    assert provider.summary_requests  # provedor chamado para sínteses
    assert provider.concept_requests  # e para conceitos


async def test_summary_without_support_is_rejected(db: Database, tmp_path: Path) -> None:
    """SPEC §7.4: sem suporte identificado, o item abstrato não é publicado."""
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider(summary_factory=summary_without_support())
    report = await _enrich(db, edition_id, provider)

    assert report.created
    assert report.summaries_section == 0
    assert report.summaries_chapter == 0
    assert report.summaries_edition == 0
    assert report.warnings  # itens rejeitados registrados, não silenciosos

    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
        concepts = await ConceptsRepository(conn).list_all()
    assert summaries == []
    assert concepts  # conceitos continuam extraídos independentemente


async def test_support_out_of_scope_fails_closed(db: Database, tmp_path: Path) -> None:
    edition_id = await _seeded_edition(db, tmp_path)

    def _bad_summary(request):
        return SummaryResult(text="síntese", supporting_passage_ids=(uuid4(),))

    provider = FakeEnrichmentProvider(summary_factory=_bad_summary)
    with pytest.raises(ModelResponseError):
        await _enrich(db, edition_id, provider)

    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
    assert summaries == []  # falha fechada, nada parcial publicado


async def test_concept_leads_to_original_passages(db: Database, tmp_path: Path) -> None:
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider()
    await _enrich(db, edition_id, provider)

    async with db.connection() as conn:
        concepts = await ConceptsRepository(conn).list_all()
        assert concepts
        concept = concepts[0]
        passages = await ConceptsRepository(conn).supporting_passages(concept.id)
        indexed = await PassagesRepository(conn).list_by_edition(edition_id)
    assert passages
    indexed_ids = {p.id for p in indexed if p.embedding_version_id is not None}
    assert {p.id for p in passages} <= indexed_ids
    assert all(p.text for p in passages)


async def test_summaries_never_serve_as_citations(db: Database, tmp_path: Path) -> None:
    """AC-12: a recuperação descendente devolve SÓ passagens — a síntese não
    é citação final."""
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider()
    await _enrich(db, edition_id, provider)

    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
        indexed = await PassagesRepository(conn).list_by_edition(edition_id)
    indexed_texts = {p.text for p in indexed}
    for summary in summaries:
        async with db.connection() as conn:
            supports = await SummariesRepository(conn).supporting_passages(summary.id)
        assert supports
        assert {p.text for p in supports} <= indexed_texts
        # o texto da síntese nunca é uma passagem citável.
        assert summary.text not in indexed_texts
        assert summary.text not in {p.text for p in supports}


async def test_reexecution_with_new_version_preserves_history(db: Database, tmp_path: Path) -> None:
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider()
    first = await _enrich(db, edition_id, provider, model_name="model-a")
    second = await _enrich(db, edition_id, provider, model_name="model-b")

    assert first.created
    assert second.created
    assert first.summarizer_version_id != second.summarizer_version_id

    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
        evidence_count = await ConceptsRepository(conn).count_evidence_for_edition(edition_id)
    # duas gerações de sínteses conviven — histórico não foi sobrescrito (AC-15).
    assert len(summaries) == 10
    versions = {summary.generator_version_id for summary in summaries}
    assert versions == {UUID(first.summarizer_version_id), UUID(second.summarizer_version_id)}
    # conceito: evidência de duas versões convive (PK inclui extractor_version_id).
    assert evidence_count == 2


async def test_reexecution_same_version_is_idempotent(db: Database, tmp_path: Path) -> None:
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider()
    first = await _enrich(db, edition_id, provider, model_name="model-a")
    second = await _enrich(db, edition_id, provider, model_name="model-a")

    assert first.created
    assert not second.created
    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
    assert len(summaries) == 5  # mesma versão: sem duplicação


async def test_idempotent_even_when_concepts_empty(db: Database, tmp_path: Path) -> None:
    """A identidade de uma execução é a versão de síntese — conceitos podem
    legitimamente ser vazios sem quebrar a idempotência."""
    edition_id = await _seeded_edition(db, tmp_path)
    provider = FakeEnrichmentProvider(concepts_factory=lambda _req: ExtractedConcepts(concepts=()))
    first = await _enrich(db, edition_id, provider, model_name="model-a")
    second = await _enrich(db, edition_id, provider, model_name="model-a")

    assert first.created
    assert not second.created
    assert first.concepts == 0
    async with db.connection() as conn:
        summaries = await SummariesRepository(conn).list_by_edition(edition_id)
        concepts = await ConceptsRepository(conn).list_all()
    assert len(summaries) == 5
    assert concepts == []


async def test_unknown_edition_raises_not_found(db: Database, tmp_path: Path) -> None:
    service = EnrichmentService(FakeEnrichmentProvider())
    async with db.connection() as conn:
        with pytest.raises(NotFoundError):
            await service.enrich(conn, edition_id=uuid4(), model_name="fake-model")


async def test_edition_without_indexed_passages_fails(db: Database, tmp_path: Path) -> None:
    edition_id = await _ingest_epub(db, tmp_path)  # ingerida mas não indexada
    service = EnrichmentService(FakeEnrichmentProvider())
    async with db.connection() as conn:
        with pytest.raises(IngestionError):
            await service.enrich(conn, edition_id=edition_id, model_name="fake-model")
