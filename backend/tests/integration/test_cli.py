"""CLI `rag` contra PostgreSQL real (T05; SPEC §7.1; AC-01).

O banco é o container de testcontainers; as variáveis POSTGRES_* e
ARTIFACT_ROOT são injetadas via `env` do CliRunner — o mesmo caminho de
configuração usado em operação real.
"""

import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub

from rag.cli.main import create_app
from rag.domain.versions import EmbeddingVersion, utcnow
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

pytestmark = pytest.mark.integration


class _FakeEmbeddingProvider:
    """Determinístico, sem rede — usado para exercitar `rag index` sem T07."""

    @property
    def embedding_version(self) -> EmbeddingVersion:
        return EmbeddingVersion(
            label="cli-fake-embedding",
            model_name="cli-fake-embedding",
            dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            created_at=utcnow(),
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] * EMBEDDING_COLUMN_DIMENSIONS for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * EMBEDDING_COLUMN_DIMENSIONS


@pytest.fixture
def cli_env(migrated: Any, tmp_path: Path, db: object) -> dict[str, str]:
    # `db` entra só pela limpeza: seu teardown trunca as tabelas entre testes.
    return {
        "POSTGRES_HOST": migrated["host"],
        "POSTGRES_PORT": str(migrated["port"]),
        "POSTGRES_DB": migrated["db"],
        "POSTGRES_USER": migrated["user"],
        "POSTGRES_PASSWORD": migrated["password"],
        "ARTIFACT_ROOT": str(tmp_path / "artifacts"),
    }


@pytest.fixture
def book(tmp_path: Path) -> tuple[Path, Path]:
    epub = tmp_path / "livro.epub"
    epub.write_bytes(
        make_epub(
            [
                ("Capítulo I", ["Primeira lição do livro."]),
                ("Capítulo II", ["Segunda parte."]),
            ]
        )
    )
    meta = tmp_path / "livro.yaml"
    meta.write_text(
        "title: Livro CLI\nauthors: [Autora Fixture]\nedition_label: 1ª ed.\n",
        encoding="utf-8",
    )
    return epub, meta


class TestIngestCommand:
    def test_ingest_then_inspect(self, cli_env: dict[str, str], book: tuple[Path, Path]) -> None:
        runner = CliRunner()
        app = create_app()
        epub, meta = book

        result = runner.invoke(app, ["ingest", str(epub), "--metadata", str(meta)], env=cli_env)
        assert result.exit_code == 0, result.output
        assert "criada" in result.output
        assert "seções=2" in result.output

        edition_id = result.output.split("id=")[1].split()[0]
        inspect = runner.invoke(app, ["inspect", edition_id], env=cli_env)
        assert inspect.exit_code == 0, inspect.output
        assert "Livro CLI" in inspect.output
        assert "íntegro" in inspect.output
        assert "seções: 2" in inspect.output

    def test_reingest_idempotent_exit_zero(
        self, cli_env: dict[str, str], book: tuple[Path, Path]
    ) -> None:
        runner = CliRunner()
        app = create_app()
        epub, meta = book
        args = ["ingest", str(epub), "--metadata", str(meta)]
        first = runner.invoke(app, args, env=cli_env)
        second = runner.invoke(app, args, env=cli_env)
        assert first.exit_code == 0
        assert second.exit_code == 0
        assert "criada" in first.output
        assert "existente" in second.output

    def test_dry_run_persists_nothing(
        self, cli_env: dict[str, str], book: tuple[Path, Path]
    ) -> None:
        runner = CliRunner()
        app = create_app()
        epub, meta = book
        result = runner.invoke(
            app, ["ingest", str(epub), "--metadata", str(meta), "--dry-run"], env=cli_env
        )
        assert result.exit_code == 0, result.output
        assert "dry-run" in result.output
        assert "id=-" in result.output

    def test_invalid_metadata_exit_one(
        self, cli_env: dict[str, str], book: tuple[Path, Path], tmp_path: Path
    ) -> None:
        runner = CliRunner()
        app = create_app()
        epub, _meta = book
        bad = tmp_path / "bad.yaml"
        bad.write_text("language: en\n", encoding="utf-8")
        result = runner.invoke(app, ["ingest", str(epub), "--metadata", str(bad)], env=cli_env)
        assert result.exit_code == 1
        assert "erro:" in result.output

    def test_inspect_unknown_edition_exit_one(self, cli_env: dict[str, str]) -> None:
        runner = CliRunner()
        app = create_app()
        result = runner.invoke(
            app,
            ["inspect", "00000000-0000-0000-0000-000000000000"],
            env=cli_env,
        )
        assert result.exit_code == 1
        assert "não encontrada" in result.output

    def test_backfill_fingerprint_command_is_idempotent(
        self,
        cli_env: dict[str, str],
        book: tuple[Path, Path],
    ) -> None:
        runner = CliRunner()
        app = create_app()
        epub, meta = book
        ingest = runner.invoke(app, ["ingest", str(epub), "--metadata", str(meta)], env=cli_env)
        assert ingest.exit_code == 0, ingest.output
        edition_id = ingest.output.split("id=")[1].split()[0]

        # A mutação síncrona evita cruzar event loops com o CliRunner, que
        # também chama `asyncio.run`; `cli_env` já inclui a fixture de limpeza.
        with psycopg.connect(
            host=cli_env["POSTGRES_HOST"],
            port=cli_env["POSTGRES_PORT"],
            dbname=cli_env["POSTGRES_DB"],
            user=cli_env["POSTGRES_USER"],
            password=cli_env["POSTGRES_PASSWORD"],
        ) as conn:
            conn.execute(
                "UPDATE editions SET canonical_fingerprint = NULL WHERE id = %s",
                (edition_id,),
            )
        first = runner.invoke(app, ["backfill-fingerprint", edition_id], env=cli_env)
        second = runner.invoke(app, ["backfill-fingerprint", edition_id], env=cli_env)
        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert "fingerprint atualizado" in first.output
        assert "fingerprint atualizado" in second.output


class TestIndexCommand:
    def test_index_then_inspect_shows_passages(
        self, cli_env: dict[str, str], book: tuple[Path, Path]
    ) -> None:
        runner = CliRunner()
        app = create_app(embedding_provider_factory=_FakeEmbeddingProvider)
        epub, meta = book
        ingest = runner.invoke(app, ["ingest", str(epub), "--metadata", str(meta)], env=cli_env)
        assert ingest.exit_code == 0, ingest.output
        edition_id = ingest.output.split("id=")[1].split()[0]

        result = runner.invoke(app, ["index", edition_id], env=cli_env)
        assert result.exit_code == 0, result.output
        assert "indexação[criada]" in result.output
        assert "pais=2" in result.output

        inspect = runner.invoke(app, ["inspect", edition_id], env=cli_env)
        assert inspect.exit_code == 0, inspect.output
        assert "passagens:" in inspect.output
        assert "pais=2" in inspect.output

    def test_reindex_idempotent_without_force(
        self, cli_env: dict[str, str], book: tuple[Path, Path]
    ) -> None:
        runner = CliRunner()
        app = create_app(embedding_provider_factory=_FakeEmbeddingProvider)
        epub, meta = book
        runner.invoke(app, ["ingest", str(epub), "--metadata", str(meta)], env=cli_env)
        ingest = runner.invoke(app, ["ingest", str(epub), "--metadata", str(meta)], env=cli_env)
        edition_id = ingest.output.split("id=")[1].split()[0]

        first = runner.invoke(app, ["index", edition_id], env=cli_env)
        second = runner.invoke(app, ["index", edition_id], env=cli_env)
        assert first.exit_code == 0
        assert second.exit_code == 0
        assert "indexação[criada]" in first.output
        assert "indexação[existente]" in second.output

    def test_index_invalid_uuid_exit_one(self, cli_env: dict[str, str]) -> None:
        runner = CliRunner()
        app = create_app(embedding_provider_factory=_FakeEmbeddingProvider)
        result = runner.invoke(app, ["index", "não-é-uuid"], env=cli_env)
        assert result.exit_code == 1
        assert "UUID" in result.output

    def test_index_unknown_edition_exit_one(self, cli_env: dict[str, str]) -> None:
        runner = CliRunner()
        app = create_app(embedding_provider_factory=_FakeEmbeddingProvider)
        result = runner.invoke(app, ["index", "00000000-0000-0000-0000-000000000000"], env=cli_env)
        assert result.exit_code == 1
        assert "erro:" in result.output
