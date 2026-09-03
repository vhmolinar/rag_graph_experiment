"""Serviço de montagem de contexto e modo quote (T12; SPEC §8.6, §9.2, AC-08).

`ContextService` compone a recuperação (T09) com a seleção de evidências:

- `assemble(...) -> PackedContext`: resolve metadados citáveis (AC-03),
  seleciona as evidências dos candidatos reranked com deduplicação,
  diversidade adaptativa (SPEC §8.6) e orçamento de contexto (SPEC §9.1),
  e registra a política de contexto como `ContextPolicyVersion` (AC-15);
- `quote(...) -> QuoteResponse`: projeta as evidências seleccionadas no
  contrato de T02 — SEM chamar o provedor de geração.

A ausência de geração no modo quote é estrutural: o serviço não recebe nem
conhece nenhum provedor de geração (AC-08); `QuoteResponse` (T02) não tem
campos de prosa. O `PackedContext` (com `parent_text` de expansão, nunca
citável) é a entrada natural do modo dissertativo (T13).
"""

from psycopg import AsyncConnection

from rag.domain.answer import QuoteResponse
from rag.domain.context import (
    ContextCandidate,
    ContextPolicy,
    PackedContext,
    context_total_chars,
    select_evidences,
)
from rag.domain.enums import Depth
from rag.domain.errors import NotFoundError
from rag.domain.query import QueryPlan
from rag.domain.retrieval import RetrievalResult
from rag.domain.runs import RankedCandidate
from rag.domain.versions import ContextPolicyVersion, utcnow
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository


class ContextService:
    """Montador de contexto e produtor do modo quote. NUNCA chama provedor de
    geração (AC-08)."""

    async def assemble(
        self,
        conn: AsyncConnection,
        *,
        plan: QueryPlan,
        retrieval: RetrievalResult,
        depth: Depth,
        policy: ContextPolicy,
    ) -> PackedContext:
        budget = policy.budget_for(depth)
        candidates = await self._resolve_candidates(conn, retrieval.final_candidates())
        evidences = select_evidences(
            candidates, budget=budget, needs_diversity=plan.needs_diversity
        )
        policy_version = await self._register_policy(conn, policy)
        return PackedContext(
            evidences=evidences,
            total_chars=context_total_chars(evidences),
            context_budget_chars=budget.max_context_chars,
            policy_version_id=policy_version.id,
        )

    async def quote(
        self,
        conn: AsyncConnection,
        *,
        plan: QueryPlan,
        retrieval: RetrievalResult,
        depth: Depth,
        policy: ContextPolicy,
    ) -> QuoteResponse:
        """Modo quote: só trechos literais ranqueados e metadados — sem prosa.

        Delega na `assemble` (que nunca chama o provedor de geração) e projeta
        os `EvidenceRef` seleccionados. O contrato `QuoteResponse` (T02) não
        tem campos de prosa (AC-08).
        """
        packed = await self.assemble(
            conn, plan=plan, retrieval=retrieval, depth=depth, policy=policy
        )
        return QuoteResponse(evidences=tuple(item.evidence for item in packed.evidences))

    @staticmethod
    async def _resolve_candidates(
        conn: AsyncConnection, ranked: tuple[RankedCandidate, ...]
    ) -> list[ContextCandidate]:
        """Resolve metadados citáveis de cada candidato final (AC-03).

        O ranking final é `retrieval.final_candidates()`: reranked em
        `hybrid`/`expanded`, lexical em `literal` (SPEC §8.3, B01).

        Falha fechada se uma passagem candidata deixou de existir entre a
        recuperação e a montagem — nunca se cita um documento não verificado
        no acervo.
        """
        passages = PassagesRepository(conn)
        candidates: list[ContextCandidate] = []
        for candidate in ranked:
            citable = await passages.get_citable(candidate.passage_id)
            if citable is None:
                raise NotFoundError(
                    "Passagem candidata deixou de existir durante a montação de contexto.",
                    context={"passage_id": str(candidate.passage_id)},
                )
            candidates.append(
                ContextCandidate(passage=citable, score=candidate.score, rank=candidate.rank)
            )
        return candidates

    @staticmethod
    async def _register_policy(
        conn: AsyncConnection, policy: ContextPolicy
    ) -> ContextPolicyVersion:
        version = await VersionsRepository(conn).get_or_create(
            ContextPolicyVersion(
                label="context-policy",
                params=policy.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return version
