"""Serviço de recuperação: lexical + vetorial independentes, RRF, reranking
(T09; SPEC §8.5, AC-05, AC-06, AC-07), estratégia `expanded` (R03; SPEC §8.3)
e estágio hierárquico (R04; SPEC §8.7, AC-11, AC-12).

`RetrievalService` coordina os dois estágios de busca (reaproveitando
`LexicalSearchRepository` de T08 e `VectorSearchRepository` desta tarefa),
funde por RRF e re-rankana com o provider de reranking (T07). A falha do
reranker propagha ao chamador como erro tipado — nunca é mascarada devolvendo
a lista fundida como se fora o resultado reranked (checklist §9). Scores e
posições de todos os estágios ficam preservados em `RetrievalResult` (AC-06).

Na estratégia `expanded`, a recuperação delega no `ExpansionExecutor` (R03):
as subperguntas e os aliases do plano alteram os candidatos, cada expansão é
recuperada e ranqueada em separado com os MESMOS filtros, e a fusão RRF
deduplica as passagens recuperadas por várias expansões (a contribuição de
cada expansão soma) sob um orçamento TOTAL (`ExpansionPolicy`, `fused_top_k`)
— o orçamento nunca é multiplicado sem limite pelo número de subperguntas.

Quando o plano marca `needs_hierarchical` (perguntas conceituais e
comparativas), o `HierarchicalRetrievalService` (R04) seleciona sínteses e
conceitos relevantes, desce até as passagens ORIGINAIS da execução vigente e
une essas passagens à fusão — só passagens viram candidatos/evidências
(AC-12). A política hierárquica (`HierarchicalPolicy`) é versionada; falha
fechada se `needs_hierarchical` não tiver política.

As políticas de orçamento são registradas como versões imutáveis
(`RetrievalPolicyVersion`/`ExpansionPolicyVersion`/`HierarchicalPolicyVersion`,
idempotentes via `VersionsRepository`) — uma execução pode reproduzir os
parâmetros de recuperação usados (AC-15).
"""

from uuid import UUID

from psycopg import AsyncConnection

from rag.application.expansion import ExpansionExecutor
from rag.application.hierarchical import HierarchicalRetrievalService
from rag.domain.enums import Depth, QueryStatus, RankingStage, SearchStrategy
from rag.domain.errors import ModelResponseError, NotFoundError
from rag.domain.providers import EmbeddingProvider, RerankerProvider
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan
from rag.domain.retrieval import (
    ExpansionPolicy,
    HierarchicalHit,
    HierarchicalPolicy,
    RetrievalPolicy,
    RetrievalResult,
    fuse_rankings,
)
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import (
    EmbeddingVersion,
    ExpansionPolicyVersion,
    HierarchicalPolicyVersion,
    RetrievalPolicyVersion,
    utcnow,
)
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.search import LexicalSearchRepository
from rag.infrastructure.repositories.vector import VectorSearchRepository
from rag.infrastructure.repositories.versions import VersionsRepository


class RetrievalService:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        reranker_provider: RerankerProvider,
        hierarchical: HierarchicalRetrievalService | None = None,
    ) -> None:
        self._embedding = embedding_provider
        self._reranker = reranker_provider
        self._hierarchical = (
            hierarchical if hierarchical is not None else HierarchicalRetrievalService()
        )

    async def retrieve(
        self,
        conn: AsyncConnection,
        *,
        lexical_query: LexicalQuery,
        semantic_query: str,
        run: AnswerRun,
        filters: EditionFilter | None = None,
        policy: RetrievalPolicy | None = None,
        depth: Depth = Depth.STANDARD,
        strategy: SearchStrategy = SearchStrategy.HYBRID,
        plan: QueryPlan | None = None,
        expansion_policy: ExpansionPolicy | None = None,
        hierarchical_policy: HierarchicalPolicy | None = None,
    ) -> RetrievalResult:
        if not isinstance(run, AnswerRun):
            raise TypeError(
                f"run deve ser uma instância de AnswerRun, recebido {type(run).__name__}"
            )
        if strategy is SearchStrategy.AUTOMATIC:
            raise TypeError(
                "estratégia 'automatic' deve ser resolvida pelo planejador "
                "antes da recuperação (SPEC §8.3)"
            )
        policy = policy if policy is not None else RetrievalPolicy.defaults()
        budget = policy.budget_for(depth)
        filters = filters if filters is not None else EditionFilter()

        # R04 (B03): falha fechada ANTES de consultar o banco — um plano que
        # marca `needs_hierarchical` exige a política hierárquica versionada;
        # nunca se executa o estágio comum em silencio como substituo.
        if (
            strategy is not SearchStrategy.LITERAL
            and plan is not None
            and plan.needs_hierarchical
            and hierarchical_policy is None
        ):
            raise TypeError(
                "plano com needs_hierarchical exige a política hierárquica "
                "(orçamento versionado) — SPEC §8.7"
            )

        if strategy is SearchStrategy.EXPANDED:
            # R03 (B02): sem plano não há subperguntas/aliases a executar —
            # o plano nunca declara expansão sem expansão a executar (T10-02).
            if plan is None:
                raise TypeError(
                    "estratégia 'expanded' exige o plano (subperguntas/aliases) "
                    "para executar a expansão (SPEC §8.3)"
                )
            if expansion_policy is None:
                raise TypeError(
                    "estratégia 'expanded' exige a política de expansão "
                    "(orçamento total versionado)"
                )
            return await self._retrieve_expanded(
                conn,
                plan=plan,
                filters=filters,
                policy=expansion_policy,
                run=run,
                depth=depth,
                hierarchical_policy=hierarchical_policy,
            )

        if strategy is not SearchStrategy.LITERAL:
            # Falha fechada antes de consultar o banco quando o pipeline
            # semântico não tem uma versão de embedding válida.
            try:
                emb_version = self._embedding.embedding_version
            except AttributeError as err:
                raise TypeError(
                    "embedding_provider deve implementar a propriedade 'embedding_version' "
                    f"(SPEC §8.5, AC-05, AC-15): {err}"
                ) from err

            if not isinstance(emb_version, EmbeddingVersion):
                raise TypeError(
                    "embedding_provider.embedding_version deve ser EmbeddingVersion, "
                    f"recebido {type(emb_version).__name__}"
                )

        # Estágio lexical: comum a todas as estratégias (SPEC §8.4/§8.5).
        lexical = await LexicalSearchRepository(conn).search(
            lexical_query, filters=filters, limit=budget.lexical_top_k
        )

        if strategy is SearchStrategy.LITERAL:
            # SPEC §8.3 (B01): `literal` é FTS e similaridade textual — SEM
            # embedding, busca vetorial, RRF ou reranking. Os provedores de
            # modelo NÃO são tocados: uma falha deles não afeta a resposta.
            policy_version = await self._register_policy(conn, policy)
            result = RetrievalResult(
                lexical=tuple(lexical),
                vector=(),
                fused=(),
                reranked=(),
                policy_version_id=policy_version.id,
                embedding_version_id=None,
                run_id=run.id,
                strategy=strategy,
            )
            await self._persist(conn, run, result, retrieval_policy_version_id=policy_version.id)
            return result

        # `hybrid`/`expanded`: embedding + busca vetorial + RRF + reranking
        # (SPEC §8.5). `expanded` adiciona expansão em R03; esta taska garante
        # que a estratégia governa os estágios executados.
        # Estágios independentes (SPEC §8.5): cada lista é recuperada e
        # ranqueada em separado, sem que um condicione o outro.
        registered_emb_version = await VersionsRepository(conn).get_or_create(emb_version)

        query_vector = await self._embedding.embed_query(semantic_query)
        vector = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=registered_emb_version.id,
            filters=filters,
            limit=budget.vector_top_k,
        )

        # T9-04: manter a lista RRF completa em `fused` para rastreabilidade integral
        # de todos os candidatos; derivar `rerank_candidates` separadamente para o reranker.
        (
            hierarchical,
            hierarchical_hits,
            hierarchical_policy_version_id,
        ) = await self._hierarchical_stage(
            conn,
            plan=plan,
            strategy=strategy,
            filters=filters,
            depth=depth,
            hierarchical_policy=hierarchical_policy,
        )
        fusion_lists: list[list[RankedCandidate]] = [lexical, vector]
        if hierarchical:
            fusion_lists.append(list(hierarchical))
        fused = fuse_rankings(fusion_lists, k=budget.rrf_k)
        rerank_candidates = fused[: budget.rerank_top_n]

        texts = await self._passage_texts(conn, rerank_candidates)
        reranked = await self._reranked(rerank_candidates, semantic_query, texts)

        policy_version = await self._register_policy(conn, policy)
        result = RetrievalResult(
            lexical=tuple(lexical),
            vector=tuple(vector),
            hierarchical=hierarchical,
            hierarchical_hits=hierarchical_hits,
            fused=fused,
            reranked=reranked,
            policy_version_id=policy_version.id,
            embedding_version_id=registered_emb_version.id,
            run_id=run.id,
            strategy=strategy,
        )

        # R2-T9-02 (AC-06, AC-15): persistência obrigatória dos rankings e versões no AnswerRun
        await self._persist(
            conn,
            run,
            result,
            retrieval_policy_version_id=policy_version.id,
            hierarchical_policy_version_id=hierarchical_policy_version_id,
            embedding_version_id=registered_emb_version.id,
        )
        return result

    async def _retrieve_expanded(
        self,
        conn: AsyncConnection,
        *,
        plan: QueryPlan,
        filters: EditionFilter,
        policy: ExpansionPolicy,
        run: AnswerRun,
        depth: Depth,
        hierarchical_policy: HierarchicalPolicy | None = None,
    ) -> RetrievalResult:
        """Executa a estratégia `expanded` (R03; SPEC §8.3, B02).

        - orçamento TOTAL por profundidade (`ExpansionBudget`): o número de
          expansões é limitado e a fusão é tetada por `fused_top_k` — o total
          não é multiplicado sem limite pelo número de subperguntas;
        - cada expansão (principal, subperguntas, aliases) é recuperada e
          ranqueada em separado (`ExpansionExecutor`), com os MESMOS filtros;
        - a fusão RRF deduplica as passagens recuperadas por várias expansões
          (a contribuição de cada expansão soma em `fuse_rankings`);
        - reranking só toca os candidatos permitidos (obra excluída nunca chega);
        - todas as consultas, scores e posições ficam registrados em
          `RetrievalResult.expansions` e persistidos em `AnswerRun.expansions`.
        """
        budget = policy.budget_for(depth)
        try:
            emb_version = self._embedding.embedding_version
        except AttributeError as err:
            raise TypeError(
                "embedding_provider deve implementar a propriedade 'embedding_version' "
                f"(SPEC §8.5, AC-05, AC-15): {err}"
            ) from err
        if not isinstance(emb_version, EmbeddingVersion):
            raise TypeError(
                "embedding_provider.embedding_version deve ser EmbeddingVersion, "
                f"recebido {type(emb_version).__name__}"
            )
        registered_emb_version = await VersionsRepository(conn).get_or_create(emb_version)

        executor = ExpansionExecutor(self._embedding)
        expansions = executor.build_expansions(plan, budget)
        per_expansion = await executor.search(
            conn,
            expansions=expansions,
            embedding_version_id=registered_emb_version.id,
            filters=filters,
            budget=budget,
        )

        all_lists: list[tuple[RankedCandidate, ...] | list[RankedCandidate]] = []
        for exp_result in per_expansion:
            all_lists.append(exp_result.lexical)
            all_lists.append(exp_result.vector)
        (
            hierarchical,
            hierarchical_hits,
            hierarchical_policy_version_id,
        ) = await self._hierarchical_stage(
            conn,
            plan=plan,
            strategy=SearchStrategy.EXPANDED,
            filters=filters,
            depth=depth,
            hierarchical_policy=hierarchical_policy,
        )
        if hierarchical:
            all_lists.append(list(hierarchical))
        fused_full = fuse_rankings(all_lists, k=budget.rrf_k)
        fused = fused_full[: budget.fused_top_k]
        rerank_candidates = fused[: budget.rerank_top_n]

        texts = await self._passage_texts(conn, rerank_candidates)
        reranked = await self._reranked(rerank_candidates, plan.semantic_query, texts)

        lexical_flat = tuple(c for exp_result in per_expansion for c in exp_result.lexical)
        vector_flat = tuple(c for exp_result in per_expansion for c in exp_result.vector)

        policy_version = await self._register_expansion_policy(conn, policy)
        result = RetrievalResult(
            lexical=lexical_flat,
            vector=vector_flat,
            hierarchical=hierarchical,
            hierarchical_hits=hierarchical_hits,
            fused=fused,
            reranked=reranked,
            expansions=per_expansion,
            policy_version_id=policy_version.id,
            embedding_version_id=registered_emb_version.id,
            run_id=run.id,
            strategy=SearchStrategy.EXPANDED,
        )
        await self._persist(
            conn,
            run,
            result,
            expansion_policy_version_id=policy_version.id,
            hierarchical_policy_version_id=hierarchical_policy_version_id,
            embedding_version_id=registered_emb_version.id,
        )
        return result

    async def _hierarchical_stage(
        self,
        conn: AsyncConnection,
        *,
        plan: QueryPlan | None,
        strategy: SearchStrategy,
        filters: EditionFilter,
        depth: Depth,
        hierarchical_policy: HierarchicalPolicy | None,
    ) -> tuple[
        tuple[RankedCandidate, ...],
        tuple[HierarchicalHit, ...],
        UUID | None,
    ]:
        """R04 (B03): `needs_hierarchical` governa um estágio real (SPEC §8.7).

        O estágio seleciona sínteses/conceitos relevantes, desce até as
        passagens ORIGINAIS e une essas passagens aos candidatos lexical/
        vetorial — só passagens viram candidatos/evidências (AC-12). Sem
        plano, sem necessidade ou em `literal` (FTS puro, SPEC §8.3/B01) o
        estágio não é executado. Falha fechada: `needs_hierarchical` sem
        política hierárquica é erro de configuração, nunca execução parcial
        silenciosa.
        """
        if plan is None or not plan.needs_hierarchical:
            return (), (), None
        if strategy is SearchStrategy.LITERAL:
            return (), (), None
        if hierarchical_policy is None:
            raise TypeError(
                "plano com needs_hierarchical exige a política hierárquica "
                "(orçamento versionado) — SPEC §8.7"
            )
        budget = hierarchical_policy.budget_for(depth)
        result = await self._hierarchical.retrieve(
            conn,
            query=plan.semantic_query,
            filters=filters,
            budget=budget,
        )
        policy_version = await self._register_hierarchical_policy(conn, hierarchical_policy)
        return result.candidates, result.hits, policy_version.id

    @staticmethod
    async def _persist(
        conn: AsyncConnection,
        run: AnswerRun,
        result: RetrievalResult,
        *,
        retrieval_policy_version_id: UUID | None = None,
        expansion_policy_version_id: UUID | None = None,
        hierarchical_policy_version_id: UUID | None = None,
        embedding_version_id: UUID | None = None,
    ) -> None:
        version_updates: dict[str, object] = {}
        if retrieval_policy_version_id is not None:
            version_updates["retrieval_policy_version_id"] = retrieval_policy_version_id
        if expansion_policy_version_id is not None:
            version_updates["expansion_policy_version_id"] = expansion_policy_version_id
        if hierarchical_policy_version_id is not None:
            version_updates["hierarchical_policy_version_id"] = hierarchical_policy_version_id
        if embedding_version_id is not None:
            version_updates["embedding_version_id"] = embedding_version_id
        new_versions = run.versions.model_copy(update=version_updates)
        target_status = (
            QueryStatus.RUNNING
            if run.status in (QueryStatus.QUEUED, QueryStatus.RUNNING)
            else run.status
        )
        changes: dict[str, object] = {
            "candidates": (*run.candidates, *result.answer_run_candidates()),
            "versions": new_versions,
        }
        if result.expansions:
            # R03 (B02): rastreabilidade das consultas/scores/posições por
            # expansão persistida no AnswerRun (append-only).
            changes["expansions"] = (*run.expansions, *result.expansions)
        if result.hierarchical_hits:
            # R04 (B03): auditoria do estágio hierárquico — qual nó localizou
            # qual passagem (append-only, AC-12/AC-15).
            changes["hierarchical_hits"] = (
                *run.hierarchical_hits,
                *result.hierarchical_hits,
            )
        updated_run = run.transition(target_status, **changes)
        await AnswerRunsRepository(conn).save(updated_run)

    @staticmethod
    async def _register_expansion_policy(
        conn: AsyncConnection, policy: ExpansionPolicy
    ) -> ExpansionPolicyVersion:
        version = await VersionsRepository(conn).get_or_create(
            ExpansionPolicyVersion(
                label="expansion-policy",
                params=policy.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return version

    @staticmethod
    async def _register_hierarchical_policy(
        conn: AsyncConnection, policy: HierarchicalPolicy
    ) -> HierarchicalPolicyVersion:
        version = await VersionsRepository(conn).get_or_create(
            HierarchicalPolicyVersion(
                label="hierarchical-policy",
                params=policy.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return version

    @staticmethod
    async def _passage_texts(
        conn: AsyncConnection, fused: tuple[RankedCandidate, ...]
    ) -> list[str]:
        """Texto citável de cada candidato fundido, na ordem da fusão.

        Falha fechada se uma passagem candidata deixou de existir entre a
        busca e a montação do prompt (ex.: reindexação concorrente) — nunca
        se rerankana um documento não verificado no acervo.
        """
        passages = PassagesRepository(conn)
        texts: list[str] = []
        for candidate in fused:
            passage = await passages.get(candidate.passage_id)
            if passage is None:
                raise NotFoundError(
                    "Passagem candidata deixou de existir durante a recuperação.",
                    context={"passage_id": str(candidate.passage_id)},
                )
            texts.append(passage.text)
        return texts

    async def _reranked(
        self,
        fused: tuple[RankedCandidate, ...],
        semantic_query: str,
        texts: list[str],
    ) -> tuple[RankedCandidate, ...]:
        """Reranking falha fechado: qualquer falha do provider propagha."""
        if not fused:
            return ()
        relevance = await self._reranker.rerank(semantic_query, texts)
        if len(relevance) != len(fused):
            raise ModelResponseError(
                "Pontuações do reranker não correspondem aos candidatos enviados.",
                context={"esperado": str(len(fused)), "recebido": str(len(relevance))},
            )
        ordered_indices = sorted(
            range(len(fused)),
            key=lambda i: (-relevance[i], fused[i].passage_id),
        )
        return tuple(
            RankedCandidate(
                passage_id=fused[index].passage_id,
                stage=RankingStage.RERANKED,
                score=float(relevance[index]),
                rank=rank,
            )
            for rank, index in enumerate(ordered_indices)
        )

    @staticmethod
    async def _register_policy(
        conn: AsyncConnection, policy: RetrievalPolicy
    ) -> RetrievalPolicyVersion:
        version = await VersionsRepository(conn).get_or_create(
            RetrievalPolicyVersion(
                label="retrieval-policy",
                params=policy.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return version
