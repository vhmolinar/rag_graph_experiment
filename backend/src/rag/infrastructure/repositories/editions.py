"""Repository de edições e artefatos derivados."""

from uuid import UUID, uuid4

from psycopg import AsyncConnection, AsyncCursor, errors
from psycopg.rows import dict_row

from rag.domain.enums import IngestionStatus
from rag.domain.errors import ConflictError
from rag.domain.library import DerivedArtifactRef, Edition


class EditionsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, edition: Edition) -> Edition:
        try:
            async with self._conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO editions (id, work_id, title, publisher, publication_year,
                                          isbn, edition_label, source_type, source_sha256,
                                          license_status, ingestion_status, created_at)
                    VALUES (%(id)s, %(work_id)s, %(title)s, %(publisher)s,
                            %(publication_year)s, %(isbn)s, %(edition_label)s,
                            %(source_type)s, %(source_sha256)s, %(license_status)s,
                            %(ingestion_status)s, %(created_at)s)
                    """,
                    {
                        "id": edition.id,
                        "work_id": edition.work_id,
                        "title": edition.title,
                        "publisher": edition.publisher,
                        "publication_year": edition.publication_year,
                        "isbn": edition.isbn,
                        "edition_label": edition.edition_label,
                        "source_type": edition.source_type.value,
                        "source_sha256": edition.source_sha256,
                        "license_status": edition.license_status.value,
                        "ingestion_status": edition.ingestion_status.value,
                        "created_at": edition.created_at,
                    },
                )
                for artifact in edition.derived_artifacts:
                    await self._insert_derived(cur, edition.id, artifact)
        except errors.UniqueViolation as exc:
            raise ConflictError(
                "Edição duplicada: source_sha256 já existe.",
                cause=exc,
                context={"edition_id": str(edition.id)},
            ) from exc
        return edition

    async def _insert_derived(
        self, cur: AsyncCursor, edition_id: UUID, artifact: DerivedArtifactRef
    ) -> None:
        await cur.execute(
            """
            INSERT INTO derived_artifacts (id, edition_id, sha256, kind,
                                           derived_from_sha256, generator, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                edition_id,
                artifact.sha256,
                artifact.kind.value,
                artifact.derived_from,
                artifact.generator,
                artifact.created_at,
            ),
        )

    async def get(self, edition_id: UUID) -> Edition | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, work_id, title, publisher, publication_year, isbn, "
                "edition_label, source_type, source_sha256, license_status, "
                "ingestion_status, created_at FROM editions WHERE id = %s",
                (edition_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            await cur.execute(
                "SELECT sha256, kind, derived_from_sha256, generator, created_at "
                "FROM derived_artifacts WHERE edition_id = %s ORDER BY created_at",
                (edition_id,),
            )
            artifacts = [
                DerivedArtifactRef(
                    sha256=r["sha256"],
                    kind=r["kind"],
                    derived_from=r["derived_from_sha256"],
                    generator=r["generator"],
                    created_at=r["created_at"],
                )
                for r in await cur.fetchall()
            ]
        return Edition(**(row | {"derived_artifacts": artifacts}))

    async def get_by_source_hash(self, source_sha256: str) -> Edition | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT id FROM editions WHERE source_sha256 = %s", (source_sha256,))
            row = await cur.fetchone()
        if row is None:
            return None
        return await self.get(row["id"])

    async def list_by_work(self, work_id: UUID) -> list[Edition]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id FROM editions WHERE work_id = %s ORDER BY created_at", (work_id,)
            )
            ids = [row["id"] for row in await cur.fetchall()]
        editions: list[Edition] = []
        for edition_id in ids:
            edition = await self.get(edition_id)
            if edition is not None:
                editions.append(edition)
        return editions

    async def update_ingestion_status(self, edition_id: UUID, status: IngestionStatus) -> None:
        async with self._conn.cursor() as cur:
            await cur.execute(
                "UPDATE editions SET ingestion_status = %s WHERE id = %s",
                (status.value, edition_id),
            )
