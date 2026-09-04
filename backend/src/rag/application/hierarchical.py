"""Estágio hierárquico: sínteses e conceitos localizam passagens (R04/B03;
SPEC §8.7, AC-12, AC-15).

`HierarchicalRetrievalService` compone a seleção de nós relevantes
(`SummariesRepository.select_nodes`/`ConceptsRepository.select_nodes`, via FTS
sobre o texto da síntese e o rótulo/aliases do conceito) com a recuperação
descendente (`supporting_passages_current`) para unir as passagens ORIGINAIS
aos candidatos lexical/vetorial do `RetrievalService` — “selecionar nós
hierárquicos relevantes; descer até passagens originais; reranquear as
passagens; citar somente as passagens” (SPEC §8.7).

Garantias (B03/R04):

- `needs_hierarchical` governa este estágio: o serviço só é chamado quando o
  plano o marca (retrieval gated no `RetrievalService`);
- os filtros de obra/edição são aplicados ANTES e DEPOIS da seleção de nós —
  uma obra excluída nunca é localizada por síntese/conceito (AC-07);
- só suportes da execução de indexação/enriquecimento vigente são resolvidos
  (`_ACTIVE_RUN_CONDITION` nos repositorios);
- só PASSAGES vira candidato: `candidates` são `RankedCandidate` (stage
  HIERARCHICAL); o texto de síntese/conceito NUNCA é passagem, evidência ou
  candidato (AC-12);
- `hits` registra, para auditoria, qual nó localizou qual passagem (AC-15) —
  só IDs, nunca texto do livro (AC-16).

Falha fechada: sem texto de consulta o estágio devolve vazio (a recuperação
comum continua); qualquer falha do banco propagha como erro tipado — nunca se
devolve um subconjunto de nós como sucesso silencioso.
"""

from collections import defaultdict
from uuid import UUID

from psycopg import AsyncConnection

from rag.domain.enums import HierarchicalSourceKind, RankingStage
from rag.domain.library import Passage
from rag.domain.query import EditionFilter
from rag.domain.retrieval import (
    HierarchicalBudget,
    HierarchicalHit,
    HierarchicalResult,
    RankedCandidate,
)
from rag.infrastructure.repositories.enrichment import (
    ConceptsRepository,
    SummariesRepository,
)


class HierarchicalRetrievalService:
    """Seleciona sínteses/conceitos relevantes e desce até as passagens originais."""

    async def retrieve(
        self,
        conn: AsyncConnection,
        *,
        query: str,
        filters: EditionFilter,
        budget: HierarchicalBudget,
    ) -> HierarchicalResult:
        query = query.strip()
        if not query:
            return HierarchicalResult()

        summaries_repo = SummariesRepository(conn)
        concepts_repo = ConceptsRepository(conn)
        summary_nodes = await summaries_repo.select_nodes(
            query=query, filters=filters, limit=budget.max_summary_nodes
        )
        concept_nodes = await concepts_repo.select_nodes(
            query=query, filters=filters, limit=budget.max_concept_nodes
        )

        # Deduplicação por passagem (uma passagem pode ser localizada por
        # vários nós): retém o score máximo e registra TODOS os nós que a
        # localizou na auditoria.
        best_score: dict[UUID, float] = {}
        hits_by_passage: dict[UUID, list[HierarchicalHit]] = defaultdict(list)
        for kind, nodes in (
            (HierarchicalSourceKind.SUMMARY, summary_nodes),
            (HierarchicalSourceKind.CONCEPT, concept_nodes),
        ):
            for node_id, score in nodes:
                passages = await self._descend(
                    kind,
                    conn,
                    node_id,
                    filters=filters,
                    limit=budget.max_passages_per_node,
                )
                for passage in passages:
                    if passage.id not in best_score or score > best_score[passage.id]:
                        best_score[passage.id] = score
                    hits_by_passage[passage.id].append(
                        HierarchicalHit(kind=kind, node_id=node_id, passage_id=passage.id)
                    )

        ordered = sorted(best_score.items(), key=lambda item: (-item[1], item[0]))
        ordered = ordered[: budget.max_total_passages]
        candidates = tuple(
            RankedCandidate(
                passage_id=passage_id,
                stage=RankingStage.HIERARCHICAL,
                score=score,
                rank=rank,
            )
            for rank, (passage_id, score) in enumerate(ordered)
        )
        hits = tuple(hit for passage_id, _ in ordered for hit in hits_by_passage[passage_id])
        return HierarchicalResult(candidates=candidates, hits=hits)

    @staticmethod
    async def _descend(
        kind: HierarchicalSourceKind,
        conn: AsyncConnection,
        node_id: UUID,
        *,
        filters: EditionFilter,
        limit: int,
    ) -> list[Passage]:
        """Desce do nó até as passagens originais (recuperação descendente).

        `SummariesRepository.supporting_passages_current` /
        `ConceptsRepository.supporting_passages_current` já aplican o conjunto
        corrente e os filtros antes da seleção (AC-07, AC-12).
        """
        if kind is HierarchicalSourceKind.SUMMARY:
            return await SummariesRepository(conn).supporting_passages_current(
                node_id, filters=filters, limit=limit
            )
        return await ConceptsRepository(conn).supporting_passages_current(
            node_id, filters=filters, limit=limit
        )
