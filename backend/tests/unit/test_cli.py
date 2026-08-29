"""CLI `rag` (T05; SPEC §7.1): códigos de saída e contratos de comando.

Testes de `ocr` usam motor stub injetado via `create_app` — sem modelo nem
rede. `ingest`/`inspect` com banco real estão em tests/integration/test_cli.py.
"""

import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_text_pdf

from rag.adapters.pdf_writer import OcrLine, OcrPage
from rag.cli.main import create_app


class StubEngine:
    name = "stub"

    def recognize(self, pdf_path: Path) -> list[OcrPage]:
        return [
            OcrPage(
                width=612.0,
                height=792.0,
                lines=(OcrLine(text="Texto stub.", x=72.0, y=700.0, height=12.0),),
            )
        ]


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    return create_app(engine_factory=lambda _name: StubEngine())


class TestOcrCommand:
    def test_success_exit_zero(self, cli: CliRunner, app: typer.Typer, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        out = tmp_path / "derivado.pdf"
        result = cli.invoke(app, ["ocr", str(scan), "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert "sha256=" in result.output
        assert out.is_file()

    def test_missing_input_exit_one(self, cli: CliRunner, app: typer.Typer, tmp_path: Path) -> None:
        result = cli.invoke(
            app, ["ocr", str(tmp_path / "ausente.pdf"), "--output", str(tmp_path / "o.pdf")]
        )
        assert result.exit_code == 1
        assert "não encontrado" in result.output

    def test_output_equal_input_exit_one(
        self, cli: CliRunner, app: typer.Typer, tmp_path: Path
    ) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        result = cli.invoke(app, ["ocr", str(scan), "--output", str(scan)])
        assert result.exit_code == 1
        assert "sobrescrever o original" in result.output

    def test_unknown_engine_exit_one(self, cli: CliRunner, tmp_path: Path) -> None:
        # Sem injeção: a fábrica real valida o nome do motor.
        from rag.cli.main import create_app as real_app

        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        result = cli.invoke(
            real_app(),
            ["ocr", str(scan), "--output", str(tmp_path / "o.pdf"), "--engine", "bogus"],
        )
        assert result.exit_code == 1
        assert "desconhecido" in result.output


class TestInspectCommand:
    def test_invalid_uuid_exit_one(self, cli: CliRunner, app: typer.Typer) -> None:
        result = cli.invoke(app, ["inspect", "não-é-uuid"])
        assert result.exit_code == 1
        assert "UUID" in result.output


class TestIngestCommandValidation:
    def test_missing_metadata_exit_one(
        self, cli: CliRunner, app: typer.Typer, tmp_path: Path
    ) -> None:
        epub = tmp_path / "livro.epub"
        epub.write_bytes(b"PK\x03\x04")  # conteúdo irrelevante: falha antes
        result = cli.invoke(
            app, ["ingest", str(epub), "--metadata", str(tmp_path / "ausente.yaml")]
        )
        assert result.exit_code == 1
        assert "metadados" in result.output
