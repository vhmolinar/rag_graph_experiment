"""Serviço de recuperação: lexical + vetorial independentes, RRF, reranking
(T09; SPEC §8.5, AC-05, AC-06, AC-07).

`RetrievalService` coordina os dois estágios de busca (reaproveitando
`LexicalSearchRepository` de T08 e `VectorSearchRepository` desta tarefa),
funde por RRF e re-rankana com o provider de reranking (T07). A falha do
reranker propagha ao chamador como erro tipado — nunca é mascarada devolvendo
a lista fundida como se fora o resultado reranked (checklist §9). Scores e
posições de todos os estágios ficam preservados em `RetrievalResult` (AC-06).

A política de orçamento por profundidade é registrada como
`RetrievalPolicyVersion` (idempotente via `VersionsRepository`) — uma
execução pode reproduzir os parâmetros de recuperação usados (AC-15).
"""

from uuid import UUID

from psycopg import AsyncConnection

from rag.domain.enums import Depth, QueryStatus, RankingStage, SearchStrategy
from rag.domain.errors import ModelResponseError, NotFoundError
from rag.domain.providers import EmbeddingProvider, RerankerProvider
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.retrieval import RetrievalPolicy, RetrievalResult, fuse_rankings
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import EmbeddingVersion, RetrievalPolicyVersion, utcnow
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
    ) -> None:
        self._embedding = embedding_provider
        self._reranker = reranker_provider

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
            await self._persist(conn, run, result, policy_version.id)
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
        fused = fuse_rankings([lexical, vector], k=budget.rrf_k)
        rerank_candidates = fused[: budget.rerank_top_n]

        texts = await self._passage_texts(conn, rerank_candidates)
        reranked = await self._reranked(rerank_candidates, semantic_query, texts)

        policy_version = await self._register_policy(conn, policy)
        result = RetrievalResult(
            lexical=tuple(lexical),
            vector=tuple(vector),
            fused=fused,
            reranked=reranked,
            policy_version_id=policy_version.id,
            embedding_version_id=registered_emb_version.id,
            run_id=run.id,
            strategy=strategy,
        )

        # R2-T9-02 (AC-06, AC-15): persistência obrigatória dos rankings e versões no AnswerRun
        await self._persist(conn, run, result, policy_version.id, registered_emb_version.id)
        return result

    @staticmethod
    async def _persist(
        conn: AsyncConnection,
        run: AnswerRun,
        result: RetrievalResult,
        policy_version_id: UUID,
        embedding_version_id: UUID | None = None,
    ) -> None:
        version_updates: dict[str, object] = {
            "retrieval_policy_version_id": policy_version_id,
            "embedding_version_id": embedding_version_id,
        }
        new_versions = run.versions.model_copy(update=version_updates)
        target_status = (
            QueryStatus.RUNNING
            if run.status in (QueryStatus.QUEUED, QueryStatus.RUNNING)
            else run.status
        )
        updated_run = run.transition(
            target_status,
            candidates=(*run.candidates, *result.answer_run_candidates()),
            versions=new_versions,
        )
        await AnswerRunsRepository(conn).save(updated_run)

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
