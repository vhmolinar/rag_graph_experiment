"""Lógica pura do enriquecimento hierárquico (T11; SPEC §7.4, AC-12).

Cobre as partes determinísticas e testáveis sem banco: cálculo de seções
descendentes (`descendant_section_ids`) e a validação fechada de suportes
(`EnrichmentService._validated_supports`) — vazio = item rejeitado (SPEC §7.4),
fora do escopo = falha fechada (AC-12).
"""

from uuid import UUID, uuid4

import pytest

from rag.application.enrichment import EnrichmentService
from rag.domain.errors import ModelResponseError
from rag.domain.knowledge import descendant_section_ids
from rag.domain.library import Section


def _section(id_: UUID, *, level: int, parent: UUID | None = None) -> Section:
    return Section(
        edition_id=uuid4(),
        level=level,
        ordinal=0,
        path=["secao"] * (level + 1),
        parent_section_id=parent,
        id=id_,
    )


class TestDescendantSectionIds:
    def test_returns_self_and_all_descendants(self) -> None:
        root, child_a, child_b, grandchild = uuid4(), uuid4(), uuid4(), uuid4()
        sections = [
            _section(root, level=0),
            _section(child_a, level=1, parent=root),
            _section(child_b, level=1, parent=root),
            _section(grandchild, level=2, parent=child_a),
        ]
        assert descendant_section_ids(sections, root) == frozenset(
            {root, child_a, child_b, grandchild}
        )
        assert descendant_section_ids(sections, child_a) == frozenset({child_a, grandchild})

    def test_section_without_children_returns_only_self(self) -> None:
        solo = uuid4()
        assert descendant_section_ids([_section(solo, level=0)], solo) == frozenset({solo})

    def test_sibling_not_included(self) -> None:
        root, child, sibling = uuid4(), uuid4(), uuid4()
        sections = [
            _section(root, level=0),
            _section(child, level=1, parent=root),
            _section(sibling, level=1, parent=root),
        ]
        assert descendant_section_ids(sections, child) == frozenset({child})


class TestValidatedSupports:
    def test_empty_support_rejects_item_with_warning(self) -> None:
        """SPEC §7.4: sem suporte identificado, o item abstrato não é publicado."""
        warnings: list[str] = []
        result = EnrichmentService._validated_supports(
            (), allowed={uuid4()}, item_label="seção X", warnings=warnings
        )
        assert result is None
        assert len(warnings) == 1
        assert "não publicado" in warnings[0]

    def test_out_of_scope_support_fails_closed(self) -> None:
        """Suporte fora do escopo é violação de contrato — nunca publica síntese
        com evidência de outra região (AC-12)."""
        allowed = {uuid4()}
        with pytest.raises(ModelResponseError):
            EnrichmentService._validated_supports(
                (uuid4(),), allowed=allowed, item_label="capítulo X", warnings=[]
            )

    def test_deduplicates_preserving_order(self) -> None:
        a, b = uuid4(), uuid4()
        result = EnrichmentService._validated_supports(
            (a, b, a), allowed={a, b}, item_label="edição", warnings=[]
        )
        assert result == (a, b)

    def test_supports_within_scope_are_accepted(self) -> None:
        a, b = uuid4(), uuid4()
        result = EnrichmentService._validated_supports(
            (b, a), allowed={a, b}, item_label="seção X", warnings=[]
        )
        assert result == (b, a)
