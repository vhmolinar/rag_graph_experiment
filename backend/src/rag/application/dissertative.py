"""Serviço de geração dissertativa e verificação (T13; SPEC §9.3, §9.4).

`DissertativeService` compone a montagem de contexto (T12) com:

1. geração da resposta via `GeneratorProvider` (prompt estruturado com
   evidências numeradas e políticas de profundidade — SPEC §9.3);
2. verificação obrigatória via `VerifierProvider`: existência de IDs
   (determinística, SPEC §9.4), suporte e contradição (semânticas);
3. correção/regeneração com limite por profundidade (SPEC §9.4);
4. abstenção forzada se a cobertura global ficar abaixo do limiar (AC-10);
5. declaração determinística de limitação para comparativas com fonte única
   (AC-11).

Regras estruturais (NOTES.md §10.14):
- NUNCA se devolve uma resposta sem verificação: falha/timeout do provedor
  de verificação falha fechado (`VerificationError` — AC-14);
- NUNCA se devolve uma citação fabricada (ID inexistente): após o limite de
  regenerações, `VerificationError` (SPEC §9.4);
- NUNCA se usa conhecimento externo do modelo como fallback (SPEC §2) — não
  existe caminho alternativo de geração.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from rag.domain.answer import GeneratedAnswer, VerificationResult
from rag.domain.context import PackedContext
from rag.domain.enums import Depth, Intent, VerificationAction
from rag.domain.errors import VerificationError
from rag.domain.identifiers import sha256_of_text
from rag.domain.providers import (
    GenerationRequest,
    GeneratorProvider,
    VerificationRequest,
    VerifierProvider,
)
from rag.domain.query import QueryPlan
from rag.domain.verification import (
    VerificationBudget,
    VerificationPolicy,
    assess_claims,
    invalid_evidence_ids,
    mark_unsupported_as_inference,
)
from rag.domain.versions import (
    ModelEndpointVersion,
    PromptVersion,
    VerificationPolicyVersion,
    utcnow,
)
from rag.infrastructure.repositories.versions import VersionsRepository

_GENERATION_POLICY = (
    "És um provedor de respostas dissertativas de um RAG de livros em português. "
    "Responde EXCLUSIVAMENTE a partir das evidências fornecidas. Nunca use "
    "conhecimento externo ao acervo. Cada afirmação factual precisa de ao menos "
    "uma evidência citada; inferências precisam ser marcadas. Se as evidências "
    "não sustentarem uma resposta, abstengue-se. Responde apenas em JSON válido."
)

_GENERATION_OUTPUT_CONTRACT = (
    'Devolva um objeto JSON com "answer_markdown" (resposta em Markdown), '
    '"blocks" (lista de blocos cuja concatenação reproduz EXACTAMENTE '
    'answer_markdown: cada bloco é um objeto com "text" (trecho exato do '
    'Markdown) e "claim_id" (ID da afirmação se o trecho for a afirmação; '
    "null para prosa conectiva/estructural)), "
    '"claims" (lista de objetos com "id" (string), "text" (afirmação em '
    'português; para blocos de afirmação, "text" deve ser IDÊNTICO ao do '
    'bloco), "evidence_ids" (lista de UUIDs EXACTAMENTE como informados; '
    'vazio se inference=true) e "inference" (bool, marca afirmações '
    'inferidas sem suporte direto)), "limitations" (lista de limitações da '
    'resposta, ex.: fonte única consultada) e "abstained"/"abstention_reason" '
    "(abstenção se as evidências não sustentarem resposta; em abstenção, "
    "answer_markdown deve ser vazio)."
)

_VERIFICATION_POLICY = (
    "És um provedor de verificação de afirmações de um RAG de livros em "
    "português. Julga EXCLUSIVAMENTE se cada evidência informada sustenta a "
    "afirmação citada e se a contradice. Nunca introduz novas afirmações. "
    "Responde apenas em JSON válido."
)

_VERIFICATION_OUTPUT_CONTRACT = (
    'Devolva um objeto JSON com "verdicts": lista de objetos com "claim_id" '
    '(string), "evidence_id" (string UUID), "supported" (bool), '
    '"contradiction" (bool, padrão false) e "detail" (string opcional, breve). '
    "Emita um veredicto para CADA par (afirmação, evidência) citado em "
    "evidence_ids."
)

_SINGLE_SOURCE_LIMITATION = (
    "A resposta se apoia em evidências de uma única obra; a ausência de outras "
    "fontes limita a comparação."
)

_ABSTENTION_REASON = "O acervo não proporcionou evidências suficientes para responder."


@dataclass(frozen=True)
class _RegisteredVersions:
    generation_prompt_version_id: UUID
    verification_prompt_version_id: UUID
    generator_endpoint_version_id: UUID
    verifier_endpoint_version_id: UUID
    verification_policy_version_id: UUID


class DissertativeAnswer(BaseModel):
    """Resultado do modo dissertativo: resposta verificada + versões registradas
    (AC-15; a integração completa em `AnswerRun` fica para T18)."""

    model_config = ConfigDict(frozen=True)

    answer: GeneratedAnswer
    verification: VerificationResult
    generator_endpoint_version_id: UUID
    verifier_endpoint_version_id: UUID
    generation_prompt_version_id: UUID
    verification_prompt_version_id: UUID
    verification_policy_version_id: UUID


class DissertativeService:
    def __init__(self, generator: GeneratorProvider, verifier: VerifierProvider) -> None:
        self._generator = generator
        self._verifier = verifier

    async def answer(
        self,
        conn: AsyncConnection,
        *,
        question: str,
        session_context: str | None,
        depth: Depth,
        plan: QueryPlan,
        packed: PackedContext,
        verification_policy: VerificationPolicy,
        model_name: str,
    ) -> DissertativeAnswer:
        budget = verification_policy.budget_for(depth)
        versions = await self._register_versions(conn, model_name, verification_policy)

        if not packed.evidences:
            # AC-10: sem evidências não há geração possível — abstenção.
            return self._forced_abstention(versions, budget, result=None)

        evidence_refs = [item.evidence for item in packed.evidences]
        allowed_ids = frozenset(ref.passage_id for ref in evidence_refs)
        request = GenerationRequest(
            system_policy=_GENERATION_POLICY,
            output_contract=_GENERATION_OUTPUT_CONTRACT,
            question=question,
            scope_description=self._scope_description(plan, packed),
            evidences=evidence_refs,
            depth=depth,
            session_context=session_context,
            prompt_version_id=versions.generation_prompt_version_id,
        )

        feedback: str | None = None
        for iteration in range(budget.max_iterations + 1):
            if feedback is not None:
                request = request.model_copy(update={"verification_feedback": feedback})
            answer = await self._generator.generate(request)
            result = await self._verify(answer, request, allowed_ids, budget, iteration, versions)
            if answer.abstained:
                # Abstenção do gerador: nenhumas afirmações a verificar
                # (NOTES.md §10.14 item 8). T13-01: o serviço entrega SÓ a
                # forma canônica — nenhuna prosa do gerador (nem o seu
                # abstention_reason) atravessa o caminho de abstenção.
                return self._build_dissertative_answer(
                    _abstained_answer(_ABSTENTION_REASON), result, versions
                )
            if self._acceptable(result):
                final = self._ensure_comparative_limitation(answer, plan, packed)
                return self._build_dissertative_answer(final, result, versions)
            if iteration < budget.max_iterations:
                feedback = self._build_feedback(result)
                continue
            return self._finalize(answer, result, plan, packed, budget, versions)
        raise AssertionError("limite de iteraciones inalcanzável")  # pragma: no cover

    async def _verify(
        self,
        answer: GeneratedAnswer,
        request: GenerationRequest,
        allowed_ids: frozenset[UUID],
        budget: VerificationBudget,
        iteration: int,
        versions: _RegisteredVersions,
    ) -> VerificationResult:
        if answer.abstained:
            return VerificationResult(
                total_claims=0,
                supported_claims=0,
                citation_coverage=1.0,
                action=VerificationAction.ACCEPTED,
                iterations=iteration + 1,
            )
        # 1. Determinística: existência de IDs (SPEC §9.4 "rejeita IDs inexistentes").
        invalid = invalid_evidence_ids(answer.claims, allowed_ids)

        # 2. Semântica: o provedor julga cada par (afirmação, evidência).
        try:
            verdicts = await self._verifier.verify(
                VerificationRequest(
                    system_policy=_VERIFICATION_POLICY,
                    output_contract=_VERIFICATION_OUTPUT_CONTRACT,
                    question=request.question,
                    claims=answer.claims,
                    evidences=tuple(request.evidences),
                    prompt_version_id=versions.verification_prompt_version_id,
                )
            )
        except Exception as exc:
            raise VerificationError(
                "Verificação da resposta falhou; nenhuna resposta não verificada é liberada.",
                cause=exc,
            ) from exc

        assessments = assess_claims(answer.claims, verdicts.verdicts)
        total = len(answer.claims)
        supported = sum(1 for assessment in assessments if assessment.supported)
        coverage = (supported / total) if total else 1.0
        unsupported_ids = tuple(
            assessment.claim_id for assessment in assessments if not assessment.supported
        )
        contradictions = tuple(
            contradiction
            for assessment in assessments
            for contradiction in assessment.contradictions
        )
        if invalid or unsupported_ids or contradictions:
            action = (
                VerificationAction.REGENERATED
                if iteration < budget.max_iterations
                else VerificationAction.CORRECTED
            )
        else:
            action = VerificationAction.ACCEPTED
        return VerificationResult(
            total_claims=total,
            supported_claims=supported,
            citation_coverage=coverage,
            action=action,
            invalid_evidence_ids=invalid,
            unsupported_claim_ids=unsupported_ids,
            contradictions=contradictions,
            iterations=iteration + 1,
        )

    def _finalize(
        self,
        answer: GeneratedAnswer,
        result: VerificationResult,
        plan: QueryPlan,
        packed: PackedContext,
        budget: VerificationBudget,
        versions: _RegisteredVersions,
    ) -> DissertativeAnswer:
        if result.invalid_evidence_ids:
            raise VerificationError(
                "A resposta continua citando evidências inexistentes após o limite "
                "de regenerações; nenhuna resposta com citação fabricada é liberada.",
                context={"invalid_evidence_ids": [str(u) for u in result.invalid_evidence_ids]},
            )
        if result.citation_coverage >= budget.support_threshold:
            corrected = mark_unsupported_as_inference(answer, set(result.unsupported_claim_ids))
            corrected = self._ensure_comparative_limitation(corrected, plan, packed)
            corrected_result = result.model_copy(update={"action": VerificationAction.CORRECTED})
            return self._build_dissertative_answer(corrected, corrected_result, versions)
        return self._forced_abstention(versions, budget, result=result)

    def _forced_abstention(
        self,
        versions: _RegisteredVersions,
        budget: VerificationBudget,
        *,
        result: VerificationResult | None,
    ) -> DissertativeAnswer:
        """Abstenção forzada: sem evidências ou cobertura abaixo do limiar (AC-10)."""
        abstained = _abstained_answer(_ABSTENTION_REASON)
        if result is None:
            result = VerificationResult(
                total_claims=0,
                supported_claims=0,
                citation_coverage=1.0,
                action=VerificationAction.FORCED_ABSTENTION,
                iterations=0,
            )
        else:
            result = result.model_copy(update={"action": VerificationAction.FORCED_ABSTENTION})
        return self._build_dissertative_answer(abstained, result, versions)

    @staticmethod
    def _acceptable(result: VerificationResult) -> bool:
        return (
            not result.invalid_evidence_ids
            and not result.contradictions
            and not result.unsupported_claim_ids
        )

    @staticmethod
    def _build_feedback(result: VerificationResult) -> str:
        """Retorno ao gerador para a regeneração (SPEC §9.4). Nunca contém texto
        do livro além dos IDs/identificadores das afirmações."""
        parts: list[str] = []
        if result.invalid_evidence_ids:
            ids = ", ".join(str(u) for u in result.invalid_evidence_ids)
            parts.append(
                f"Existem citações de evidências INEXISTENTES ({ids}). Use apenas os "
                "IDs informados na lista de evidências."
            )
        if result.unsupported_claim_ids:
            ids = ", ".join(result.unsupported_claim_ids)
            parts.append(
                f"As afirmações {ids} não são sustentadas pelas evidências citadas. "
                "Corrige o texto, remova-as ou marca-as como inferências; nunca "
                "inventes evidências."
            )
        if result.contradictions:
            ids = ", ".join(c.claim_id for c in result.contradictions)
            parts.append(f"As afirmações {ids} contradicem as suas fontes. Corrige ou remova-as.")
        return "\n".join(parts)

    @staticmethod
    def _ensure_comparative_limitation(
        answer: GeneratedAnswer,
        plan: QueryPlan,
        packed: PackedContext,
    ) -> GeneratedAnswer:
        """AC-11: pergunta comparativa não é respondida com evidências de somente
        uma obra sem declarar a limitação. Garantia determinística do serviço
        (NOTES.md §10.14 item 7), independente do gerador."""
        if plan.intent is not Intent.COMPARATIVE:
            return answer
        works = {item.evidence.work_id for item in packed.evidences}
        if len(works) >= 2:
            return answer
        if _SINGLE_SOURCE_LIMITATION in answer.limitations:
            return answer
        data = answer.model_dump(mode="python")
        data["limitations"] = (*answer.limitations, _SINGLE_SOURCE_LIMITATION)
        return GeneratedAnswer.model_validate(data)

    @staticmethod
    def _scope_description(plan: QueryPlan, packed: PackedContext) -> str:
        works = {item.evidence.work_id for item in packed.evidences}
        filters = (
            "filtros inferidos ativos"
            if not plan.inferred_filters.is_empty()
            else "sem filtros inferidos"
        )
        return (
            f"Intenção: {plan.intent.value}. Evidências: {len(packed.evidences)} "
            f"passagem(s) de {len(works)} obra(s); {filters}."
        )

    @staticmethod
    async def _register_versions(
        conn: AsyncConnection,
        model_name: str,
        verification_policy: VerificationPolicy,
    ) -> _RegisteredVersions:
        versions = VersionsRepository(conn)
        generation_prompt = await versions.get_or_create(
            PromptVersion(
                label="dissertative-generation-prompt",
                template_sha256=sha256_of_text(
                    _GENERATION_POLICY + "\n" + _GENERATION_OUTPUT_CONTRACT
                ),
                params={"mode": "dissertative"},
                created_at=utcnow(),
            )
        )
        verification_prompt = await versions.get_or_create(
            PromptVersion(
                label="dissertative-verification-prompt",
                template_sha256=sha256_of_text(
                    _VERIFICATION_POLICY + "\n" + _VERIFICATION_OUTPUT_CONTRACT
                ),
                params={"judgment": "per-claim-evidence"},
                created_at=utcnow(),
            )
        )
        generator_version = await versions.get_or_create(
            ModelEndpointVersion(
                label="generator",
                endpoint_kind="generator",
                provider="openai-compatible",
                model_name=model_name,
                params={"role": "dissertative", "prompt_version_id": str(generation_prompt.id)},
                created_at=utcnow(),
            )
        )
        verifier_version = await versions.get_or_create(
            ModelEndpointVersion(
                label="verifier",
                endpoint_kind="generator",
                provider="openai-compatible",
                model_name=model_name,
                params={"role": "verification", "prompt_version_id": str(verification_prompt.id)},
                created_at=utcnow(),
            )
        )
        policy_version = await versions.get_or_create(
            VerificationPolicyVersion(
                label="verification-policy",
                params=verification_policy.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return _RegisteredVersions(
            generation_prompt_version_id=generation_prompt.id,
            verification_prompt_version_id=verification_prompt.id,
            generator_endpoint_version_id=generator_version.id,
            verifier_endpoint_version_id=verifier_version.id,
            verification_policy_version_id=policy_version.id,
        )

    @staticmethod
    def _build_dissertative_answer(
        answer: GeneratedAnswer,
        result: VerificationResult,
        versions: _RegisteredVersions,
    ) -> DissertativeAnswer:
        return DissertativeAnswer(
            answer=answer,
            verification=result,
            generation_prompt_version_id=versions.generation_prompt_version_id,
            verification_prompt_version_id=versions.verification_prompt_version_id,
            generator_endpoint_version_id=versions.generator_endpoint_version_id,
            verifier_endpoint_version_id=versions.verifier_endpoint_version_id,
            verification_policy_version_id=versions.verification_policy_version_id,
        )


def _abstained_answer(reason: str) -> GeneratedAnswer:
    return GeneratedAnswer(
        answer_markdown="",
        claims=(),
        limitations=(),
        abstained=True,
        abstention_reason=reason,
    )
