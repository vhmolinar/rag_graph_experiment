"""Autenticação por ambiente ou secret file (SPEC §11, T07)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rag.adapters.model_settings import ModelAuthSettings


class TestModelAuthSettings:
    def test_defaults_to_empty_api_key(self) -> None:
        settings = ModelAuthSettings()
        assert settings.api_key.get_secret_value() == ""

    def test_api_key_from_constructor(self) -> None:
        settings = ModelAuthSettings(api_key="segredo-env")  # type: ignore[arg-type]
        assert settings.api_key.get_secret_value() == "segredo-env"

    def test_api_key_file_is_read_and_stripped(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "api_key"
        secret_file.write_text("segredo-do-arquivo\n", encoding="utf-8")
        settings = ModelAuthSettings(api_key_file=secret_file)
        assert settings.api_key.get_secret_value() == "segredo-do-arquivo"

    def test_both_api_key_and_file_is_rejected(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "api_key"
        secret_file.write_text("segredo-do-arquivo", encoding="utf-8")
        with pytest.raises(ValidationError, match="nunca ambos"):
            ModelAuthSettings(api_key="segredo-env", api_key_file=secret_file)  # type: ignore[arg-type]

    def test_missing_secret_file_fails_closed(self, tmp_path: Path) -> None:
        missing = tmp_path / "nao-existe"
        with pytest.raises(ValidationError, match="não foi possível ler"):
            ModelAuthSettings(api_key_file=missing)

    def test_empty_secret_file_fails_closed(self, tmp_path: Path) -> None:
        empty = tmp_path / "api_key"
        empty.write_text("   \n", encoding="utf-8")
        with pytest.raises(ValidationError, match="vazio"):
            ModelAuthSettings(api_key_file=empty)
