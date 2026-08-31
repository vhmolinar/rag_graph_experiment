"""Rotas do acervo (SPEC §10.2): obras, edições, passagens e artefato de origem
com suporte a range requests."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from rag.api.deps import AppDependencies
from rag.api.schemas import (
    ContributorOut,
    EditionDetail,
    EditionOut,
    PassageDetail,
    WorkDetail,
    WorkSummary,
)
from rag.domain.errors import NotFoundError
from rag.domain.library import Edition
from rag.infrastructure.artifacts import ArtifactMetadata, ArtifactStore
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.works import WorksRepository

router = APIRouter(tags=["catalog"])

_CHUNK = 64 * 1024


def _deps(request: Request) -> AppDependencies:
    return request.app.state.deps  # type: ignore[no-any-return]


@router.get("/works", response_model=list[WorkSummary])
async def list_works(request: Request) -> list[WorkSummary]:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        works = await WorksRepository(conn).list_all()
    return [
        WorkSummary(
            work_id=work.id,
            canonical_title=work.canonical_title,
            original_title=work.original_title,
            authors=[
                ContributorOut(name=author.name, role=author.role.value) for author in work.authors
            ],
            language=work.language,
        )
        for work in works
    ]


@router.get("/works/{work_id}", response_model=WorkDetail)
async def get_work(work_id: UUID, request: Request) -> WorkDetail:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        work = await WorksRepository(conn).get(work_id)
        if work is None:
            raise NotFoundError("Obra não encontrada.", context={"work_id": str(work_id)})
        editions = await EditionsRepository(conn).list_by_work(work_id)
    return WorkDetail(
        work_id=work.id,
        canonical_title=work.canonical_title,
        original_title=work.original_title,
        authors=[ContributorOut(name=a.name, role=a.role.value) for a in work.authors],
        language=work.language,
        editions=[_edition_out(edition) for edition in editions],
    )


@router.get("/editions/{edition_id}", response_model=EditionDetail)
async def get_edition(edition_id: UUID, request: Request) -> EditionDetail:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        edition = await EditionsRepository(conn).get(edition_id)
        if edition is None:
            raise NotFoundError("Edição não encontrada.", context={"edition_id": str(edition_id)})
        work = await WorksRepository(conn).get(edition.work_id)
    return EditionDetail(
        edition=_edition_out(edition),
        work_title=work.canonical_title if work is not None else None,
    )


@router.get("/editions/{edition_id}/passages/{passage_id}", response_model=PassageDetail)
async def get_passage(edition_id: UUID, passage_id: UUID, request: Request) -> PassageDetail:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        edition = await EditionsRepository(conn).get(edition_id)
        if edition is None:
            raise NotFoundError("Edição não encontrada.", context={"edition_id": str(edition_id)})
        citable = await PassagesRepository(conn).get_citable(passage_id)
    if citable is None or citable.edition_id != edition_id:
        raise NotFoundError(
            "Passagem não encontrada na edição.",
            context={"edition_id": str(edition_id), "passage_id": str(passage_id)},
        )
    return PassageDetail(
        passage_id=citable.passage_id,
        edition_id=citable.edition_id,
        work_id=citable.work_id,
        text=citable.text,
        section_path=list(citable.section_path),
        physical_page=citable.physical_page,
        printed_label=citable.printed_label,
        char_start=citable.char_start,
        char_end=citable.char_end,
    )


@router.get("/editions/{edition_id}/source")
async def get_source(edition_id: UUID, request: Request) -> Response:
    """Serve o artefato de origem da edição, com suporte a range requests
    (SPEC §10.2). O I/O do `ArtifactStore` é síncrono → `asyncio.to_thread`."""
    deps = _deps(request)
    async with deps.db.connection() as conn:
        edition = await EditionsRepository(conn).get(edition_id)
        if edition is None:
            raise NotFoundError("Edição não encontrada.", context={"edition_id": str(edition_id)})
    store: ArtifactStore = deps.store
    metadata: ArtifactMetadata = await asyncio.to_thread(store.metadata, edition.source_sha256)
    total = metadata.size_bytes
    media_type = metadata.media_type.value

    range_header = request.headers.get("range")
    parsed = _parse_range(range_header, total) if range_header else None

    if parsed is None:
        if range_header:
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{total}",
                    "Accept-Ranges": "bytes",
                },
            )
        return StreamingResponse(
            _stream(store, edition.source_sha256, 0),
            media_type=media_type,
            headers={
                "Content-Length": str(total),
                "Accept-Ranges": "bytes",
            },
        )

    start, end = parsed
    length = end - start + 1
    return StreamingResponse(
        _stream(store, edition.source_sha256, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Length": str(length),
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Accept-Ranges": "bytes",
        },
    )


def _edition_out(edition: Edition) -> EditionOut:
    return EditionOut(
        edition_id=edition.id,
        work_id=edition.work_id,
        title=edition.title,
        source_type=edition.source_type.value,
        publisher=edition.publisher,
        publication_year=edition.publication_year,
        edition_label=edition.edition_label,
        license_status=edition.license_status.value,
        ingestion_status=edition.ingestion_status.value,
    )


def _parse_range(header: str, total: int) -> tuple[int, int] | None:
    """Analiza `Range: bytes=...` (uma única faixa).

    Retorna None se o header não for um range válido (o chamador decide entre
    servidor completo e 416). Suporta `start-end`, `start-` e `-suffix`."""
    if not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :].strip()
    if "," in spec:
        return None  # multipart não suportado; o chamador responde 416
    if "-" not in spec:
        return None
    start_str, end_str = spec.split("-", 1)
    try:
        if not start_str:
            suffix = int(end_str)
            if suffix <= 0:
                return None
            start = max(0, total - suffix)
            return start, total - 1
        start = int(start_str)
        end = int(end_str) if end_str else total - 1
    except ValueError:
        return None
    if start < 0 or start >= total:
        return None
    end = min(end, total - 1)
    if end < start:
        return None
    return start, end


async def _stream(
    store: ArtifactStore, sha256: str, offset: int, length: int | None = None
) -> AsyncIterator[bytes]:
    """Streama o artefato desde `offset`, em chunks, via `to_thread` (I/O
    síncrono do store — NOTAS.md §10.15 item 10)."""
    remaining = length
    stream = await asyncio.to_thread(store.open_stream, sha256, offset)
    try:
        while remaining is None or remaining > 0:
            read_bytes = _CHUNK if remaining is None else min(_CHUNK, remaining)
            chunk = await asyncio.to_thread(stream.read, read_bytes)
            if not chunk:
                break
            yield chunk
            if remaining is not None:
                remaining -= len(chunk)
    finally:
        await asyncio.to_thread(stream.close)
