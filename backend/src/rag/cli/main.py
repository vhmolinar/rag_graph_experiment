"""CLI `rag` (T05; SPEC §7.1): ingest, ocr, inspect.

Contratos:
- `--dry-run` valida arquivo, metadados, idioma e duplicidade sem persistir;
- falhas retornam código != 0 (1: erro de domínio; 2: erro inesperado);
- logs estruturados (structlog) em stderr, sem conteúdo do livro e sem
  caminhos absolutos — apenas nomes de arquivo e ids;
- reexecução com os mesmos inputs é idempotente (AC-01).

Configuração por ambiente: POSTGRES_* (banco) e ARTIFACT_* (armazenamento).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from uuid import UUID

import structlog
import typer

from rag.adapters.docling_adapter import DoclingExtractor
from rag.adapters.ocr_adapter import DoclingOcrEngine, OcrEngine, ocr_pdf
from rag.application.ingest import IngestionService, load_metadata
from rag.domain.errors import RagError
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.config import DatabaseSettings, StorageSettings
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.works import WorksRepository


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.KeyValueRenderer(),
        ],
    )


def _echo_error(exc: RagError) -> None:
    typer.secho(f"erro: {exc.message}", err=True, fg=typer.colors.RED)


async def _ingest_async(file: Path, metadata: Path, dry_run: bool) -> int:
    meta = load_metadata(metadata)
    storage = StorageSettings()
    store = ArtifactStore(storage.root, max_size_bytes=storage.max_size_bytes)
    service = IngestionService(store, DoclingExtractor())
    db = Database(DatabaseSettings())
    await db.open()
    try:
        async with db.connection() as conn:
            report = await service.ingest(conn, file_path=file, metadata=meta, dry_run=dry_run)
    finally:
        await db.close()
    mode = "dry-run" if report.dry_run else ("criada" if report.created else "existente")
    typer.echo(
        f"edição[{mode}] id={report.edition_id or '-'} obra={report.work_id or '-'} "
        f"tipo={report.source_type} seções={report.sections} páginas={report.pages} "
        f"blocos={report.blocks}"
    )
    for warning in report.warnings:
        typer.echo(f"aviso: {warning}")
    return 0


async def _inspect_async(edition_id: UUID) -> int:
    storage = StorageSettings()
    store = ArtifactStore(storage.root, max_size_bytes=storage.max_size_bytes)
    db = Database(DatabaseSettings())
    await db.open()
    try:
        async with db.connection() as conn:
            edition = await EditionsRepository(conn).get(edition_id)
            if edition is None:
                typer.secho("erro: edição não encontrada.", err=True, fg=typer.colors.RED)
                return 1
            work = await WorksRepository(conn).get(edition.work_id)
            sections = await SectionsRepository(conn).list_by_edition(edition.id)
            pages = await PagesRepository(conn).list_by_edition(edition.id)
    finally:
        await db.close()

    try:
        artifact = store.verify_integrity(edition.source_sha256)
        integrity = f"íntegro ({artifact.size_bytes} bytes)"
    except RagError:
        integrity = "FALHA DE INTEGRIDADE"

    typer.echo(f"edição {edition.id}")
    typer.echo(f"  obra: {work.canonical_title if work else '?'} ({edition.work_id})")
    typer.echo(f"  título: {edition.title}")
    typer.echo(f"  tipo: {edition.source_type}  status: {edition.ingestion_status}")
    typer.echo(f"  sha256: {edition.source_sha256}")
    typer.echo(f"  artefato original: {integrity}")
    typer.echo(f"  seções: {len(sections)}  páginas: {len(pages)}")
    for derived in edition.derived_artifacts:
        typer.echo(f"  derivado[{derived.kind}]: {derived.sha256} via {derived.generator}")
    return 0


def create_app(engine_factory: Callable[[str], OcrEngine] = DoclingOcrEngine) -> typer.Typer:
    app = typer.Typer(
        name="rag",
        help="RAG de livros — ingestão, OCR e inspeção (fase 1, local).",
        no_args_is_help=True,
    )

    @app.command()
    def ingest(
        file: Annotated[Path, typer.Argument(help="arquivo .pdf ou .epub")],
        metadata: Annotated[Path, typer.Option("--metadata", help="YAML de metadados")],
        dry_run: Annotated[bool, typer.Option("--dry-run", help="valida sem persistir")] = False,
    ) -> None:
        """Ingere um livro (PDF-texto ou EPUB; escaneado via ocr_artifact)."""
        log = structlog.get_logger()
        log.info("ingest.started", file=file.name, dry_run=dry_run)
        try:
            code = asyncio.run(_ingest_async(file, metadata, dry_run))
        except RagError as exc:
            log.info("ingest.failed", file=file.name, error=exc.message)
            _echo_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            log.exception("ingest.unexpected_error", file=file.name)
            typer.secho(f"erro inesperado: {type(exc).__name__}", err=True)
            raise typer.Exit(code=2) from exc
        log.info("ingest.finished", file=file.name, dry_run=dry_run)
        raise typer.Exit(code=code)

    @app.command()
    def ocr(
        file: Annotated[Path, typer.Argument(help="PDF escaneado")],
        output: Annotated[Path, typer.Option("--output", help="PDF de saída ou diretório")],
        engine: Annotated[
            str, typer.Option("--engine", help="auto|ocrmac|rapidocr|tesseract")
        ] = "auto",
    ) -> None:
        """Gera PDF derivado com camada de texto (não altera o original)."""
        log = structlog.get_logger()
        log.info("ocr.started", file=file.name, engine=engine)
        try:
            report = ocr_pdf(engine_factory(engine), file, output)
        except RagError as exc:
            log.info("ocr.failed", file=file.name, error=exc.message)
            _echo_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            log.exception("ocr.unexpected_error", file=file.name)
            typer.secho(f"erro inesperado: {type(exc).__name__}", err=True)
            raise typer.Exit(code=2) from exc
        log.info("ocr.finished", file=file.name, pages=report.pages)
        typer.echo(
            f"derivado OCR: {report.output_path} sha256={report.sha256} "
            f"páginas={report.pages} linhas={report.lines}"
        )

    @app.command()
    def inspect(
        edition_id: Annotated[str, typer.Argument(help="UUID da edição")],
    ) -> None:
        """Relatório de inspeção: metadados, contagens, artefatos, integridade."""
        try:
            parsed = UUID(edition_id)
        except ValueError:
            typer.secho("erro: edition-id deve ser um UUID.", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None
        try:
            code = asyncio.run(_inspect_async(parsed))
        except RagError as exc:
            _echo_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            structlog.get_logger().exception("inspect.unexpected_error")
            typer.secho(f"erro inesperado: {type(exc).__name__}", err=True)
            raise typer.Exit(code=2) from exc
        raise typer.Exit(code=code)

    return app


_configure_logging()
app = create_app()


def main() -> None:  # entry point do console script
    app()


if __name__ == "__main__":
    main()
