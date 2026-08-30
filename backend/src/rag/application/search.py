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

from psycopg import AsyncConnection

from rag.domain.enums import Depth, RankingStage
from rag.domain.errors import ModelResponseError, NotFoundError
from rag.domain.providers import EmbeddingProvider, RerankerProvider
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.retrieval import RetrievalPolicy, RetrievalResult, fuse_rankings
from rag.domain.runs import RankedCandidate
from rag.domain.versions import RetrievalPolicyVersion, utcnow
from rag.infrastructure.repositories.passages import PassagesRepository
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
        filters: EditionFilter | None,
        policy: RetrievalPolicy,
        depth: Depth,
    ) -> RetrievalResult:
        budget = policy.budget_for(depth)
        filters = filters if filters is not None else EditionFilter()

        # Estágios independentes (SPEC §8.5): cada lista é recuperada e
        # ranqueada em separado, sem que um condicione o outro.
        lexical = await LexicalSearchRepository(conn).search(
            lexical_query, filters=filters, limit=budget.lexical_top_k
        )
        query_vector = await self._embedding.embed_query(semantic_query)
        vector = await VectorSearchRepository(conn).search(
            query_vector, filters=filters, limit=budget.vector_top_k
        )

        fused = fuse_rankings([lexical, vector], k=budget.rrf_k)[: budget.rerank_top_n]

        texts = await self._passage_texts(conn, fused)
        reranked = await self._reranked(fused, semantic_query, texts)

        policy_version = await self._register_policy(conn, policy)
        return RetrievalResult(
            lexical=tuple(lexical),
            vector=tuple(vector),
            fused=fused,
            reranked=reranked,
            policy_version_id=policy_version.id,
        )

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
