"""Contratos de resposta e verificação (T02; AC-08, AC-09, AC-10)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.answer import (
    Claim,
    Contradiction,
    EvidenceRef,
    GeneratedAnswer,
    QuoteResponse,
    VerificationResult,
)
from rag.domain.enums import VerificationAction


class TestClaim:
    def test_factual_claim_requires_evidence(self) -> None:
        """AC-09: afirmação factual sem evidência é inválida por construção."""
        with pytest.raises(ValidationError, match="evidência"):
            Claim(id="c1", text="O autor define spleen.", inference=False)

    def test_inference_claim_may_lack_direct_evidence(self) -> None:
        claim = Claim(id="c1", text="Infere-se que...", inference=True)
        assert claim.inference

    def test_inference_claim_may_cite_evidence(self) -> None:
        claim = Claim(id="c1", text="Infere-se X", evidence_ids=(uuid4(),), inference=True)
        assert len(claim.evidence_ids) == 1

    def test_evidence_ids_are_immutable(self) -> None:
        """RRR01: evidências de uma afirmação verificada não podem mudar."""
        claim = Claim(id="c1", text="Fato", evidence_ids=(uuid4(),))
        with pytest.raises(AttributeError):
            claim.evidence_ids.append(uuid4())  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            claim.evidence_ids = ()


class TestGeneratedAnswer:
    def test_abstention_requires_reason(self) -> None:
        """AC-10."""
        with pytest.raises(ValidationError, match="abstention_reason"):
            GeneratedAnswer(answer_markdown="Sem suporte.", abstained=True)

    def test_abstained_answer_cannot_have_claims(self) -> None:
        with pytest.raises(ValidationError, match="abstida"):
            GeneratedAnswer(
                answer_markdown="...",
                abstained=True,
                abstention_reason="acervo não cobre",
                claims=(Claim(id="c1", text="x", evidence_ids=(uuid4(),)),),
            )

    def test_reason_forbidden_when_not_abstained(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedAnswer(
                answer_markdown="...",
                abstained=False,
                abstention_reason="indevido",
            )

    def test_claim_ids_unique(self) -> None:
        with pytest.raises(ValidationError, match="únicos"):
            GeneratedAnswer(
                answer_markdown="...",
                abstained=False,
                claims=(
                    Claim(id="c1", text="a", evidence_ids=(uuid4(),)),
                    Claim(id="c1", text="b", evidence_ids=(uuid4(),)),
                ),
            )

    def test_valid_answer(self) -> None:
        answer = GeneratedAnswer(
            answer_markdown="Spleen é ... [1]",
            abstained=False,
            claims=(Claim(id="c1", text="Spleen é tédio", evidence_ids=(uuid4(),)),),
            limitations=("apenas uma obra consultada",),
        )
        assert not answer.abstained


class TestQuoteResponse:
    def test_has_no_prose_fields(self) -> None:
        """AC-08: garantia estrutural — quote não tem campo para texto sintetizado."""
        assert set(QuoteResponse.model_fields) == {"evidences"}

    def test_empty_when_no_results(self) -> None:
        assert QuoteResponse().evidences == ()


class TestEvidenceRef:
    def test_section_path_is_immutable(self) -> None:
        """RRR01: caminho de seção de uma evidência não pode ser alterado."""
        ref = EvidenceRef(
            passage_id=uuid4(),
            edition_id=uuid4(),
            work_id=uuid4(),
            text="trecho",
            score=0.9,
            rank=0,
            section_path=("Parte 1", "Capítulo I"),
        )
        with pytest.raises(AttributeError):
            ref.section_path.append("Capítulo II")  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            ref.section_path = ("Outra",)

    def test_offsets_paired(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceRef(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="trecho",
                score=0.9,
                rank=0,
                char_start=3,
            )

    def test_full_reference(self) -> None:
        ref = EvidenceRef(
            passage_id=uuid4(),
            edition_id=uuid4(),
            work_id=uuid4(),
            text="trecho literal",
            score=0.9,
            rank=0,
            section_path=("Obra", "Capítulo I"),
            physical_page=42,
            printed_label="p. 37",
            char_start=10,
            char_end=30,
        )
        assert ref.physical_page == 42


class TestVerificationResult:
    def test_supported_cannot_exceed_total(self) -> None:
        with pytest.raises(ValidationError):
            VerificationResult(
                total_claims=1,
                supported_claims=2,
                citation_coverage=0.5,
                action=VerificationAction.ACCEPTED,
            )

    def test_contradiction_record(self) -> None:
        result = VerificationResult(
            total_claims=2,
            supported_claims=1,
            citation_coverage=0.5,
            action=VerificationAction.CORRECTED,
            contradictions=(
                Contradiction(claim_id="c2", evidence_id=uuid4(), detail="fonte afirma o oposto"),
            ),
        )
        assert result.contradictions[0].claim_id == "c2"
