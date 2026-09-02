"""Serviço de geração dissertativa e verificação (T13; AC-09, AC-10, AC-11, AC-14).

Cobre: fluxo aceito; abstenção do gerador; regeneração com feedback quando o
gerador cita ID inexistente; falha fechada quando a citação fabricada persiste;
afirmações não sustentadas marcadas como inferências (CORRECTED); abstenção
forzada por cobertura baixa; timeout do provedor de verificação não libera
resposta não verificada; limitação determinística para comparativas com fonte
única (AC-11).
"""

import sys
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import FakeGeneratorProvider, FakeVerifierProvider, verdict_factory
from psycopg import AsyncConnection

from rag.application.dissertative import (
    _ABSTENTION_REASON,
    DissertativeService,
    _RegisteredVersions,
)
from rag.domain.answer import AnswerBlock, Claim, EvidenceRef, GeneratedAnswer
from rag.domain.context import ContextEvidence, PackedContext
from rag.domain.enums import Depth, Intent, SearchStrategy, VerificationAction
from rag.domain.errors import VerificationError
from rag.domain.providers import (
    ClaimVerdict,
    GenerationRequest,
    VerificationRequest,
    VerificationVerdict,
)
from rag.domain.query import LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.verification import VerificationBudget, VerificationPolicy


def _conn() -> AsyncConnection:
    return cast(AsyncConnection, object())


def _registered_versions() -> _RegisteredVersions:
    return _RegisteredVersions(
        generation_prompt_version_id=uuid4(),
        verification_prompt_version_id=uuid4(),
        generator_endpoint_version_id=uuid4(),
        verifier_endpoint_version_id=uuid4(),
        verification_policy_version_id=uuid4(),
    )


def _verification_policy(
    *,
    max_iterations: int = 1,
    support_threshold: float = 0.7,
) -> VerificationPolicy:
    budgets = tuple(
        (
            depth,
            VerificationBudget(
                depth=depth,
                max_iterations=max_iterations,
                support_threshold=support_threshold,
            ),
        )
        for depth in Depth
    )
    return VerificationPolicy(budgets=budgets)


def _evidence_ref(passage_id: UUID, work_id: UUID) -> EvidenceRef:
    return EvidenceRef(
        passage_id=passage_id,
        edition_id=uuid4(),
        work_id=work_id,
        text="Trecho literal de teste.",
        score=1.0,
        rank=0,
    )


def _packed(*, work_ids: tuple[UUID, ...] = (uuid4(),)) -> PackedContext:
    evidences = tuple(
        ContextEvidence(evidence=_evidence_ref(uuid4(), work_id)) for work_id in work_ids
    )
    return PackedContext(
        evidences=evidences,
        total_chars=sum(len(item.evidence.text) for item in evidences),
        context_budget_chars=8000,
    )


def _plan(intent: Intent = Intent.COMPARATIVE) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        lexical_query=LexicalQuery(required_terms=("sufoca",)),
        semantic_query="consulta semântica",
        strategy=SearchStrategy.HYBRID,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.AUTOMATIC,
            chosen=SearchStrategy.HYBRID,
            intent_signals=(f"intenção={intent.value}",),
            rationale="Plano de teste de T13.",
        ),
        needs_diversity=intent is Intent.COMPARATIVE,
        needs_hierarchical=intent is Intent.CONCEPTUAL,
    )


def _answer(
    evidence_id: UUID,
    *,
    claim_id: str = "c1",
    text: str = "Afirmação sustentada.",
) -> GeneratedAnswer:
    claim = Claim(id=claim_id, text=text, evidence_ids=(evidence_id,))
    blocks = (
        AnswerBlock(text="Resposta de teste em Markdown: "),
        AnswerBlock(text=claim.text, claim_id=claim.id),
    )
    return GeneratedAnswer(
        answer_markdown="".join(block.text for block in blocks),
        blocks=blocks,
        claims=(claim,),
        limitations=(),
        abstained=False,
        abstention_reason=None,
    )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> DissertativeService:
    versions = _registered_versions()

    async def _register(
        _conn: AsyncConnection, _model: str, _policy: object
    ) -> _RegisteredVersions:
        return versions

    monkeypatch.setattr(DissertativeService, "_register_versions", staticmethod(_register))
    return DissertativeService(FakeGeneratorProvider(), FakeVerifierProvider())


class TestAcceptedFlow:
    async def test_valid_answer_accepted(self, service: DissertativeService) -> None:
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
        assert outcome.answer.claims[0].evidence_ids == (evidence_id,)
        assert outcome.verification.action is VerificationAction.ACCEPTED
        assert outcome.verification.supported_claims == 1
        assert outcome.verification.citation_coverage == 1.0
        assert outcome.verification.iterations == 1
        assert outcome.verification.invalid_evidence_ids == ()
        assert outcome.verification.unsupported_claim_ids == ()

    async def test_no_evidences_forces_abstention(self, service: DissertativeService) -> None:
        packed = PackedContext(
            evidences=(),
            total_chars=0,
            context_budget_chars=8000,
        )
        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(),
            model_name="test-model",
        )
        assert outcome.answer.abstained
        assert outcome.verification.action is VerificationAction.FORCED_ABSTENTION


class TestGeneratorAbstention:
    async def test_abstained_answer_skips_verification(self, service: DissertativeService) -> None:
        packed = _packed()
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: GeneratedAnswer(
            answer_markdown="",
            claims=(),
            limitations=(),
            abstained=True,
            abstention_reason="Sem suporte suficiente.",
        )
        verifier = cast(FakeVerifierProvider, service._verifier)
        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(),
            model_name="test-model",
        )
        assert outcome.answer.abstained
        assert outcome.verification.action is VerificationAction.ACCEPTED
        assert verifier.requests == []  # nenhuna chamada ao verificador

    async def test_generator_abstention_is_replaced_with_canonical(
        self, service: DissertativeService
    ) -> None:
        """T13-01: o serviço substitui a abstenção do gerador pela forma
        canônica — nenhuna prosa nem razão arbitrária do gerador atravessa o
        caminho de abstenção."""
        packed = _packed()
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: GeneratedAnswer(
            answer_markdown="",
            claims=(),
            limitations=(),
            abstained=True,
            abstention_reason="motivo arbitrário do gerador.",
        )
        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(),
            model_name="test-model",
        )
        assert outcome.answer.abstained
        assert outcome.answer.abstention_reason == _ABSTENTION_REASON
        assert outcome.answer.answer_markdown == ""
        assert outcome.answer.claims == ()
        assert outcome.answer.blocks == ()


class TestInvalidCitations:
    async def test_invalid_id_triggers_regeneration_with_feedback(
        self, service: DissertativeService
    ) -> None:
        """O gerador tenta citar ID inexistente; com feedback corrige-se."""
        packed = _packed()
        valid_id = packed.evidences[0].evidence.passage_id
        invalid_id = uuid4()

        calls = 0

        def _factory(request: GenerationRequest) -> GeneratedAnswer:
            nonlocal calls
            calls += 1
            if request.verification_feedback is None:
                return _answer(invalid_id)
            return _answer(valid_id)

        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = _factory
        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=1),
            model_name="test-model",
        )
        assert calls == 2
        assert outcome.verification.iterations == 2
        assert outcome.verification.action is VerificationAction.ACCEPTED
        assert outcome.answer.claims[0].evidence_ids == (valid_id,)
        assert generator.requests[1].verification_feedback is not None

    async def test_invalid_id_persists_fails_closed(self, service: DissertativeService) -> None:
        """Após o limite de regenerações, citação fabricada NUNCA é liberada."""
        packed = _packed()
        invalid_id = uuid4()
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(invalid_id)
        with pytest.raises(VerificationError):
            await service.answer(
                _conn(),
                question="Pergunta?",
                session_context=None,
                depth=Depth.STANDARD,
                plan=_plan(),
                packed=packed,
                verification_policy=_verification_policy(max_iterations=0),
                model_name="test-model",
            )


class TestUnsupportedClaims:
    async def test_unsupported_claims_marked_as_inference(
        self, service: DissertativeService
    ) -> None:
        """AC-09: afirmação não sustentada é marcada como inferência (CORRECTED)."""
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: GeneratedAnswer(
            answer_markdown="Resposta de teste: Sustentada. Não sustentada.",
            blocks=(
                AnswerBlock(text="Resposta de teste: "),
                AnswerBlock(text="Sustentada.", claim_id="c1"),
                AnswerBlock(text=" "),
                AnswerBlock(text="Não sustentada.", claim_id="c2"),
            ),
            claims=(
                Claim(id="c1", text="Sustentada.", evidence_ids=(evidence_id,)),
                Claim(id="c2", text="Não sustentada.", evidence_ids=(evidence_id,)),
            ),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
        verifier = cast(FakeVerifierProvider, service._verifier)
        verifier._verdict_factory = verdict_factory(unsupported={("c2", evidence_id)})

        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0, support_threshold=0.5),
            model_name="test-model",
        )
        assert outcome.verification.action is VerificationAction.CORRECTED
        assert outcome.verification.unsupported_claim_ids == ("c2",)
        assert outcome.verification.citation_coverage == 0.5
        assert outcome.answer.claims[0].inference is False
        assert outcome.answer.claims[1].inference is True

    async def test_low_coverage_forces_abstention(self, service: DissertativeService) -> None:
        """AC-10: cobertura abaixo do limiar produz abstenção forzada."""
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: GeneratedAnswer(
            answer_markdown="Resposta de teste: Sustentada. Não sustentada.",
            blocks=(
                AnswerBlock(text="Resposta de teste: "),
                AnswerBlock(text="Sustentada.", claim_id="c1"),
                AnswerBlock(text=" "),
                AnswerBlock(text="Não sustentada.", claim_id="c2"),
            ),
            claims=(
                Claim(id="c1", text="Sustentada.", evidence_ids=(evidence_id,)),
                Claim(id="c2", text="Não sustentada.", evidence_ids=(evidence_id,)),
            ),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
        verifier = cast(FakeVerifierProvider, service._verifier)
        verifier._verdict_factory = verdict_factory(unsupported={("c2", evidence_id)})

        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0, support_threshold=0.7),
            model_name="test-model",
        )
        assert outcome.verification.action is VerificationAction.FORCED_ABSTENTION
        assert outcome.answer.abstained
        assert outcome.answer.claims == ()

    async def test_contradiction_forces_regeneration_then_correction(
        self, service: DissertativeService
    ) -> None:
        """Detecção de contradição: com regenerações disponíveis, tenta de novo;
        no fim, marca e registra a contradição."""
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        verifier = cast(FakeVerifierProvider, service._verifier)
        verifier._verdict_factory = verdict_factory(contradictions={("c1", evidence_id)})

        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0, support_threshold=0.0),
            model_name="test-model",
        )
        assert outcome.verification.contradictions
        assert outcome.verification.contradictions[0].claim_id == "c1"
        assert outcome.verification.action is VerificationAction.CORRECTED
        assert outcome.answer.claims[0].inference is True

    async def test_contradiction_with_supported_true_marks_inference(
        self, service: DissertativeService
    ) -> None:
        """T13-02: um veredicto `supported=true, contradiction=true` NUNCA
        libera a afirmação como factual — a contradição prevalece e a afirmação
        é marcada como inferência (CORRECTED)."""
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)

        def _supported_contradiction(request: VerificationRequest) -> VerificationVerdict:
            return VerificationVerdict(
                verdicts=(
                    ClaimVerdict(
                        claim_id="c1",
                        evidence_id=evidence_id,
                        supported=True,
                        contradiction=True,
                        detail="A fonte contradice a afirmação.",
                    ),
                )
            )

        verifier = cast(FakeVerifierProvider, service._verifier)
        verifier._verdict_factory = _supported_contradiction

        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0, support_threshold=0.0),
            model_name="test-model",
        )
        assert outcome.verification.contradictions
        assert outcome.verification.supported_claims == 0
        assert outcome.verification.unsupported_claim_ids == ("c1",)
        assert outcome.answer.claims[0].inference is True


class TestAnswerMarkdownBinding:
    async def test_corrected_answer_keeps_markdown_bound_to_claims(
        self, service: DissertativeService
    ) -> None:
        """T13-03: no caminho de correção, o Markdown entregue continua ligado
        às afirmações — blocos cubrem o Markdown e correspondem verbatim às
        claims (a resposta corrigida continua validável)."""
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: GeneratedAnswer(
            answer_markdown="Sustentada. Não sustentada.",
            blocks=(
                AnswerBlock(text="Sustentada.", claim_id="c1"),
                AnswerBlock(text=" "),
                AnswerBlock(text="Não sustentada.", claim_id="c2"),
            ),
            claims=(
                Claim(id="c1", text="Sustentada.", evidence_ids=(evidence_id,)),
                Claim(id="c2", text="Não sustentada.", evidence_ids=(evidence_id,)),
            ),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
        verifier = cast(FakeVerifierProvider, service._verifier)
        verifier._verdict_factory = verdict_factory(unsupported={("c2", evidence_id)})

        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0, support_threshold=0.5),
            model_name="test-model",
        )
        assert outcome.verification.action is VerificationAction.CORRECTED
        delivered = outcome.answer
        assert "".join(block.text for block in delivered.blocks) == delivered.answer_markdown
        claim_by_id = {claim.id: claim for claim in delivered.claims}
        for block in delivered.blocks:
            if block.claim_id is not None:
                assert claim_by_id[block.claim_id].text == block.text


class TestVerifierFailure:
    async def test_verifier_timeout_releases_nothing(self, service: DissertativeService) -> None:
        """AC-14/SPEC §9.4: timeout do verificador NUNCA libera resposta não
        verificada — o erro tipado propagha fechado."""
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        from rag.domain.errors import ModelTimeoutError

        verifier = cast(FakeVerifierProvider, service._verifier)
        verifier._pending_failures.append(ModelTimeoutError())

        with pytest.raises(VerificationError):
            await service.answer(
                _conn(),
                question="Pergunta?",
                session_context=None,
                depth=Depth.STANDARD,
                plan=_plan(),
                packed=packed,
                verification_policy=_verification_policy(),
                model_name="test-model",
            )


class TestComparativeLimitation:
    async def test_comparative_single_work_declares_limitation(
        self, service: DissertativeService
    ) -> None:
        """AC-11: comparativa com fonte única declara a limitação."""
        packed = _packed(work_ids=(uuid4(),))
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        outcome = await service.answer(
            _conn(),
            question="Compare X com Y?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(intent=Intent.COMPARATIVE),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
        assert any("única obra" in limitation for limitation in outcome.answer.limitations)

    async def test_comparative_two_works_no_limitation(self, service: DissertativeService) -> None:
        packed = _packed(work_ids=(uuid4(), uuid4()))
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        outcome = await service.answer(
            _conn(),
            question="Compare X com Y?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(intent=Intent.COMPARATIVE),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
        assert not any("única obra" in limitation for limitation in outcome.answer.limitations)

    async def test_factual_single_work_no_limitation(self, service: DissertativeService) -> None:
        packed = _packed(work_ids=(uuid4(),))
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        outcome = await service.answer(
            _conn(),
            question="Facto?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(intent=Intent.FACTUAL),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
        assert outcome.answer.limitations == ()


class TestVersions:
    async def test_versions_registered_and_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        versions = _registered_versions()

        async def _register(
            _conn: AsyncConnection, _model: str, _policy: object
        ) -> _RegisteredVersions:
            return versions

        monkeypatch.setattr(DissertativeService, "_register_versions", staticmethod(_register))
        service = DissertativeService(FakeGeneratorProvider(), FakeVerifierProvider())
        packed = _packed()
        evidence_id = packed.evidences[0].evidence.passage_id
        generator = cast(FakeGeneratorProvider, service._generator)
        generator._answer_factory = lambda _request: _answer(evidence_id)
        outcome = await service.answer(
            _conn(),
            question="Pergunta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=_plan(),
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
        assert outcome.generation_prompt_version_id == versions.generation_prompt_version_id
        assert outcome.verification_prompt_version_id == versions.verification_prompt_version_id
        assert outcome.generator_endpoint_version_id == versions.generator_endpoint_version_id
        assert outcome.verifier_endpoint_version_id == versions.verifier_endpoint_version_id
        assert outcome.verification_policy_version_id == versions.verification_policy_version_id
