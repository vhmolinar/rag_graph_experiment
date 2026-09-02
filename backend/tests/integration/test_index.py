"""Serviço de indexação ponta a ponta contra PostgreSQL real (T06).

Ingestão real (T05) primeiro produz a edição + artefato no store; depois
`IndexingService` reextrai, chunkeia e indexa com um provedor de embeddings
fake (determinístico, sem rede).
"""

import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub, make_text_pdf

from rag.application.index import IndexingService, IndexReport
from rag.application.ingest import IngestionService, load_metadata
from rag.domain.chunking import ChunkingParams
from rag.domain.enums import IngestionStatus
from rag.domain.errors import (
    EmbeddingDimensionError,
    IngestionError,
    ModelUnavailableError,
    NotFoundError,
)
from rag.domain.identifiers import sha256_of_text
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

pytestmark = pytest.mark.integration


class _FakeEmbeddingProvider:
    """Determinístico, sem rede: um vetor distinto por texto, na ordem.

    Usa a dimensão fixa do schema (T03) — uma `EmbeddingVersion` com outra
    dimensão é rejeitada no cadastro (RR05), antes mesmo de chegar à
    validação própria de T06 (`_WrongDimensionEmbeddingProvider` abaixo
    testa especificamente essa segunda camada).
    """

    def __init__(self, dimensions: int = EMBEDDING_COLUMN_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i + 1)] * self.dimensions for i in range(len(texts))]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dimensions


class _WrongDimensionEmbeddingProvider:
    """Registra a `EmbeddingVersion` com a dimensão correta do schema, mas
    retorna vetores de OUTRA dimensão — exercita a checagem própria de T06
    (`len(vec) != embedding_version.dimensions`), distinta da checagem de
    schema (RR05) que age sobre o valor registrado, não o retornado."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0] for _ in texts]  # sempre dimensão 2, incompatível

    async def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0]


class _CountMismatchEmbeddingProvider:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # sempre 1 vetor, não importa quantos textos — dimensão certa, mas
        # quantidade errada.
        return [[0.0] * EMBEDDING_COLUMN_DIMENSIONS]

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * EMBEDDING_COLUMN_DIMENSIONS


class _FailsOnSecondBatchEmbeddingProvider:
    """T6-06: falha a meio caminho de uma indexação em lotes — usado para
    provar que nenhum índice parcial é publicado."""

    def __init__(self, dimensions: int = EMBEDDING_COLUMN_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls == 2:
            raise ModelUnavailableError("falha simulada no segundo lote")
        return [[float(i + 1)] * self.dimensions for i in range(len(texts))]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dimensions


def _write_metadata(path: Path, *, title: str = "Livro Fixture") -> Path:
    path.write_text(
        f"title: {title}\nauthors: [Autor Fixture]\nedition_label: 1ª ed.\n",
        encoding="utf-8",
    )
    return path


def _extractor_pdf_no_model() -> DocumentConverter:
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(backend=PyPdfiumDocumentBackend)}
    )


async def _ingest_epub(
    db: Database, tmp_path: Path, *, title: str = "Livro Fixture", variant: int = 1
) -> UUID:
    from rag.adapters.docling_adapter import DoclingExtractor

    epub = tmp_path / f"livro{variant}.epub"
    epub.write_bytes(
        make_epub(
            [
                ("Capítulo I", ["Primeira frase do capitulo um.", "Segunda frase do capitulo um."]),
                ("Capítulo II", [f"Primeira frase do capitulo dois, variante {variant}."]),
            ]
        )
    )
    meta = load_metadata(_write_metadata(tmp_path / f"livro{variant}.yaml", title=title))
    service = IngestionService(ArtifactStore(tmp_path / "artifacts"), DoclingExtractor())
    async with db.connection() as conn:
        report = await service.ingest(conn, file_path=epub, metadata=meta)
    return UUID(report.edition_id)


async def _ingest_pdf(db: Database, tmp_path: Path, *, title: str = "Livro Fixture") -> UUID:
    from rag.adapters.docling_adapter import DoclingExtractor

    pdf = tmp_path / "livro.pdf"
    pdf.write_bytes(
        make_text_pdf(
            [
                ["CAPITULO I", "Primeira frase do capitulo um.", "Segunda frase do capitulo um."],
                ["CAPITULO II", "Primeira frase do capitulo dois."],
            ]
        )
    )
    meta = load_metadata(_write_metadata(tmp_path / "livro.yaml", title=title))
    service = IngestionService(
        ArtifactStore(tmp_path / "artifacts"), DoclingExtractor(converter=_extractor_pdf_no_model())
    )
    async with db.connection() as conn:
        report = await service.ingest(conn, file_path=pdf, metadata=meta)
    return UUID(report.edition_id)


def _service(tmp_path: Path, embedding_provider: object) -> IndexingService:
    from rag.adapters.docling_adapter import DoclingExtractor

    return IndexingService(
        ArtifactStore(tmp_path / "artifacts"),
        DoclingExtractor(converter=_extractor_pdf_no_model()),
        embedding_provider,  # type: ignore[arg-type]
    )


class TestIndexEdition:
    async def test_indexes_epub_creates_parents_and_children(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            report = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert report.created
        assert report.parents == 2  # duas seções-folha (Capítulo I e II)
        assert report.children >= 2

        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert edition is not None
        assert edition.ingestion_status is IngestionStatus.INDEXED
        assert len(passages) == report.parents + report.children
        children = [p for p in passages if p.embedding_version_id is not None]
        parents = [p for p in passages if p.embedding_version_id is None]
        assert len(children) == report.children
        assert len(parents) == report.parents
        for child in children:
            assert child.parent_passage_id in {p.id for p in parents}
            assert child.section_id is not None
            # EPUB: sem páginas.
            assert child.page_start_id is None
            assert child.char_start is None

    async def test_indexes_pdf_with_page_offsets(self, db: Database, tmp_path: Path) -> None:
        edition_id = await _ingest_pdf(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            pages = await PagesRepository(conn).list_by_edition(edition_id)
        pages_by_id = {p.id: p for p in pages}
        for passage in passages:
            if passage.page_start_id is None:
                continue
            assert passage.char_start is not None
            assert passage.char_end is not None
            start_page = pages_by_id[passage.page_start_id]
            end_page = pages_by_id[passage.page_end_id]  # type: ignore[index]
            if passage.page_start_id == passage.page_end_id:
                assert start_page.text[passage.char_start : passage.char_end] == passage.text
            else:
                reconstructed = (
                    start_page.text[passage.char_start :] + "\n" + end_page.text[: passage.char_end]
                )
                assert reconstructed == passage.text

    async def test_reindex_without_force_is_idempotent(self, db: Database, tmp_path: Path) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            second = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert first.created
        assert not second.created
        assert len(provider.calls) == 1  # segunda chamada nem gerou embeddings

    async def test_force_reindexes_preserves_passage_history(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T6-01: `--force` minta uma execução NOVA sem apagar a antiga —
        um `AnswerRun` que referencie IDs da primeira execução continua
        reproduzível depois do `--force`."""
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            forced = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                force=True,
            )
        assert first.created
        assert forced.created
        assert forced.forced
        assert forced.index_run_id != first.index_run_id
        assert len(provider.calls) == 2
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM passages WHERE edition_id = %s", (edition_id,))
            total_row = await cur.fetchone()
            await cur.execute(
                "SELECT count(*) FROM passages WHERE index_run_id = %s", (first.index_run_id,)
            )
            first_run_row = await cur.fetchone()
            await cur.execute(
                "SELECT is_active FROM index_runs WHERE id = %s", (first.index_run_id,)
            )
            first_run_active = await cur.fetchone()
            await cur.execute(
                "SELECT is_active FROM index_runs WHERE id = %s", (forced.index_run_id,)
            )
            forced_run_active = await cur.fetchone()
        assert total_row is not None
        assert first_run_row is not None
        assert first_run_active is not None
        assert forced_run_active is not None
        # histórico preservado: NADA foi apagado fisicamente.
        assert total_row[0] == first.parents + first.children + forced.parents + forced.children
        assert first_run_row[0] == first.parents + first.children
        # só a execução mais recente fica ativa.
        assert first_run_active[0] is False
        assert forced_run_active[0] is True

    async def test_different_chunking_params_create_new_version(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        edition_id_2 = await _ingest_epub(db, tmp_path, title="Outro Livro", variant=2)
        async with db.connection() as conn:
            second = await service.index_edition(
                conn,
                edition_id=edition_id_2,
                chunking_params=ChunkingParams(child_target_tokens=999),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert first.chunking_version_id != second.chunking_version_id

    async def test_different_chunking_params_same_edition_creates_new_run_without_force(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T6-01: parâmetros novos são executados mesmo sem `--force` — a
        idempotência olha para a identidade completa da execução, não para
        a mera existência de passagens."""
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            second = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(child_target_tokens=999),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert first.created
        assert second.created
        assert not second.forced
        assert second.chunking_version_id != first.chunking_version_id
        assert second.index_run_id != first.index_run_id
        assert len(provider.calls) == 2
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM passages WHERE edition_id = %s", (edition_id,))
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == first.parents + first.children + second.parents + second.children

    async def test_different_embedding_model_same_edition_creates_new_run_without_force(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            second = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="outro-modelo",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert first.created
        assert second.created
        assert second.embedding_version_id != first.embedding_version_id
        assert second.index_run_id != first.index_run_id
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM index_runs WHERE edition_id = %s AND is_active",
                (edition_id,),
            )
            active_row = await cur.fetchone()
        assert active_row is not None
        assert active_row[0] == 1

    async def test_concurrent_indexing_same_edition_does_not_race(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T6-10: duas indexações concorrentes da mesma edição são
        serializadas por um lock por edição — a segunda observa a execução
        já ativa em vez de competir por uma restrição de unicidade."""
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)

        async def _run() -> IndexReport:
            async with db.connection() as conn:
                return await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )

        first, second = await asyncio.gather(_run(), _run())
        assert {first.created, second.created} == {True, False}
        assert len(provider.calls) == 1
        winner = first if first.created else second
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM passages WHERE edition_id = %s", (edition_id,))
            passages_row = await cur.fetchone()
            await cur.execute(
                "SELECT count(*) FROM index_runs WHERE edition_id = %s AND is_active",
                (edition_id,),
            )
            active_runs_row = await cur.fetchone()
        assert passages_row is not None
        assert active_runs_row is not None
        assert passages_row[0] == winner.parents + winner.children
        assert active_runs_row[0] == 1

    async def test_embeddings_are_generated_in_configurable_batches(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            report = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                batch_size=1,
            )
        assert report.children >= 2
        assert len(provider.calls) == report.children
        assert all(len(call) == 1 for call in provider.calls)

    async def test_batch_failure_leaves_no_partial_index(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FailsOnSecondBatchEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            with pytest.raises(ModelUnavailableError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                    batch_size=1,
                )
        assert provider.calls == 2  # confirma que o segundo lote foi de fato tentado
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert passages == []
        assert edition is not None
        assert edition.ingestion_status is not IngestionStatus.INDEXED
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM index_runs WHERE edition_id = %s", (edition_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0

    async def test_original_text_preserves_source_blocks_for_multi_paragraph_epub_chunk(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
        assert passages
        for passage in passages:
            assert passage.original_text is not None
            assert passage.citable_text == passage.original_text
        multi_paragraph = [p for p in passages if p.original_text and "\n" in p.original_text]
        assert multi_paragraph, "esperava ao menos um chunk cobrindo os 2 parágrafos do capítulo I"
        for passage in multi_paragraph:
            original_text = passage.original_text
            assert original_text is not None
            assert original_text != passage.text
            assert "Primeira frase do capitulo um." in original_text
            assert "Segunda frase do capitulo um." in original_text

    async def test_reextraction_diverging_from_persisted_pages_fails_closed(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T6-02: uma reextração que produza conteúdo de página diferente do
        que foi persistido na ingestão original falha fechado, sem tocar
        chunking, embeddings ou o status da edição."""
        edition_id = await _ingest_pdf(db, tmp_path)
        adulterated_text = "texto de página adulterado, divergente da extração original"
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE pages SET text = %s, text_sha256 = %s "
                "WHERE edition_id = %s AND physical_index = 0",
                (adulterated_text, sha256_of_text(adulterated_text), edition_id),
            )
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(IngestionError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert passages == []
        assert edition is not None
        assert edition.ingestion_status is not IngestionStatus.INDEXED
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM index_runs WHERE edition_id = %s", (edition_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0

    async def test_reextraction_diverging_from_persisted_sections_fails_closed(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_pdf(db, tmp_path)
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE sections SET title = 'TÍTULO ADULTERADO' "
                "WHERE edition_id = %s AND ordinal = 0",
                (edition_id,),
            )
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(IngestionError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert passages == []
        assert edition is not None
        assert edition.ingestion_status is not IngestionStatus.INDEXED

    async def test_embedding_dimension_mismatch_fails_before_persisting(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _WrongDimensionEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(EmbeddingDimensionError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert passages == []
        assert edition is not None
        assert edition.ingestion_status is not IngestionStatus.INDEXED

    async def test_embedding_count_mismatch_fails_before_persisting(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _CountMismatchEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(EmbeddingDimensionError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
        assert passages == []

    async def test_unknown_edition_raises_not_found(self, db: Database, tmp_path: Path) -> None:
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(NotFoundError):
                await service.index_edition(
                    conn,
                    edition_id=uuid4(),
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )

    async def test_context_header_includes_work_and_section(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path, title="Titulo Distintivo")
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            sections = await SectionsRepository(conn).list_by_edition(edition_id)
        section_titles = {s.title for s in sections}
        for passage in passages:
            assert "Titulo Distintivo" in passage.context_header
            assert any(title in passage.context_header for title in section_titles if title)
            # o cabeçalho contextual nunca é o texto citável em si.
            assert passage.context_header != passage.text
