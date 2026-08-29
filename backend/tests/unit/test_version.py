"""Teste mínimo de fumaça do backend (T01)."""

import rag


def test_version_is_semver_like() -> None:
    parts = rag.__version__.split(".")
    assert len(parts) == 3
    for part in parts:
        assert part.isdigit()
