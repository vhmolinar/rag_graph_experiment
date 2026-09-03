"""CRUD de integração: obra, edição, seção, página, passagem, versões, execução (T03)."""

from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError

from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer, QuoteResponse, VerificationResult
from rag.domain.enums import (
    ArtifactKind,
    IngestionStatus,
    QueryStatus,
    RankingStage,
    SourceType,
    VerificationAction,
)
from rag.domain.errors import (
    ConcurrencyError,
    ConflictError,
    EmbeddingDimensionError,
    ErrorCode,
    NotFoundError,
)
from rag.domain.library import (
    Contributor,
    DerivedArtifactRef,
    Edition,
    Page,
    Passage,
    Section,
    Work,
)
from rag.domain.query import EditionFilter
from rag.domain.runs import AnswerRun, RankedCandidate, StageLatency, VersionSet
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ExtractionVersion,
    ModelEndpointVersion,
    PromptVersion,
    RetrievalPolicyVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

HASH_A = "a" * 64
HASH_B = "b" * 64


async def _work(db: Database) -> Work:
    async with db.connection() as conn:
        work = Work(
            canonical_title="Dom Casmurro",
            authors=[Contributor(name="Machado de Assis")],
        )
        return await WorksRepository(conn).create(work)


def _edition(
    work_id: UUID,
    sha256: str = HASH_A,
    source_type: SourceType = SourceType.PDF_TEXT,
    edition_label: str | None = None,
    derived_artifacts: list[DerivedArtifactRef] | None = None,
    extraction_warnings: tuple[str, ...] = (),
) -> Edition:
    return Edition(
        work_id=work_id,
        title="Dom Casmurro",
        source_type=source_type,
        source_sha256=sha256,
        edition_label=edition_label,
        derived_artifacts=derived_artifacts or [],
        extraction_warnings=extraction_warnings,
    )


class TestWorksAndEditions:
    async def test_work_roundtrip_with_contributors(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            loaded = await WorksRepository(conn).get(work.id)
        assert loaded is not None
        assert loaded.canonical_title == "Dom Casmurro"
        assert [a.name for a in loaded.authors] == ["Machado de Assis"]

    async def test_duplicate_source_hash_rejected(self, db: Database) -> None:
        """AC-01: mesma origem não duplica edição (constraint UNIQUE)."""
        work = await _work(db)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            await repo.create(_edition(work.id, HASH_A))
            with pytest.raises(ConflictError):
                await repo.create(_edition(work.id, HASH_A))

    async def test_two_editions_same_work_distinct(self, db: Database) -> None:
        """AC-02: duas edições da mesma obra coexistem distinguíveis."""
        work = await _work(db)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            first = await repo.create(_edition(work.id, HASH_A, edition_label="1ª ed."))
            second = await repo.create(_edition(work.id, HASH_B, edition_label="2ª ed."))
            editions = await repo.list_by_work(work.id)
        assert first.id != second.id
        assert {e.id for e in editions} == {first.id, second.id}
        assert {e.edition_label for e in editions} == {"1ª ed.", "2ª ed."}

    async def test_derived_artifact_persisted(self, db: Database) -> None:
        """Decisão §10.1.5: artefato OCR derivado aponta para o original."""
        work = await _work(db)
        derived = DerivedArtifactRef(
            sha256=HASH_B,
            kind=ArtifactKind.OCR_TEXT_LAYER,
            derived_from=HASH_A,
            generator="docling-ocr",
        )
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            edition = await repo.create(
                _edition(
                    work.id,
                    HASH_A,
                    source_type=SourceType.PDF_SCAN,
                    derived_artifacts=[derived],
                )
            )
            loaded = await repo.get(edition.id)
        assert loaded is not None
        assert loaded.derived_artifacts[0].sha256 == HASH_B
        assert loaded.derived_artifacts[0].derived_from == HASH_A

    async def test_derived_provenance_enforced_by_db(self, db: Database) -> None:
        """R4-02: FK composta (edition_id, derived_from_sha256) impede OCR
        associado ao hash original errado ou à edição errada (AC-02/03)."""
        work = await _work(db)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            ed_a = await repo.create(_edition(work.id, HASH_A, source_type=SourceType.PDF_SCAN))
            ed_b = await repo.create(_edition(work.id, HASH_B, source_type=SourceType.PDF_SCAN))
        insert = (
            "INSERT INTO derived_artifacts (id, edition_id, sha256, kind,"
            " derived_from_sha256, generator, created_at) VALUES"
            " (gen_random_uuid(), %s, %s, 'ocr_text_layer', %s, 'test', now())"
        )
        # hash original que não é o source_sha256 da edição
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await conn.execute(insert, (ed_a.id, "c" * 64, "c" * 64))
        # hash original correto, mas edição errada
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await conn.execute(insert, (ed_b.id, "d" * 64, HASH_A))
        # associação válida: derivado da edição A apontando para o original de A
        async with db.connection() as conn:
            await conn.execute(insert, (ed_a.id, "e" * 64, HASH_A))
            row = await conn.execute(
                "SELECT COUNT(*) FROM derived_artifacts WHERE edition_id = %s",
                (ed_a.id,),
            )
            count = await row.fetchone()
        assert count is not None
        assert count[0] == 1

    async def test_ingestion_status_transition(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            edition = await repo.create(_edition(work.id))
            await repo.update_ingestion_status(edition.id, IngestionStatus.EXTRACTED)
            loaded = await repo.get(edition.id)
        assert loaded is not None
        assert loaded.ingestion_status is IngestionStatus.EXTRACTED

    async def test_extraction_warnings_roundtrip(self, db: Database) -> None:
        """T5-08: warnings de extração persistem e ficam disponíveis a
        `rag inspect` depois que o processo de ingestão termina."""
        work = await _work(db)
        warnings = ("1 tabela(s) ignorada(s): conteúdo não textual fora da fase 1",)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            edition = await repo.create(_edition(work.id, extraction_warnings=warnings))
            loaded = await repo.get(edition.id)
            by_hash = await repo.get_by_source_hash(HASH_A)
        assert loaded is not None
        assert loaded.extraction_warnings == warnings
        assert by_hash is not None
        assert by_hash.extraction_warnings == warnings

    async def test_extraction_warnings_default_empty(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            edition = await repo.create(_edition(work.id))
            loaded = await repo.get(edition.id)
        assert loaded is not None
        assert loaded.extraction_warnings == ()

    async def test_get_by_source_hash(self, db: Database) -> None:
        """R11: caminho de idempotência da ingestão — create → lookup → mesmo ID."""
        work = await _work(db)
        async with db.connection() as conn:
            repo = EditionsRepository(conn)
            edition = await repo.create(_edition(work.id, HASH_A))
            found = await repo.get_by_source_hash(HASH_A)
            missing = await repo.get_by_source_hash(HASH_B)
        assert found is not None
        assert found.id == edition.id
        assert missing is None


class TestSectionsPagesPassages:
    async def _edition_with_content(self, db: Database) -> Edition:
        work = await _work(db)
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).create(_edition(work.id))
            await SectionsRepository(conn).create_many(
                [
                    Section(edition_id=edition.id, level=0, ordinal=0, path=["Obra"]),
                    Section(
                        edition_id=edition.id,
                        level=1,
                        ordinal=1,
                        path=["Obra", "Capítulo I"],
                        start_page=0,
                        end_page=1,
                    ),
                ]
            )
            await PagesRepository(conn).create_many(
                [
                    Page.create(edition_id=edition.id, physical_index=0, text="página um"),
                    Page.create(
                        edition_id=edition.id,
                        physical_index=1,
                        text="página dois",
                        printed_label="p. 2",
                    ),
                ]
            )
            return edition

    async def test_sections_and_pages_roundtrip(self, db: Database) -> None:
        edition = await self._edition_with_content(db)
        async with db.connection() as conn:
            sections = await SectionsRepository(conn).list_by_edition(edition.id)
            pages = await PagesRepository(conn).list_by_edition(edition.id)
        assert [s.ordinal for s in sections] == [0, 1]
        assert sections[1].path == ["Obra", "Capítulo I"]
        assert [p.physical_index for p in pages] == [0, 1]
        assert pages[1].printed_label == "p. 2"

    async def test_passage_roundtrip_with_embedding(self, db: Database) -> None:
        edition = await self._edition_with_content(db)
        async with db.connection() as conn:
            versions = VersionsRepository(conn)
            chunking = await versions.get_or_create(
                ChunkingVersion(label="chunk-test", params={"max_tokens": 512}, created_at=utcnow())
            )
            embedding_version = await versions.get_or_create(
                EmbeddingVersion(
                    label="emb-test",
                    model_name="qwen3-embedding",
                    dimensions=1024,
                    created_at=utcnow(),
                )
            )
            passage = Passage(
                edition_id=edition.id,
                ordinal=0,
                text="A liberdade interior é o tema.",
                token_count=6,
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            repo = PassagesRepository(conn)
            await repo.create(passage, embedding=[0.01] * 1024)
            loaded = await repo.get(passage.id)
        assert loaded is not None
        assert loaded.text == "A liberdade interior é o tema."
        assert loaded.embedding_version_id == embedding_version.id

    async def test_wrong_embedding_dimension_fails_before_persist(self, db: Database) -> None:
        """Dimensão inesperada falha antes de gravar (T06 aprofunda; AC-15)."""
        edition = await self._edition_with_content(db)
        async with db.connection() as conn:
            versions = VersionsRepository(conn)
            chunking = await versions.get_or_create(
                ChunkingVersion(label="chunk-dim", created_at=utcnow())
            )
            embedding_version = await versions.get_or_create(
                EmbeddingVersion(
                    label="emb-dim", model_name="m", dimensions=1024, created_at=utcnow()
                )
            )
            passage = Passage(
                edition_id=edition.id,
                ordinal=0,
                text="trecho",
                token_count=1,
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            repo = PassagesRepository(conn)
            with pytest.raises(EmbeddingDimensionError):
                await repo.create(passage, embedding=[0.1, 0.2, 0.3])
            assert await repo.get(passage.id) is None

    async def test_fts_generated_column_matches(self, db: Database) -> None:
        edition = await self._edition_with_content(db)
        async with db.connection() as conn:
            chunking = await VersionsRepository(conn).get_or_create(
                ChunkingVersion(label="chunk-fts", created_at=utcnow())
            )
            passage = Passage(
                edition_id=edition.id,
                ordinal=0,
                text="A educação sentimental do protagonista",
                token_count=5,
                chunking_version_id=chunking.id,
            )
            await PassagesRepository(conn).create(passage)
            cursor = await conn.execute(
                "SELECT 1 FROM passages WHERE text_search @@ "
                "plainto_tsquery('portuguese_unaccent', 'educação') AND id = %s",
                (passage.id,),
            )
            assert await cursor.fetchone() is not None


class TestConflictDivergence:
    """R08: repetição idêntica é idempotente; divergência falha explicitamente."""

    async def test_identical_sections_replay_is_idempotent(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).create(_edition(work.id))
            repo = SectionsRepository(conn)
            section = Section(edition_id=edition.id, level=0, ordinal=0, path=["Obra"])
            await repo.create_many([section])
            # nova instância (outro UUID), mesmo conteúdo lógico
            await repo.create_many(
                [Section(edition_id=edition.id, level=0, ordinal=0, path=["Obra"])]
            )
            sections = await repo.list_by_edition(edition.id)
        assert len(sections) == 1

    async def test_divergent_section_replay_fails(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).create(_edition(work.id))
            repo = SectionsRepository(conn)
            await repo.create_many(
                [Section(edition_id=edition.id, level=0, ordinal=0, path=["Obra"])]
            )
            with pytest.raises(ConflictError, match="divergente"):
                await repo.create_many(
                    [
                        Section(
                            edition_id=edition.id,
                            level=0,
                            ordinal=0,
                            path=["Obra ALTERADA"],
                        )
                    ]
                )

    async def test_identical_pages_replay_is_idempotent(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).create(_edition(work.id))
            repo = PagesRepository(conn)
            page = Page.create(edition_id=edition.id, physical_index=0, text="mesmo texto")
            await repo.create_many([page])
            await repo.create_many(
                [Page.create(edition_id=edition.id, physical_index=0, text="mesmo texto")]
            )
            pages = await repo.list_by_edition(edition.id)
        assert len(pages) == 1

    async def test_divergent_page_replay_fails(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).create(_edition(work.id))
            repo = PagesRepository(conn)
            await repo.create_many(
                [Page.create(edition_id=edition.id, physical_index=0, text="original")]
            )
            with pytest.raises(ConflictError, match="divergente"):
                await repo.create_many(
                    [Page.create(edition_id=edition.id, physical_index=0, text="ALTERADO")]
                )


class TestCrossEditionIntegrity:
    """R01: FKs compostas impedem referências entre edições distintas."""

    async def _two_editions_with_content(
        self, db: Database
    ) -> tuple[Edition, Edition, Section, Page, ChunkingVersion]:
        work = await _work(db)
        async with db.connection() as conn:
            ed_a = await EditionsRepository(conn).create(_edition(work.id, HASH_A))
            ed_b = await EditionsRepository(conn).create(_edition(work.id, HASH_B))
            section_b = Section(edition_id=ed_b.id, level=0, ordinal=0, path=["B"])
            page_b = Page.create(edition_id=ed_b.id, physical_index=0, text="página de B")
            await SectionsRepository(conn).create_many([section_b])
            await PagesRepository(conn).create_many([page_b])
            chunking = await VersionsRepository(conn).get_or_create(
                ChunkingVersion(label="chunk-r01", created_at=utcnow())
            )
        return ed_a, ed_b, section_b, page_b, chunking

    async def test_passage_with_page_from_other_edition_fails(self, db: Database) -> None:
        ed_a, _ed_b, _section_b, page_b, chunking = await self._two_editions_with_content(db)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await PassagesRepository(conn).create(
                    Passage(
                        edition_id=ed_a.id,
                        ordinal=0,
                        text="passagem de A com página de B",
                        token_count=6,
                        chunking_version_id=chunking.id,
                        page_start_id=page_b.id,
                    )
                )

    async def test_passage_with_section_from_other_edition_fails(self, db: Database) -> None:
        ed_a, _ed_b, section_b, _page_b, chunking = await self._two_editions_with_content(db)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await PassagesRepository(conn).create(
                    Passage(
                        edition_id=ed_a.id,
                        ordinal=0,
                        text="passagem de A com seção de B",
                        token_count=6,
                        chunking_version_id=chunking.id,
                        section_id=section_b.id,
                    )
                )

    async def test_child_passage_with_parent_from_other_edition_fails(self, db: Database) -> None:
        ed_a, ed_b, _section_b, _page_b, chunking = await self._two_editions_with_content(db)
        async with db.connection() as conn:
            parent = Passage(
                edition_id=ed_b.id,
                ordinal=0,
                text="pai em B",
                token_count=2,
                chunking_version_id=chunking.id,
            )
            await PassagesRepository(conn).create(parent)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await PassagesRepository(conn).create(
                    Passage(
                        edition_id=ed_a.id,
                        ordinal=1,
                        text="filho em A com pai em B",
                        token_count=6,
                        chunking_version_id=chunking.id,
                        parent_passage_id=parent.id,
                    )
                )

    async def _generator_version_id(self, db: Database) -> UUID:
        async with db.connection() as conn:
            version = await VersionsRepository(conn).get_or_create(
                ModelEndpointVersion(
                    label="gen-rr01",
                    endpoint_kind="generator",
                    provider="local",
                    model_name="qwen3",
                    created_at=utcnow(),
                )
            )
        return version.id

    async def test_summary_section_scope_from_other_edition_fails(self, db: Database) -> None:
        """RR01: síntese de A não pode referenciar seção de B."""
        ed_a, _ed_b, section_b, _page_b, _chunk = await self._two_editions_with_content(db)
        gen_id = await self._generator_version_id(db)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO summaries (id, edition_id, scope_type, section_id, text,"
                    " generator_version_id) VALUES (gen_random_uuid(), %s, 'section', %s,"
                    " 'síntese', %s)",
                    (ed_a.id, section_b.id, gen_id),
                )

    async def test_summary_section_scope_with_unknown_section_fails(self, db: Database) -> None:
        """RR01: section_id inexistente é rejeitado pela FK composta."""
        ed_a, *_ = await self._two_editions_with_content(db)
        gen_id = await self._generator_version_id(db)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO summaries (id, edition_id, scope_type, section_id, text,"
                    " generator_version_id) VALUES (gen_random_uuid(), %s, 'section', %s,"
                    " 'síntese', %s)",
                    (ed_a.id, uuid4(), gen_id),
                )

    async def test_summary_chapter_scope_without_section_fails(self, db: Database) -> None:
        """RR01: 'chapter' é representado por Section — section_id obrigatório."""
        ed_a, *_ = await self._two_editions_with_content(db)
        gen_id = await self._generator_version_id(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO summaries (id, edition_id, scope_type, section_id, text,"
                    " generator_version_id) VALUES (gen_random_uuid(), %s, 'chapter', NULL,"
                    " 'síntese', %s)",
                    (ed_a.id, gen_id),
                )

    async def test_summary_chapter_scope_requires_top_level_section(self, db: Database) -> None:
        """R4-03: 'chapter' apontando para seção aninhada (level > 0) falha."""
        ed_a, *_ = await self._two_editions_with_content(db)
        gen_id = await self._generator_version_id(db)
        async with db.connection() as conn:
            parent = Section(edition_id=ed_a.id, level=0, ordinal=0, path=["A"])
            nested = Section(
                edition_id=ed_a.id,
                parent_section_id=parent.id,
                level=1,
                ordinal=1,
                path=["A", "A.1"],
            )
            await SectionsRepository(conn).create_many([parent, nested])
        with pytest.raises(psycopg.errors.RaiseException, match="seção de topo"):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO summaries (id, edition_id, scope_type, section_id, text,"
                    " generator_version_id) VALUES (gen_random_uuid(), %s, 'chapter', %s,"
                    " 'síntese', %s)",
                    (ed_a.id, nested.id, gen_id),
                )

    async def test_summary_valid_scopes_pass(self, db: Database) -> None:
        """RR01: escopos section/chapter/edition válidos na mesma edição passam."""
        ed_a, *_ = await self._two_editions_with_content(db)
        gen_id = await self._generator_version_id(db)
        async with db.connection() as conn:
            section_a = Section(edition_id=ed_a.id, level=0, ordinal=0, path=["A"])
            await SectionsRepository(conn).create_many([section_a])
            for scope, section_id in (
                ("section", section_a.id),
                ("chapter", section_a.id),
                ("edition", None),
            ):
                await conn.execute(
                    "INSERT INTO summaries (id, edition_id, scope_type, section_id, text,"
                    " generator_version_id) VALUES (gen_random_uuid(), %s, %s, %s,"
                    " 'síntese', %s)",
                    (ed_a.id, scope, section_id, gen_id),
                )
            count = await conn.execute(
                "SELECT COUNT(*) FROM summaries WHERE edition_id = %s", (ed_a.id,)
            )
            row = await count.fetchone()
        assert row is not None
        assert row[0] == 3

    async def test_section_with_parent_from_other_edition_fails(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            ed_a = await EditionsRepository(conn).create(_edition(work.id, HASH_A))
            ed_b = await EditionsRepository(conn).create(_edition(work.id, HASH_B))
            repo = SectionsRepository(conn)
            await repo.create_many([Section(edition_id=ed_b.id, level=0, ordinal=0, path=["B"])])
            parent_b = (await repo.list_by_edition(ed_b.id))[0]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with db.connection() as conn:
                await SectionsRepository(conn).create_many(
                    [
                        Section(
                            edition_id=ed_a.id,
                            parent_section_id=parent_b.id,
                            level=1,
                            ordinal=0,
                            path=["B", "filho em A"],
                        )
                    ]
                )

    async def test_valid_same_edition_references_work(self, db: Database) -> None:
        work = await _work(db)
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).create(_edition(work.id))
            sections_repo = SectionsRepository(conn)
            await sections_repo.create_many(
                [Section(edition_id=edition.id, level=0, ordinal=0, path=["Obra"])]
            )
            parent = (await sections_repo.list_by_edition(edition.id))[0]
            await sections_repo.create_many(
                [
                    Section(
                        edition_id=edition.id,
                        parent_section_id=parent.id,
                        level=1,
                        ordinal=1,
                        path=["Obra", "Cap. I"],
                    )
                ]
            )
            await PagesRepository(conn).create_many(
                [Page.create(edition_id=edition.id, physical_index=0, text="p0")]
            )
            page = (await PagesRepository(conn).list_by_edition(edition.id))[0]
            chunking = await VersionsRepository(conn).get_or_create(
                ChunkingVersion(label="chunk-r01-ok", created_at=utcnow())
            )
            passages_repo = PassagesRepository(conn)
            parent_passage = Passage(
                edition_id=edition.id,
                ordinal=0,
                text="passagem pai",
                token_count=2,
                chunking_version_id=chunking.id,
                section_id=parent.id,
                page_start_id=page.id,
                page_end_id=page.id,
            )
            await passages_repo.create(parent_passage)
            child = Passage(
                edition_id=edition.id,
                ordinal=1,
                text="passagem filha",
                token_count=2,
                chunking_version_id=chunking.id,
                parent_passage_id=parent_passage.id,
            )
            await passages_repo.create(child)
            loaded = await passages_repo.get(child.id)
        assert loaded is not None
        assert loaded.parent_passage_id == parent_passage.id


class TestVersions:
    async def test_get_or_create_is_idempotent(self, db: Database) -> None:
        async with db.connection() as conn:
            repo = VersionsRepository(conn)
            first = await repo.get_or_create(
                ChunkingVersion(label="c1", params={"max_tokens": 512}, created_at=utcnow())
            )
            second = await repo.get_or_create(
                ChunkingVersion(label="c1", params={"max_tokens": 512}, created_at=utcnow())
            )
        assert first.id == second.id

    async def test_distinct_params_create_distinct_versions(self, db: Database) -> None:
        async with db.connection() as conn:
            repo = VersionsRepository(conn)
            first = await repo.get_or_create(
                ChunkingVersion(label="c1", params={"max_tokens": 512}, created_at=utcnow())
            )
            second = await repo.get_or_create(
                ChunkingVersion(label="c1", params={"max_tokens": 256}, created_at=utcnow())
            )
        assert first.id != second.id

    async def test_all_version_kinds(self, db: Database) -> None:
        async with db.connection() as conn:
            repo = VersionsRepository(conn)
            extraction = await repo.get_or_create(
                ExtractionVersion(label="e1", created_at=utcnow())
            )
            embedding = await repo.get_or_create(
                EmbeddingVersion(label="em1", model_name="m", dimensions=1024, created_at=utcnow())
            )
            endpoint = await repo.get_or_create(
                ModelEndpointVersion(
                    label="g1",
                    endpoint_kind="generator",
                    provider="local",
                    model_name="qwen3",
                    created_at=utcnow(),
                )
            )
            prompt = await repo.get_or_create(
                PromptVersion(label="p1", template_sha256="a" * 64, created_at=utcnow())
            )
            policy = await repo.get_or_create(
                RetrievalPolicyVersion(
                    label="r1", params={"top_k": 20, "rrf_k": 60}, created_at=utcnow()
                )
            )
            assert (await repo.get(ExtractionVersion, extraction.id)) == extraction
            assert (await repo.get(EmbeddingVersion, embedding.id)) == embedding
            assert (await repo.get(ModelEndpointVersion, endpoint.id)) == endpoint
            assert (await repo.get(PromptVersion, prompt.id)) == prompt
            assert (await repo.get(RetrievalPolicyVersion, policy.id)) == policy

    async def test_embedding_version_rejects_dimension_outside_schema(self, db: Database) -> None:
        """RR05: versão incompatível com vector(1024) falha no cadastro."""
        async with db.connection() as conn:
            repo = VersionsRepository(conn)
            with pytest.raises(EmbeddingDimensionError, match="schema"):
                await repo.get_or_create(
                    EmbeddingVersion(
                        label="emb-wrong",
                        model_name="m",
                        dimensions=8,
                        created_at=utcnow(),
                    )
                )
            ok = await repo.get_or_create(
                EmbeddingVersion(
                    label="emb-ok", model_name="m", dimensions=1024, created_at=utcnow()
                )
            )
            assert ok.dimensions == 1024

    async def test_prompt_version_identity_includes_template_hash(self, db: Database) -> None:
        """R03: mesmo label/params com template diferente gera versão diferente."""
        async with db.connection() as conn:
            repo = VersionsRepository(conn)
            first = await repo.get_or_create(
                PromptVersion(label="answer", template_sha256="a" * 64, created_at=utcnow())
            )
            same = await repo.get_or_create(
                PromptVersion(label="answer", template_sha256="a" * 64, created_at=utcnow())
            )
            other = await repo.get_or_create(
                PromptVersion(label="answer", template_sha256="b" * 64, created_at=utcnow())
            )
            loaded_first = await repo.get(PromptVersion, first.id)
            loaded_other = await repo.get(PromptVersion, other.id)
        assert first.id == same.id
        assert first.id != other.id
        assert loaded_first is not None
        assert loaded_first.template_sha256 == "a" * 64
        assert loaded_other is not None
        assert loaded_other.template_sha256 == "b" * 64


class TestAnswerRuns:
    async def test_run_roundtrip(self, db: Database) -> None:
        run = AnswerRun(
            question_original="O que é spleen?",
            question_anonymized="O que é spleen?",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.create(run)
            queued = await repo.get(run.id)
            assert queued is not None
            assert queued.status is QueryStatus.QUEUED

            finished = run.transition(QueryStatus.RUNNING).transition(
                QueryStatus.ABSTAINED,
                response=GeneratedAnswer(
                    answer_markdown="",
                    abstained=True,
                    abstention_reason="sem evidências",
                ),
            )
            await repo.save(finished)
            loaded = await repo.get(run.id)
        assert loaded is not None
        assert loaded.status is QueryStatus.ABSTAINED
        assert isinstance(loaded.response, GeneratedAnswer)
        assert loaded.response.abstained
        assert loaded.limitations == ()

    async def test_run_roundtrip_with_limitations(self, db: Database) -> None:
        """R05: limitações persistem e sobrevivem a reload do AnswerRun (AC-11, AC-15)."""
        limitation_text = (
            "A resposta se apoia em evidências de uma única obra; a ausência de outras "
            "fontes limita a comparação."
        )
        claim = Claim(
            id="c1",
            text="Comparação preliminar.",
            evidence_ids=(UUID("11111111-1111-1111-1111-111111111111"),),
        )
        response = GeneratedAnswer(
            answer_markdown=claim.text,
            blocks=(AnswerBlock(text=claim.text, claim_id="c1"),),
            claims=(claim,),
            abstained=False,
        )
        run = AnswerRun(
            question_original="Compare as obras.",
            question_anonymized="Compare as obras.",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.create(run)
            finished = run.transition(QueryStatus.RUNNING).transition(
                QueryStatus.SUCCEEDED,
                response=response,
                limitations=(limitation_text,),
            )
            await repo.save(finished)
            loaded = await repo.get(run.id)
        assert loaded is not None
        assert loaded.status is QueryStatus.SUCCEEDED
        assert loaded.limitations == (limitation_text,)

    async def test_full_roundtrip_with_all_stages_and_versions(self, db: Database) -> None:
        """R09: candidatos dos 4 estágios, evidências e VersionSet completos (AC-06/15)."""
        passage_a, passage_b = uuid4(), uuid4()
        claim = Claim(id="c1", text="Spleen é o tédio baudelairiano.", evidence_ids=(passage_a,))
        blocks = (
            AnswerBlock(text=claim.text, claim_id="c1"),
            AnswerBlock(text=" "),
        )
        response = GeneratedAnswer(
            answer_markdown="".join(block.text for block in blocks),
            blocks=blocks,
            claims=(claim,),
            abstained=False,
        )
        run = AnswerRun(
            question_original="O que é spleen?",
            question_anonymized="O que é spleen?",
            explicit_filters=EditionFilter(include_work_ids=frozenset({uuid4()})),
        )
        run = run.transition(
            QueryStatus.RUNNING,
            rewritten_query="definição de spleen",
            candidates=[
                RankedCandidate(
                    passage_id=passage_a, stage=RankingStage.LEXICAL, score=12.5, rank=0
                ),
                RankedCandidate(
                    passage_id=passage_b, stage=RankingStage.LEXICAL, score=9.1, rank=1
                ),
                RankedCandidate(
                    passage_id=passage_a, stage=RankingStage.VECTOR, score=0.91, rank=1
                ),
                RankedCandidate(
                    passage_id=passage_b, stage=RankingStage.VECTOR, score=0.95, rank=0
                ),
                RankedCandidate(
                    passage_id=passage_a, stage=RankingStage.FUSED, score=0.033, rank=0
                ),
                RankedCandidate(
                    passage_id=passage_b, stage=RankingStage.FUSED, score=0.031, rank=1
                ),
                RankedCandidate(
                    passage_id=passage_a, stage=RankingStage.RERANKED, score=0.87, rank=0
                ),
            ],
            versions=VersionSet(
                extraction_version_ids=(uuid4(),),
                chunking_version_id=uuid4(),
                embedding_version_id=uuid4(),
                embedding_endpoint_version_id=uuid4(),
                reranker_endpoint_version_id=uuid4(),
                generator_endpoint_version_id=uuid4(),
                prompt_version_ids=(uuid4(), uuid4()),
                retrieval_policy_version_id=uuid4(),
            ),
            latencies=[
                StageLatency(stage="lexical", duration_ms=12.3),
                StageLatency(stage="vector", duration_ms=45.0),
                StageLatency(stage="rerank", duration_ms=230.7),
            ],
        )
        run = run.transition(
            QueryStatus.SUCCEEDED,
            response=response,
            selected_evidence_ids=(passage_a,),
            verification=VerificationResult(
                total_claims=1,
                supported_claims=1,
                citation_coverage=1.0,
                action=VerificationAction.ACCEPTED,
                iterations=1,
            ),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.create(run)
            loaded = await repo.get(run.id)
        assert loaded == run
        assert loaded is not None
        assert {c.stage for c in loaded.candidates} == set(RankingStage)

    async def test_save_with_unknown_id_fails(self, db: Database) -> None:
        """R05: save() de ID inexistente não retorna sucesso."""
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            with pytest.raises(NotFoundError):
                await AnswerRunsRepository(conn).save(run)

    async def test_repository_revalidates_model_copy_bypass(self, db: Database) -> None:
        """RR02: model_copy(update=...) não executa validators; o repository
        revalida o dump completo e rejeita o estado inválido antes do SQL."""
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            created = await repo.create(run)
            bypassed = created.model_copy(update={"status": QueryStatus.SUCCEEDED})
            with pytest.raises(ValidationError):
                await repo.save(bypassed)

    async def test_save_rejects_changed_question(self, db: Database) -> None:
        """RRR01: cópia estruturalmente válida com pergunta alterada é rejeitada."""
        run = AnswerRun(
            question_original="original",
            question_anonymized="original",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            created = await repo.create(run)
            for field in ("question_original", "question_anonymized"):
                tampered = created.model_copy(update={field: "alterada"})
                with pytest.raises(ConflictError, match="imutáveis"):
                    await repo.save(tampered)

    async def test_save_rejects_changed_explicit_filters(self, db: Database) -> None:
        """RRR01: filtros explícitos do pedido original não podem mudar."""
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            created = await repo.create(run)
            tampered = created.model_copy(
                update={"explicit_filters": EditionFilter(include_edition_ids=frozenset({uuid4()}))}
            )
            with pytest.raises(ConflictError, match="imutáveis"):
                await repo.save(tampered)

    async def test_save_persists_allowed_progress_fields(self, db: Database) -> None:
        """RRR01: campos da allowlist de progresso continuam persistindo."""
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            created = await repo.create(run)
            progressed = created.transition(
                QueryStatus.RUNNING,
                rewritten_query="q reescrita",
                candidates=[
                    RankedCandidate(
                        passage_id=uuid4(), stage=RankingStage.LEXICAL, score=1.0, rank=0
                    )
                ],
                versions=VersionSet(chunking_version_id=uuid4()),
                latencies=[StageLatency(stage="lexical", duration_ms=3.2)],
            )
            saved = await repo.save(progressed)
            loaded = await repo.get(created.id)
        assert loaded is not None
        assert loaded == saved
        assert saved.revision == progressed.revision + 1
        assert loaded.rewritten_query == "q reescrita"
        assert loaded.question_original == "q"

    async def test_db_rejects_terminal_status_regression(self, db: Database) -> None:
        """R4-01: trigger do banco impede regressão de estado terminal mesmo
        fora do repository."""
        async with db.connection() as conn:
            await conn.execute(
                "INSERT INTO answer_runs (id, status, question_original, "
                "question_anonymized, explicit_filters, response) VALUES "
                "(gen_random_uuid(), 'succeeded', 'q', 'q', '{}'::jsonb, "
                '\'{"answer_markdown": "x", "abstained": false}\'::jsonb)'
            )
            with pytest.raises(psycopg.errors.RaiseException, match="terminal"):
                await conn.execute(
                    "UPDATE answer_runs SET status = 'running' WHERE status = 'succeeded'"
                )


class TestAnswerRunConcurrency:
    """R4-01: controle otimista de concorrência em answer_runs (AC-15)."""

    async def _running_run_and_two_views(self, db: Database) -> tuple[AnswerRun, AnswerRun]:
        """Cria um run em RUNNING e retorna duas leituras independentes."""
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            created = await repo.create(run)
            await repo.save(created.transition(QueryStatus.RUNNING))
            first = await repo.get(created.id)
            second = await repo.get(created.id)
        assert first is not None
        assert second is not None
        assert first.revision == second.revision
        return first, second

    @staticmethod
    def _succeeded(run: AnswerRun) -> AnswerRun:
        return run.transition(QueryStatus.SUCCEEDED, response=QuoteResponse())

    async def test_stale_save_does_not_revert_succeeded(self, db: Database) -> None:
        winner, stale = await self._running_run_and_two_views(db)
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.save(self._succeeded(winner))
            with pytest.raises(ConcurrencyError):
                await repo.save(stale.transition(QueryStatus.RUNNING))
            loaded = await repo.get(winner.id)
        assert loaded is not None
        assert loaded.status is QueryStatus.SUCCEEDED

    @pytest.mark.parametrize(
        ("terminal", "kwargs"),
        [
            (QueryStatus.ABSTAINED, {}),
            (QueryStatus.FAILED, {"error_code": ErrorCode.INTERNAL_ERROR}),
            (QueryStatus.CANCELLED, {}),
        ],
    )
    async def test_stale_save_does_not_revert_other_terminal_states(
        self, db: Database, terminal: QueryStatus, kwargs: dict[str, object]
    ) -> None:
        winner, stale = await self._running_run_and_two_views(db)
        if terminal is QueryStatus.ABSTAINED:
            kwargs["response"] = GeneratedAnswer(
                answer_markdown="", abstained=True, abstention_reason="sem evidências"
            )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.save(winner.transition(terminal, **kwargs))
            with pytest.raises(ConcurrencyError):
                await repo.save(stale.transition(QueryStatus.RUNNING))
            loaded = await repo.get(winner.id)
        assert loaded is not None
        assert loaded.status is terminal

    async def test_concurrent_conclusions_have_exactly_one_winner(self, db: Database) -> None:
        first, second = await self._running_run_and_two_views(db)
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.save(self._succeeded(first))
            with pytest.raises(ConcurrencyError):
                await repo.save(self._succeeded(second))
            loaded = await repo.get(first.id)
        assert loaded is not None
        assert loaded.status is QueryStatus.SUCCEEDED
        assert loaded.revision == first.revision + 1

    async def test_stale_update_does_not_drop_progress(self, db: Database) -> None:
        winner, stale = await self._running_run_and_two_views(db)
        candidate = RankedCandidate(
            passage_id=uuid4(), stage=RankingStage.LEXICAL, score=1.0, rank=0
        )
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.save(
                winner.transition(
                    QueryStatus.RUNNING,
                    candidates=[candidate],
                    latencies=[StageLatency(stage="lexical", duration_ms=1.5)],
                    versions=VersionSet(chunking_version_id=uuid4()),
                )
            )
            with pytest.raises(ConcurrencyError):
                await repo.save(stale.transition(QueryStatus.RUNNING))
            loaded = await repo.get(winner.id)
        assert loaded is not None
        assert loaded.candidates == (candidate,)
        assert len(loaded.latencies) == 1
        assert loaded.versions.chunking_version_id is not None

    async def test_concurrency_error_is_distinct_from_not_found(self, db: Database) -> None:
        assert not issubclass(ConcurrencyError, NotFoundError)
        winner, stale = await self._running_run_and_two_views(db)
        async with db.connection() as conn:
            repo = AnswerRunsRepository(conn)
            await repo.save(self._succeeded(winner))
            with pytest.raises(ConcurrencyError) as exc_info:
                await repo.save(stale.transition(QueryStatus.RUNNING))
            assert not isinstance(exc_info.value, NotFoundError)
            unknown = AnswerRun(
                question_original="q",
                question_anonymized="q",
                explicit_filters=EditionFilter(),
            )
            with pytest.raises(NotFoundError):
                await repo.save(unknown)

    async def test_db_rejects_succeeded_without_response(self, db: Database) -> None:
        """R05: CHECK do banco impõe coerência terminal mesmo fora do domínio."""
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO answer_runs (id, status, question_original, "
                    "question_anonymized, explicit_filters) VALUES "
                    "(gen_random_uuid(), 'succeeded', 'q', 'q', '{}'::jsonb)"
                )

    async def test_db_rejects_failed_without_error_code(self, db: Database) -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO answer_runs (id, status, question_original, "
                    "question_anonymized, explicit_filters) VALUES "
                    "(gen_random_uuid(), 'failed', 'q', 'q', '{}'::jsonb)"
                )

    async def test_db_rejects_abstained_without_abstained_response(self, db: Database) -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as conn:
                await conn.execute(
                    "INSERT INTO answer_runs (id, status, question_original, "
                    "question_anonymized, explicit_filters, response) VALUES "
                    "(gen_random_uuid(), 'abstained', 'q', 'q', '{}'::jsonb, "
                    '\'{"answer_markdown": "x", "abstained": false}\'::jsonb)'
                )
