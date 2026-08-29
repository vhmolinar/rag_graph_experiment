"""CLI `rag` (T05; SPEC §7.1): ingest, ocr, inspect.

Contratos:
- `--dry-run` valida arquivo, metadados, idioma e duplicidade sem persistir;
- falhas retornam código != 0 (1: erro de domínio; 2: erro inesperado);
- logs estruturados JSON (structlog) em stderr (fábrica explícita — nunca
  stdout), sem conteúdo do livro e sem caminhos absolutos ou stack traces —
  apenas nomes de arquivo, ids e `error_type` (T5-10). O traceback completo
  só é gravado se `RAG_DEBUG_LOG` apontar para um arquivo: destino restrito
  (0600), com segredos comuns redigidos e nunca propagando falha própria
  (correção R5-10 — configuração de debug não suspende a regra de redigir
  segredos, nem pode mascarar a exceção original se o próprio sink falhar);
- reexecução com os mesmos inputs é idempotente (AC-01).

Configuração por ambiente: POSTGRES_* (banco) e ARTIFACT_* (armazenamento).
"""

import asyncio
import io
import os
import re
import sys
import traceback
from collections.abc import Callable, MutableMapping
from pathlib import Path
from types import TracebackType
from typing import Annotated, Any
from uuid import UUID

import structlog
import typer

from rag.adapters.docling_adapter import DoclingExtractor
from rag.adapters.embedding_adapter import (
    EmbeddingEndpointSettings,
    OpenAiCompatibleEmbeddingProvider,
)
from rag.adapters.ocr_adapter import DoclingOcrEngine, OcrEngine, ocr_pdf
from rag.application.index import IndexingService
from rag.application.ingest import IngestionService, load_metadata
from rag.domain.chunking import ChunkingParams
from rag.domain.errors import RagError
from rag.domain.providers import EmbeddingProvider
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.config import ChunkingSettings, DatabaseSettings, StorageSettings
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.works import WorksRepository
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

_DEBUG_LOG_ENV = "RAG_DEBUG_LOG"

# Redação best-effort de segredos comuns no traceback de debug — configuração
# de debug não é licença para vazar credenciais (correção R5-10).
_KV_SECRET = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[=:]\s*)(\S+)")
_AUTH_HEADER = re.compile(r"(?i)\b(authorization)\s*:\s*(\S+)")


def _redact_secrets(text: str) -> str:
    text = _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", text)
    return _AUTH_HEADER.sub(lambda m: f"{m.group(1)}: ***REDACTED***", text)


def _write_debug_log(
    debug_path: str,
    event_name: str,
    exc_type: type[BaseException],
    exc_value: BaseException | None,
    tb: TracebackType | None,
) -> None:
    """Grava o traceback completo em `debug_path`. Melhor esforço: qualquer
    falha aqui (permissão, disco cheio, caminho inválido) é engolida — o
    sink de debug nunca pode mascarar a exceção original sendo logada.

    Arquivo criado com permissão restrita (0600, via `os.open` — não depende
    do umask do processo) e o conteúdo passa pela mesma redação de segredos
    comuns aplicada a qualquer outro log (correção R5-10).
    """
    try:
        tb_text = io.StringIO()
        traceback.print_exception(exc_type, exc_value, tb, file=tb_text)
        content = _redact_secrets(f"=== {event_name} ===\n{tb_text.getvalue()}")
        fd = os.open(debug_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(content)
    except OSError:
        pass


def _redact_exception(
    _logger: Any,  # noqa: ANN401 -- assinatura de Processor do structlog
    _name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Remove o traceback do log de console; opcionalmente grava-o à parte.

    `log.exception()`/`log.error(..., exc_info=True)` marcam `exc_info` no
    event dict. O console nunca recebe o traceback (que pode conter
    caminhos absolutos e trechos de código) — apenas `error_type`. O
    traceback completo só é escrito em `RAG_DEBUG_LOG`, quando configurado.
    """
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return event_dict
    if exc_info is True:
        exc_type, exc_value, tb = sys.exc_info()
    else:
        exc_type, exc_value, tb = exc_info
    event_dict["error_type"] = exc_type.__name__ if exc_type is not None else "desconhecido"
    debug_path = os.environ.get(_DEBUG_LOG_ENV)
    if debug_path and exc_type is not None:
        _write_debug_log(debug_path, str(event_dict.get("event", "")), exc_type, exc_value, tb)
    return event_dict


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_exception,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
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
            passages = await PassagesRepository(conn).list_by_edition(edition.id)
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
    children = sum(1 for p in passages if p.embedding_version_id is not None)
    typer.echo(
        f"  seções: {len(sections)}  páginas: {len(pages)}  "
        f"passagens: {len(passages)} (pais={len(passages) - children} filhos={children})"
    )
    for derived in edition.derived_artifacts:
        typer.echo(f"  derivado[{derived.kind}]: {derived.sha256} via {derived.generator}")
    for warning in edition.extraction_warnings:
        typer.echo(f"  aviso: {warning}")
    return 0


def _default_embedding_provider() -> EmbeddingProvider:
    return OpenAiCompatibleEmbeddingProvider(EmbeddingEndpointSettings())


def _resolve_chunking_params(overrides: dict[str, int | None]) -> ChunkingParams:
    """Env (`CHUNKING_*`) primeiro; opções explícitas de CLI sobrepõem (T6-07)."""
    base = ChunkingSettings().model_dump()
    for key, value in overrides.items():
        if value is not None:
            base[key] = value
    return ChunkingParams(**base)


async def _index_async(
    edition_id: UUID,
    force: bool,
    embedding_provider_factory: Callable[[], EmbeddingProvider],
    chunking_overrides: dict[str, int | None],
) -> tuple[int, ChunkingParams]:
    storage = StorageSettings()
    store = ArtifactStore(storage.root, max_size_bytes=storage.max_size_bytes)
    embedding_provider = embedding_provider_factory()
    embedding_settings = EmbeddingEndpointSettings()
    chunking_params = _resolve_chunking_params(chunking_overrides)
    service = IndexingService(store, DoclingExtractor(), embedding_provider)
    db = Database(DatabaseSettings())
    await db.open()
    try:
        async with db.connection() as conn:
            report = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=chunking_params,
                embedding_model_name=embedding_settings.model,
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                force=force,
                batch_size=embedding_settings.batch_size,
                embedding_endpoint=embedding_settings.base_url,
                embedding_model_revision=embedding_settings.model_revision,
            )
    finally:
        aclose = getattr(embedding_provider, "aclose", None)
        if aclose is not None:
            await aclose()
        await db.close()
    mode = "criada" if report.created else "existente"
    typer.echo(
        f"indexação[{mode}] edição={report.edition_id} execução={report.index_run_id} "
        f"pais={report.parents} filhos={report.children} "
        f"chunking_version={report.chunking_version_id} "
        f"embedding_version={report.embedding_version_id or '-'}"
    )
    return 0, chunking_params


def create_app(
    engine_factory: Callable[[str], OcrEngine] = DoclingOcrEngine,
    embedding_provider_factory: Callable[[], EmbeddingProvider] = _default_embedding_provider,
) -> typer.Typer:
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
    def index(
        edition_id: Annotated[str, typer.Argument(help="UUID da edição")],
        force: Annotated[
            bool,
            typer.Option(
                "--force", help="minta uma nova execução mesmo com a mesma identidade de versões"
            ),
        ] = False,
        parent_tokens: Annotated[
            int | None,
            typer.Option(
                "--parent-tokens",
                help="tokens-alvo por janela pai (padrão: CHUNKING_PARENT_TARGET_TOKENS)",
            ),
        ] = None,
        child_tokens: Annotated[
            int | None,
            typer.Option(
                "--child-tokens",
                help="tokens-alvo por janela filha (padrão: CHUNKING_CHILD_TARGET_TOKENS)",
            ),
        ] = None,
        child_overlap: Annotated[
            int | None,
            typer.Option(
                "--child-overlap",
                help="sobreposição entre janelas filhas (padrão: CHUNKING_CHILD_OVERLAP_TOKENS)",
            ),
        ] = None,
    ) -> None:
        """Chunking estrutural + embeddings em lote para uma edição já ingerida."""
        try:
            parsed = UUID(edition_id)
        except ValueError:
            typer.secho("erro: edition-id deve ser um UUID.", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None
        chunking_overrides: dict[str, int | None] = {
            "parent_target_tokens": parent_tokens,
            "child_target_tokens": child_tokens,
            "child_overlap_tokens": child_overlap,
        }
        log = structlog.get_logger()
        log.info("index.started", edition_id=str(parsed), force=force)
        try:
            code, resolved_params = asyncio.run(
                _index_async(parsed, force, embedding_provider_factory, chunking_overrides)
            )
        except RagError as exc:
            log.info("index.failed", edition_id=str(parsed), error=exc.message)
            _echo_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            log.exception("index.unexpected_error", edition_id=str(parsed))
            typer.secho(f"erro inesperado: {type(exc).__name__}", err=True)
            raise typer.Exit(code=2) from exc
        log.info(
            "index.finished",
            edition_id=str(parsed),
            force=force,
            **resolved_params.model_dump(),
        )
        raise typer.Exit(code=code)

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
