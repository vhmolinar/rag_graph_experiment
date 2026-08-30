"""Repositories de resumos e conceitos (T11; SPEC §7.4, AC-12).

`SummariesRepository` persiste sínteses + suportes; `ConceptsRepository`
persiste conceitos globais com aliases e evidencias. A recuperação
descendente ("descer até passagens originais", SPEC §8.7) devolve SEMPRE
`Passage` — nunca o texto de síntese/conceito como citação (AC-12). A
integridade referencial é imposta pelo banco: `summary_supports` e
`concept_evidence` apontam a `passages` via FKs compostas (R01).
"""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.enums import ConceptState, SummaryScope
from rag.domain.knowledge import Concept, Summary
from rag.domain.library import Passage

_PASSAGE_COLUMNS = (
    "p.id, p.edition_id, p.section_id, p.page_start_id, p.page_end_id, p.ordinal, "
    "p.text, p.token_count, p.char_start, p.char_end, p.context_header, "
    "p.parent_passage_id, p.embedding_version_id, p.chunking_version_id"
)


def _passage_from_row(row: dict[str, Any]) -> Passage:
    return Passage(**row)


class SummariesRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, summary: Summary) -> None:
        """Insere a síntese e as passagens de suporte (ordinal = posição).

        A FK composta `summary_supports(passage_id, edition_id)` garante que
        cada suporte pertença à edição da síntese — o banco, não só o serviço,
        impõe a integridade (R01).
        """
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO summaries (id, edition_id, scope_type, section_id, text,
                                       generator_version_id, created_at)
                VALUES (%(id)s, %(edition_id)s, %(scope_type)s, %(section_id)s, %(text)s,
                        %(generator_version_id)s, %(created_at)s)
                """,
                {
                    "id": summary.id,
                    "edition_id": summary.edition_id,
                    "scope_type": summary.scope_type.value,
                    "section_id": summary.section_id,
                    "text": summary.text,
                    "generator_version_id": summary.generator_version_id,
                    "created_at": summary.created_at,
                },
            )
            for ordinal, passage_id in enumerate(summary.supporting_passage_ids):
                await cur.execute(
                    """
                    INSERT INTO summary_supports (summary_id, passage_id, edition_id, ordinal)
                    VALUES (%(summary_id)s, %(passage_id)s, %(edition_id)s, %(ordinal)s)
                    """,
                    {
                        "summary_id": summary.id,
                        "passage_id": passage_id,
                        "edition_id": summary.edition_id,
                        "ordinal": ordinal,
                    },
                )

    async def has_for_edition_version(self, edition_id: UUID, generator_version_id: UUID) -> bool:
        """Verdadeiro se a edição já tem sínteses da versão — idempotência."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM summaries "
                "WHERE edition_id = %s AND generator_version_id = %s LIMIT 1",
                (edition_id, generator_version_id),
            )
            return await cur.fetchone() is not None

    async def list_by_edition(self, edition_id: UUID) -> list[Summary]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT s.id, s.edition_id, s.scope_type, s.section_id, s.text, "
                "s.generator_version_id, s.created_at, "
                "COALESCE(array_agg(ss.passage_id ORDER BY ss.ordinal) "
                "FILTER (WHERE ss.passage_id IS NOT NULL), '{}') AS supporting_passage_ids "
                "FROM summaries s "
                "LEFT JOIN summary_supports ss ON ss.summary_id = s.id "
                "WHERE s.edition_id = %s "
                "GROUP BY s.id "
                "ORDER BY s.created_at, s.scope_type, s.section_id",
                (edition_id,),
            )
            rows = await cur.fetchall()
        summaries: list[Summary] = []
        for row in rows:
            row["scope_type"] = SummaryScope(row["scope_type"])
            summaries.append(Summary(**row))
        return summaries

    async def supporting_passages(self, summary_id: UUID) -> list[Passage]:
        """Recuperação descendente: as passagens originais que sustentam a síntese."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_PASSAGE_COLUMNS} FROM summary_supports ss "  # noqa: S608
                "JOIN passages p ON p.id = ss.passage_id "
                "WHERE ss.summary_id = %s ORDER BY ss.ordinal",
                (summary_id,),
            )
            return [_passage_from_row(row) for row in await cur.fetchall()]


class ConceptsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def get_or_create(self, concept: Concept) -> Concept:
        """Idempotente por `normalized_label` (UNIQUE no banco). Se o conceito
        já existir, devolve o registro existente — estado e descrição não são
        sobrescritos (curatoria futura, SPEC §5.1)."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO concepts (id, normalized_label, description, state, created_at)
                VALUES (%(id)s, %(normalized_label)s, %(description)s, %(state)s, %(created_at)s)
                ON CONFLICT (normalized_label) DO NOTHING
                """,
                {
                    "id": concept.id,
                    "normalized_label": concept.normalized_label,
                    "description": concept.description,
                    "state": concept.state.value,
                    "created_at": concept.created_at,
                },
            )
            await cur.execute(
                "SELECT id, normalized_label, description, state, created_at "
                "FROM concepts WHERE normalized_label = %s",
                (concept.normalized_label,),
            )
            row = await cur.fetchone()
        if row is None:  # pragma: no cover - defensivo
            raise RuntimeError("falha ao ler conceito recém-criado")
        row["state"] = ConceptState(row["state"])
        return Concept(**row)

    async def add_alias(self, concept_id: UUID, expression: str, confidence: float) -> None:
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO concept_aliases (concept_id, expression, confidence)
                VALUES (%s, %s, %s)
                ON CONFLICT (concept_id, expression) DO NOTHING
                """,
                (concept_id, expression, confidence),
            )

    async def add_evidence(
        self, concept_id: UUID, passage_id: UUID, confidence: float, extractor_version_id: UUID
    ) -> None:
        """Idempotente por (concept_id, passage_id, extractor_version_id):
        reexecução com a MESMA versão não duplica; com versão NOVA acumula —
        histórico preservado (AC-15)."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO concept_evidence (concept_id, passage_id, confidence,
                                              extractor_version_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (concept_id, passage_id, extractor_version_id) DO NOTHING
                """,
                (concept_id, passage_id, confidence, extractor_version_id),
            )

    async def supporting_passages(self, concept_id: UUID) -> list[Passage]:
        """Recuperação descendente: as passagens originais que sustentam o conceito."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT DISTINCT {_PASSAGE_COLUMNS} FROM concept_evidence ce "  # noqa: S608
                "JOIN passages p ON p.id = ce.passage_id "
                "WHERE ce.concept_id = %s ORDER BY p.ordinal",
                (concept_id,),
            )
            return [_passage_from_row(row) for row in await cur.fetchall()]

    async def count_for_edition(self, edition_id: UUID) -> int:
        """Número de conceitos distintos com evidência na edição."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT count(DISTINCT ce.concept_id) FROM concept_evidence ce "
                "JOIN passages p ON p.id = ce.passage_id WHERE p.edition_id = %s",
                (edition_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    async def count_evidence_for_edition(self, edition_id: UUID) -> int:
        """Número de linhas de evidência apontando a passagens da edição."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM concept_evidence ce "
                "JOIN passages p ON p.id = ce.passage_id WHERE p.edition_id = %s",
                (edition_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    async def list_all(self, *, limit: int = 1000) -> list[Concept]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, normalized_label, description, state, created_at "
                "FROM concepts ORDER BY normalized_label LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        concepts: list[Concept] = []
        for row in rows:
            row["state"] = ConceptState(row["state"])
            concepts.append(Concept(**row))
        return concepts
