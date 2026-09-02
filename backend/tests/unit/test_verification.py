"""Verificação de respostas dissertativas — domínio puro (T13; AC-09, AC-10)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.answer import AnswerBlock, Claim, Contradiction, GeneratedAnswer
from rag.domain.enums import Depth
from rag.domain.providers import ClaimVerdict
from rag.domain.verification import (
    VerificationBudget,
    VerificationPolicy,
    assess_claims,
    invalid_evidence_ids,
    mark_unsupported_as_inference,
)


def _budget(
    *,
    depth: Depth = Depth.STANDARD,
    max_iterations: int = 2,
    support_threshold: float = 0.7,
) -> VerificationBudget:
    return VerificationBudget(
        depth=depth,
        max_iterations=max_iterations,
        support_threshold=support_threshold,
    )


class TestVerificationPolicy:
    def test_requires_all_depths_exactly_once(self) -> None:
        with pytest.raises(ValidationError, match="três profundidades"):
            VerificationPolicy(budgets=((Depth.BRIEF, _budget()), (Depth.STANDARD, _budget())))
        with pytest.raises(ValidationError, match="única vez"):
            VerificationPolicy(
                budgets=(
                    (Depth.BRIEF, _budget()),
                    (Depth.BRIEF, _budget()),
                    (Depth.STANDARD, _budget()),
                    (Depth.DEEP, _budget()),
                )
            )

    def test_budget_depth_must_match_key(self) -> None:
        with pytest.raises(ValidationError, match="coincidir"):
            VerificationPolicy(
                budgets=(
                    (Depth.BRIEF, _budget(depth=Depth.DEEP)),
                    (Depth.STANDARD, _budget()),
                    (Depth.DEEP, _budget()),
                )
            )

    def test_defaults_are_monotonic(self) -> None:
        policy = VerificationPolicy.defaults()
        budgets = {d: b for d, b in policy.budgets}
        assert budgets[Depth.BRIEF].max_iterations < budgets[Depth.DEEP].max_iterations
        assert budgets[Depth.BRIEF].support_threshold < budgets[Depth.DEEP].support_threshold

    def test_budget_for_returns_matching_budget(self) -> None:
        policy = VerificationPolicy.defaults()
        assert policy.budget_for(Depth.DEEP).depth is Depth.DEEP


class TestInvalidEvidenceIds:
    def test_empty_when_all_ids_valid(self) -> None:
        passage_id = uuid4()
        claims = (Claim(id="c1", text="Afirmação.", evidence_ids=(passage_id,)),)
        assert invalid_evidence_ids(claims, {passage_id}) == ()

    def test_detects_unknown_ids(self) -> None:
        known = uuid4()
        unknown = uuid4()
        claims = (
            Claim(id="c1", text="A.", evidence_ids=(known,)),
            Claim(id="c2", text="B.", evidence_ids=(unknown,)),
        )
        assert invalid_evidence_ids(claims, {known}) == (unknown,)

    def test_deterministic_order(self) -> None:
        a = uuid4()
        b = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(b, a)),)
        assert invalid_evidence_ids(claims, set()) == tuple(sorted((a, b), key=str))

    def test_does_not_mutate_inputs(self) -> None:
        passage_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(passage_id,)),)
        invalid_evidence_ids(claims, {passage_id})
        assert claims[0].evidence_ids == (passage_id,)


class TestAssessClaims:
    def test_all_supported(self) -> None:
        evidence_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(evidence_id,)),)
        verdicts = (ClaimVerdict(claim_id="c1", evidence_id=evidence_id, supported=True),)
        assessments = assess_claims(claims, verdicts)
        assert assessments[0].supported
        assert assessments[0].contradictions == ()

    def test_unsupported_pair_marks_claim_unsupported(self) -> None:
        evidence_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(evidence_id,)),)
        verdicts = (ClaimVerdict(claim_id="c1", evidence_id=evidence_id, supported=False),)
        assessments = assess_claims(claims, verdicts)
        assert not assessments[0].supported

    def test_missing_verdict_treated_as_unsupported(self) -> None:
        """Falha fechada: um par sem veredicto não é liberado como sustentado."""
        evidence_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(evidence_id,)),)
        assessments = assess_claims(claims, ())
        assert not assessments[0].supported

    def test_contradiction_recorded_and_marks_unsupported(self) -> None:
        evidence_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(evidence_id,)),)
        verdicts = (
            ClaimVerdict(
                claim_id="c1",
                evidence_id=evidence_id,
                supported=False,
                contradiction=True,
                detail="A fonte afirma o oposto.",
            ),
        )
        assessments = assess_claims(claims, verdicts)
        assert not assessments[0].supported
        assert assessments[0].contradictions == (
            Contradiction(
                claim_id="c1", evidence_id=evidence_id, detail="A fonte afirma o oposto."
            ),
        )

    def test_contradiction_marks_unsupported_even_when_supported_true(self) -> None:
        """T13-02: um veredicto `supported=true, contradiction=true` NUNCA
        mantém a afirmação factual — a contradição sempre prevalece (AC-09)."""
        evidence_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(evidence_id,)),)
        verdicts = (
            ClaimVerdict(
                claim_id="c1",
                evidence_id=evidence_id,
                supported=True,
                contradiction=True,
                detail="A fonte contradice a afirmação.",
            ),
        )
        assessments = assess_claims(claims, verdicts)
        assert not assessments[0].supported
        assert len(assessments[0].contradictions) == 1

    def test_all_evidence_pairs_must_be_supported(self) -> None:
        first = uuid4()
        second = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(first, second)),)
        verdicts = (
            ClaimVerdict(claim_id="c1", evidence_id=first, supported=True),
            ClaimVerdict(claim_id="c1", evidence_id=second, supported=False),
        )
        assessments = assess_claims(claims, verdicts)
        assert not assessments[0].supported

    def test_extra_verdicts_are_ignored(self) -> None:
        evidence_id = uuid4()
        claims = (Claim(id="c1", text="A.", evidence_ids=(evidence_id,)),)
        verdicts = (
            ClaimVerdict(claim_id="c1", evidence_id=evidence_id, supported=True),
            ClaimVerdict(claim_id="c9", evidence_id=uuid4(), supported=False),
        )
        assessments = assess_claims(claims, verdicts)
        assert assessments[0].supported


class TestMarkUnsupportedAsInference:
    def _answer(self) -> GeneratedAnswer:
        evidence_id = uuid4()
        c1 = Claim(id="c1", text="Sustentada.", evidence_ids=(evidence_id,))
        c2 = Claim(id="c2", text="Não sustentada.", evidence_ids=(evidence_id,))
        blocks = (
            AnswerBlock(text=c1.text, claim_id="c1"),
            AnswerBlock(text=" "),
            AnswerBlock(text=c2.text, claim_id="c2"),
        )
        return GeneratedAnswer(
            answer_markdown="".join(block.text for block in blocks),
            blocks=blocks,
            claims=(c1, c2),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )

    def test_marks_only_unsupported_claims(self) -> None:
        answer = self._answer()
        corrected = mark_unsupported_as_inference(answer, {"c2"})
        assert corrected.claims[0].inference is False
        assert corrected.claims[1].inference is True
        # O restante permanece íntegro.
        assert corrected.answer_markdown == answer.answer_markdown
        assert [c.id for c in corrected.claims] == ["c1", "c2"]

    def test_keeps_existing_inference_marks(self) -> None:
        answer = self._answer()
        corrected = mark_unsupported_as_inference(answer, {"c2"})
        again = mark_unsupported_as_inference(corrected, {"c2"})
        assert again.claims[1].inference is True
        assert [c.id for c in again.claims] == ["c1", "c2"]

    def test_empty_set_is_noop(self) -> None:
        answer = self._answer()
        corrected = mark_unsupported_as_inference(answer, set())
        assert corrected == answer

    def test_result_is_revalidated(self) -> None:
        answer = self._answer()
        corrected = mark_unsupported_as_inference(answer, {"c2"})
        assert isinstance(corrected, GeneratedAnswer)
        assert not corrected.abstained
