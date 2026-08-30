"""Repository de passagens com validação de dimensão de embedding.

A dimensão do vetor é conferida contra `embedding_versions.dimensions` ANTES de
persistir (fail-closed); o banco também impõe a dimensão da coluna vector(N).
"""

from uuid import UUID

from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row

from rag.domain.context import CitablePassage
from rag.domain.errors import EmbeddingDimensionError, NotFoundError
from rag.domain.library import Passage


class PassagesRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def _expected_dimensions(self, embedding_version_id: UUID) -> int:
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT dimensions FROM embedding_versions WHERE id = %s",
                (embedding_version_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(
                "Versão de embedding desconhecida.",
                context={"embedding_version_id": str(embedding_version_id)},
            )
        return int(row[0])

    async def create(self, passage: Passage, embedding: list[float] | None = None) -> Passage:
        if embedding is not None:
            if passage.embedding_version_id is None:
                raise EmbeddingDimensionError("Passagem com embedding exige embedding_version_id.")
            expected = await self._expected_dimensions(passage.embedding_version_id)
            if len(embedding) != expected:
                raise EmbeddingDimensionError(
                    "Dimensão do embedding diverge da versão registrada.",
                    context={"expected": expected, "actual": len(embedding)},
                )
        try:
            async with self._conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO passages (id, edition_id, section_id, page_start_id,
                                          page_end_id, ordinal, text, token_count,
                                          char_start, char_end, context_header,
                                          parent_passage_id, embedding,
                                          embedding_version_id, chunking_version_id)
                    VALUES (%(id)s, %(edition_id)s, %(section_id)s, %(page_start_id)s,
                            %(page_end_id)s, %(ordinal)s, %(text)s, %(token_count)s,
                            %(char_start)s, %(char_end)s, %(context_header)s,
                            %(parent_passage_id)s, %(embedding)s,
                            %(embedding_version_id)s, %(chunking_version_id)s)
                    """,
                    {
                        "id": passage.id,
                        "edition_id": passage.edition_id,
                        "section_id": passage.section_id,
                        "page_start_id": passage.page_start_id,
                        "page_end_id": passage.page_end_id,
                        "ordinal": passage.ordinal,
                        "text": passage.text,
                        "token_count": passage.token_count,
                        "char_start": passage.char_start,
                        "char_end": passage.char_end,
                        "context_header": passage.context_header,
                        "parent_passage_id": passage.parent_passage_id,
                        "embedding": embedding,
                        "embedding_version_id": passage.embedding_version_id,
                        "chunking_version_id": passage.chunking_version_id,
                    },
                )
        except errors.DataError as exc:
            raise EmbeddingDimensionError(cause=exc) from exc
        return passage

    async def get(self, passage_id: UUID) -> Passage | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, section_id, page_start_id, page_end_id, ordinal, "
                "text, token_count, char_start, char_end, context_header, "
                "parent_passage_id, embedding_version_id, chunking_version_id "
                "FROM passages WHERE id = %s",
                (passage_id,),
            )
            row = await cur.fetchone()
        return Passage(**row) if row else None

    async def get_citable(self, passage_id: UUID) -> CitablePassage | None:
        """Passagem com metadados citáveis (T12; AC-03).

        Resolve num único JOIN as referencias de origem: seção (path), página
        física e rótulo impresso do início, obra (`work_id`) e a passagem-pai
        (`parent_text` para expansão de contexto, NUNCA citável — SPEC §7.3).
        EPUB sem páginas: `physical_page`/`printed_label`/offsets ficam nulos
        (NOTES.md §10.6 item 3). Toda condição é parametrizada.
        """
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT p.id AS passage_id, "
                "p.edition_id, e.work_id AS work_id, p.text, "
                "COALESCE(s.path, ARRAY[]::text[]) AS section_path, "
                "pstart.physical_index AS physical_page, "
                "pstart.printed_label AS printed_label, "
                "p.char_start, p.char_end, p.parent_passage_id, "
                "parent.text AS parent_text "
                "FROM passages p "
                "JOIN editions e ON e.id = p.edition_id "
                "LEFT JOIN sections s ON s.id = p.section_id "
                "  AND s.edition_id = p.edition_id "
                "LEFT JOIN pages pstart ON pstart.id = p.page_start_id "
                "  AND pstart.edition_id = p.edition_id "
                "LEFT JOIN passages parent ON parent.id = p.parent_passage_id "
                "  AND parent.edition_id = p.edition_id "
                "WHERE p.id = %s",
                (passage_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return CitablePassage(**row)

    async def list_by_edition(self, edition_id: UUID) -> list[Passage]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, section_id, page_start_id, page_end_id, ordinal, "
                "text, token_count, char_start, char_end, context_header, "
                "parent_passage_id, embedding_version_id, chunking_version_id "
                "FROM passages WHERE edition_id = %s ORDER BY ordinal",
                (edition_id,),
            )
            return [Passage(**row) for row in await cur.fetchall()]

    async def delete_by_edition(self, edition_id: UUID) -> int:
        """Remove todas as passagens da edição (pais e filhos juntos, numa
        única instrução — seguro mesmo com a FK auto-referencial de
        `parent_passage_id`). Usado por `rag index --force` (T06)."""
        async with self._conn.cursor() as cur:
            await cur.execute("DELETE FROM passages WHERE edition_id = %s", (edition_id,))
            return cur.rowcount
