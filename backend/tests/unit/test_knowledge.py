"""Invariantes de resumos e conceitos (T02, AC-12)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.enums import ConceptState, SummaryScope
from rag.domain.knowledge import Concept, ConceptAlias, ConceptEvidence, Summary


class TestSummary:
    def test_summary_requires_supporting_passage(self) -> None:
        """AC-12: item abstrato sem suporte não é publicado."""
        with pytest.raises(ValidationError):
            Summary(
                edition_id=uuid4(),
                scope_type=SummaryScope.SECTION,
                section_id=uuid4(),
                text="resumo sem suporte",
                generator_version_id=uuid4(),
                supporting_passage_ids=(),
            )

    def test_summary_with_support_is_valid(self) -> None:
        summary = Summary(
            edition_id=uuid4(),
            scope_type=SummaryScope.EDITION,
            text="Síntese da edição.",
            generator_version_id=uuid4(),
            supporting_passage_ids=(uuid4(), uuid4()),
        )
        assert len(summary.supporting_passage_ids) == 2

    def test_edition_scope_rejects_section_id(self) -> None:
        """RR01: escopo 'edition' refere-se à própria edição, sem section_id."""
        with pytest.raises(ValidationError, match="não referencia seção"):
            Summary(
                edition_id=uuid4(),
                scope_type=SummaryScope.EDITION,
                section_id=uuid4(),
                text="Síntese incoerente.",
                generator_version_id=uuid4(),
                supporting_passage_ids=(uuid4(),),
            )

    def test_section_and_chapter_scopes_require_section_id(self) -> None:
        """RR01: capítulo é representado por Section e exige section_id."""
        for scope in (SummaryScope.SECTION, SummaryScope.CHAPTER):
            with pytest.raises(ValidationError, match="section_id"):
                Summary(
                    edition_id=uuid4(),
                    scope_type=scope,
                    text="Síntese sem seção.",
                    generator_version_id=uuid4(),
                    supporting_passage_ids=(uuid4(),),
                )
        for scope in (SummaryScope.SECTION, SummaryScope.CHAPTER):
            summary = Summary(
                edition_id=uuid4(),
                scope_type=scope,
                section_id=uuid4(),
                text="Síntese válida.",
                generator_version_id=uuid4(),
                supporting_passage_ids=(uuid4(),),
            )
            assert summary.section_id is not None

    def test_supports_are_immutable_after_publication(self) -> None:
        """RRR01: suportes de uma síntese publicada não podem ser removidos."""
        summary = Summary(
            edition_id=uuid4(),
            scope_type=SummaryScope.EDITION,
            text="Síntese.",
            generator_version_id=uuid4(),
            supporting_passage_ids=(uuid4(), uuid4()),
        )
        with pytest.raises(AttributeError):
            summary.supporting_passage_ids.pop()  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            summary.supporting_passage_ids = ()
        with pytest.raises(ValidationError):
            summary.text = "alterada"


class TestConcept:
    def test_default_state_is_proposed(self) -> None:
        concept = Concept(normalized_label="liberdade")
        assert concept.state is ConceptState.PROPOSED

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ConceptAlias(concept_id=uuid4(), expression="liberdade", confidence=1.5)
        with pytest.raises(ValidationError):
            ConceptEvidence(
                concept_id=uuid4(),
                passage_id=uuid4(),
                confidence=-0.1,
                extractor_version_id=uuid4(),
            )

    def test_evidence_links_concept_to_passage(self) -> None:
        evidence = ConceptEvidence(
            concept_id=uuid4(),
            passage_id=uuid4(),
            confidence=0.9,
            extractor_version_id=uuid4(),
        )
        assert evidence.passage_id is not None
