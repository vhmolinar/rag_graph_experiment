from typing import cast

from rag.domain.canonical import BlockKind, CanonicalBlock, CanonicalDocument
from rag.domain.enums import SourceType


def _document(**changes: object) -> CanonicalDocument:
    warnings = changes.pop("warnings", ())
    block = CanonicalBlock(
        ordinal=0,
        kind=BlockKind.PARAGRAPH,
        level=0,
        text="Texto canônico.",
        original_text="Texto canônico.",
        section_path=("Capítulo",),
    ).model_copy(update=changes)
    return CanonicalDocument(
        source_type=SourceType.EPUB, blocks=(block,), warnings=cast(tuple[str, ...], warnings)
    )


def test_canonical_fingerprint_is_deterministic() -> None:
    assert _document().fingerprint() == _document().fingerprint()


def test_canonical_fingerprint_covers_structural_and_provenance_fields() -> None:
    baseline = _document().fingerprint()
    for change in (
        {"text": "Texto alterado."},
        {"original_text": "Texto original alterado."},
        {"section_path": ("Outro capítulo",)},
        {"level": 1},
    ):
        assert _document(**change).fingerprint() != baseline


def test_canonical_fingerprint_covers_warnings() -> None:
    assert _document().fingerprint() != _document(warnings=("aviso",)).fingerprint()
