"""Repositories de seções e páginas."""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.errors import ConflictError
from rag.domain.library import Page, Section

_SECTION_COMPARE_FIELDS = ("parent_section_id", "level", "title", "path", "start_page", "end_page")
_PAGE_COMPARE_FIELDS = ("printed_label", "text", "text_sha256")


class SectionsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create_many(self, sections: list[Section]) -> None:
        """Idempotente por (edition_id, ordinal): repetição idêntica é aceita;
        mesmo identificador lógico com conteúdo divergente levanta ConflictError (R08).
        """
        async with self._conn.cursor(row_factory=dict_row) as cur:
            for section in sections:
                await cur.execute(
                    """
                    INSERT INTO sections (id, edition_id, parent_section_id, level,
                                          ordinal, title, path, start_page, end_page)
                    VALUES (%(id)s, %(edition_id)s, %(parent_section_id)s, %(level)s,
                            %(ordinal)s, %(title)s, %(path)s, %(start_page)s, %(end_page)s)
                    ON CONFLICT (edition_id, ordinal) DO NOTHING
                    RETURNING id
                    """,
                    {
                        "id": section.id,
                        "edition_id": section.edition_id,
                        "parent_section_id": section.parent_section_id,
                        "level": section.level,
                        "ordinal": section.ordinal,
                        "title": section.title,
                        "path": section.path,
                        "start_page": section.start_page,
                        "end_page": section.end_page,
                    },
                )
                if await cur.fetchone() is not None:
                    continue
                await cur.execute(
                    "SELECT id, edition_id, parent_section_id, level, ordinal, title, "
                    "path, start_page, end_page FROM sections "
                    "WHERE edition_id = %s AND ordinal = %s",
                    (section.edition_id, section.ordinal),
                )
                row = await cur.fetchone()
                if row is None:  # pragma: no cover - o conflito garante a existência
                    raise RuntimeError("conflito de seção sem linha existente")
                existing = Section(**row)
                if any(
                    getattr(existing, f) != getattr(section, f) for f in _SECTION_COMPARE_FIELDS
                ):
                    raise ConflictError(
                        "Seção já existe com conteúdo divergente.",
                        context={
                            "edition_id": str(section.edition_id),
                            "ordinal": section.ordinal,
                        },
                    )

    async def list_by_edition(self, edition_id: UUID) -> list[Section]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, parent_section_id, level, ordinal, title, path, "
                "start_page, end_page FROM sections WHERE edition_id = %s ORDER BY ordinal",
                (edition_id,),
            )
            return [Section(**row) for row in await cur.fetchall()]


class PagesRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create_many(self, pages: list[Page]) -> None:
        """Idempotente por (edition_id, physical_index): repetição idêntica é aceita;
        mesmo identificador lógico com conteúdo divergente levanta ConflictError (R08).
        """
        async with self._conn.cursor(row_factory=dict_row) as cur:
            for page in pages:
                await cur.execute(
                    """
                    INSERT INTO pages (id, edition_id, physical_index, printed_label,
                                       text, text_sha256)
                    VALUES (%(id)s, %(edition_id)s, %(physical_index)s, %(printed_label)s,
                            %(text)s, %(text_sha256)s)
                    ON CONFLICT (edition_id, physical_index) DO NOTHING
                    RETURNING id
                    """,
                    {
                        "id": page.id,
                        "edition_id": page.edition_id,
                        "physical_index": page.physical_index,
                        "printed_label": page.printed_label,
                        "text": page.text,
                        "text_sha256": page.text_sha256,
                    },
                )
                if await cur.fetchone() is not None:
                    continue
                await cur.execute(
                    "SELECT id, edition_id, physical_index, printed_label, text, "
                    "text_sha256 FROM pages "
                    "WHERE edition_id = %s AND physical_index = %s",
                    (page.edition_id, page.physical_index),
                )
                row = await cur.fetchone()
                if row is None:  # pragma: no cover - o conflito garante a existência
                    raise RuntimeError("conflito de página sem linha existente")
                existing = Page(**row)
                if any(getattr(existing, f) != getattr(page, f) for f in _PAGE_COMPARE_FIELDS):
                    raise ConflictError(
                        "Página já existe com conteúdo divergente.",
                        context={
                            "edition_id": str(page.edition_id),
                            "physical_index": page.physical_index,
                        },
                    )

    async def get(self, page_id: UUID) -> Page | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, physical_index, printed_label, text, text_sha256 "
                "FROM pages WHERE id = %s",
                (page_id,),
            )
            row = await cur.fetchone()
        return Page(**row) if row else None

    async def list_by_edition(self, edition_id: UUID) -> list[Page]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, physical_index, printed_label, text, text_sha256 "
                "FROM pages WHERE edition_id = %s ORDER BY physical_index",
                (edition_id,),
            )
            return [Page(**row) for row in await cur.fetchall()]
