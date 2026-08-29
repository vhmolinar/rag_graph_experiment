"""Registros de versão são imutáveis (T02; AC-15)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ModelEndpointVersion,
    PromptVersion,
    utcnow,
)


def _now() -> datetime:
    return datetime.now(UTC)


class TestImmutability:
    def test_version_record_is_frozen(self) -> None:
        version = ChunkingVersion(label="chunk-v1", created_at=_now())
        with pytest.raises(ValidationError):
            version.label = "chunk-v2"

    def test_embedding_version_is_frozen(self) -> None:
        version = EmbeddingVersion(
            label="emb-v1", model_name="qwen3-embedding", dimensions=1024, created_at=_now()
        )
        with pytest.raises(ValidationError):
            version.dimensions = 2048


class TestValidation:
    def test_embedding_dimensions_positive(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingVersion(label="emb", model_name="m", dimensions=0, created_at=_now())

    def test_prompt_template_hash_shape(self) -> None:
        with pytest.raises(ValidationError):
            PromptVersion(label="p", template_sha256="xyz", created_at=_now())
        ok = PromptVersion(label="p", template_sha256="a" * 64, created_at=_now())
        assert ok.template_sha256 == "a" * 64

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChunkingVersion(label="c", created_at=datetime(2026, 1, 1))  # sem tz

    def test_endpoint_kind_restricted(self) -> None:
        with pytest.raises(ValidationError):
            ModelEndpointVersion(
                label="m",
                endpoint_kind="unknown",  # type: ignore[arg-type]
                provider="p",
                model_name="m",
                created_at=_now(),
            )

    def test_utcnow_is_aware(self) -> None:
        assert utcnow().tzinfo is not None
