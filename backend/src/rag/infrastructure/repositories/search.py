"""Busca lexical em português: FTS + tolerância trigram (T08; SPEC §8.4).

`text_search` (tsvector) já existe desde a migration 0001 (T03). Este
repository só compõe consultas parametrizadas sobre ele — nenhuma DDL nova.

Nunca interpola texto do usuário em SQL: frase e cada termo obrigatório/
excluído são sempre parâmetros ligados (`%(nome)s`), processados no servidor
por `phraseto_tsquery`/`plainto_tsquery` (que tratam a entrada como texto
puro, nunca como sintaxe de operador de tsquery) e por `similarity()` do
pg_trgm. Os únicos fragmentos de texto interpolados na string SQL são nomes
de coluna/tabela fixos e chaves de parâmetro previsíveis (`required_0`,
`required_1`, ...) geradas a partir do ÍNDICE da lista, nunca do conteúdo
de `term` — mesmo padrão de `VersionsRepository` (allowlist fixa).

Tolerância trigram compara o termo contra cada PALAVRA da passagem
(`unnest` sobre `regexp_split_to_array`), não contra o texto inteiro: a
similaridade de Jaccard entre um termo curto e uma passagem de várias
frases seria sempre baixa por diluição, mesmo com o termo presente quase
idêntico em uma única palavra (NOTES.md §10.9 item 2). Esse caminho
palavra-a-palavra não usa `passages_text_trgm_gin` (indexado sobre o texto
inteiro) — é sempre um scan sequencial das linhas já filtradas pelas demais
condições; calibração de desempenho em corpora grandes fica para T19.

Passagens-pai (sem `embedding_version_id`, ver `application/index.py` e
NOTES.md §10.6 item 2) nunca são candidatas: só chunks-filho são unidades
recuperáveis pela busca.

Seleção do conjunto corrente (T8-01, REVIEW_T08): a recuperação de produção
usa SOMENTE a execução de indexação ativa da edição — jamais o histórico
reproduzível. A condição é explícita (`EXISTS` sobre `index_runs.is_active`)
e se aplica antes de frase, FTS, trigram e filtros de obra/edição, no mesmo
`WHERE`. Política de compatibilidade para linhas legadas
(`index_run_id IS NULL` — fixtures de teste, acervo anterior a `rag index`):
elas continuam elegíveis APENAS enquanto a edição não tem execução ativa;
assim que a edição é indexada, essas linhas deixam de ser candidatas junto
com as de execuções antigas — nunca reintroduzem um conjunto inativo.

`limit` é o orçamento de candidatos que T09 controla: é validado num inteiro
positivo com teto fixo (`MAX_SEARCH_LIMIT`) porque `LIMIT -1`/`LIMIT 0` no
PostgreSQL não restringem ou zeram a consulta como esperado (T8-03).
"""

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.enums import RankingStage
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.runs import RankedCandidate

_FTS_CONFIG = "portuguese_unaccent"
# T8-03: teto do orçamento de candidatos. `LIMIT -1` = sem limite e `LIMIT 0`
# não é um erro no PostgreSQL, então um chamador descuidado recuperaria o
# acervo inteiro ou nada de forma silenciosa. Valores inválidos para T09.
MAX_SEARCH_LIMIT = 100
# Separador de palavras para a comparação trigram: qualquer sequência de
# caracteres não alfanuméricos (pontuação, espaços). Literal fixo, nunca
# construído a partir de entrada externa.
_WORD_SPLIT_PATTERN = "[^[:alnum:]]+"


def _max_word_similarity(term_key: str) -> str:
    return (
        "(SELECT max(similarity(w, rag_immutable_unaccent(%(" + term_key + ")s))) "
        f"FROM unnest(regexp_split_to_array(rag_immutable_unaccent(p.text), "
        f"'{_WORD_SPLIT_PATTERN}')) AS w)"
    )


class LexicalSearchRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def search(
        self,
        query: LexicalQuery,
        *,
        filters: EditionFilter | None = None,
        limit: int = 20,
    ) -> list[RankedCandidate]:
        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise ValueError(
                f"limit deve ser um inteiro entre 1 e {MAX_SEARCH_LIMIT} (recebido {limit}) — "
                "valor não-positivo ou acima do teto viola o orçamento de candidatos."
            )
        filters = filters if filters is not None else EditionFilter()
        params: dict[str, object] = {
            "trigram_threshold": query.trigram_threshold,
            "limit": limit,
        }
        conditions = [
            "p.embedding_version_id IS NOT NULL",
            # T8-01: seleção explícita do conjunto corrente. (a) a passagem
            # pertence à execução ATIVA da sua edição; (b) é legada
            # (`index_run_id IS NULL`) e a edição NÃO tem execução ativa —
            # compatibilidade que nunca reintroduz um conjunto inativo.
            "("
            "EXISTS (SELECT 1 FROM index_runs ir WHERE ir.id = p.index_run_id AND ir.is_active) "
            "OR (p.index_run_id IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM index_runs ir2 WHERE ir2.edition_id = p.edition_id AND ir2.is_active"
            "))"
            ")",
        ]
        tsquery_parts: list[str] = []
        similarity_parts: list[str] = []

        if query.phrase is not None:
            params["phrase"] = query.phrase
            tsquery_parts.append(f"phraseto_tsquery('{_FTS_CONFIG}', %(phrase)s)")
            conditions.append(f"p.text_search @@ phraseto_tsquery('{_FTS_CONFIG}', %(phrase)s)")

        for i, term in enumerate(query.required_terms):
            key = f"required_{i}"
            params[key] = term
            tsquery_parts.append(f"plainto_tsquery('{_FTS_CONFIG}', %({key})s)")
            max_similarity = f"COALESCE({_max_word_similarity(key)}, 0)"
            similarity_parts.append(max_similarity)
            conditions.append(
                f"(p.text_search @@ plainto_tsquery('{_FTS_CONFIG}', %({key})s) "
                f"OR {max_similarity} >= %(trigram_threshold)s)"
            )

        for i, term in enumerate(query.excluded_terms):
            key = f"excluded_{i}"
            params[key] = term
            conditions.append(f"NOT (p.text_search @@ plainto_tsquery('{_FTS_CONFIG}', %({key})s))")

        joins = ""
        if filters.include_work_ids or filters.exclude_work_ids:
            joins = "JOIN editions e ON e.id = p.edition_id"
        if filters.include_edition_ids:
            params["include_edition_ids"] = list(filters.include_edition_ids)
            conditions.append("p.edition_id = ANY(%(include_edition_ids)s)")
        if filters.exclude_edition_ids:
            params["exclude_edition_ids"] = list(filters.exclude_edition_ids)
            conditions.append("p.edition_id <> ALL(%(exclude_edition_ids)s)")
        if filters.include_work_ids:
            params["include_work_ids"] = list(filters.include_work_ids)
            conditions.append("e.work_id = ANY(%(include_work_ids)s)")
        if filters.exclude_work_ids:
            params["exclude_work_ids"] = list(filters.exclude_work_ids)
            conditions.append("e.work_id <> ALL(%(exclude_work_ids)s)")

        # Validado em LexicalQuery: phrase ou required_terms sempre presente,
        # logo tsquery_parts nunca está vazia aqui.
        combined_tsquery = " && ".join(tsquery_parts)
        fuzzy_score = " + ".join(similarity_parts) if similarity_parts else "0"
        where_clause = " AND ".join(conditions)
        sql = (
            "SELECT p.id AS passage_id, "  # noqa: S608
            f"ts_rank_cd(p.text_search, {combined_tsquery}) AS score, "
            f"({fuzzy_score}) AS fuzzy_score "
            f"FROM passages p {joins} "
            f"WHERE {where_clause} "
            "ORDER BY score DESC, fuzzy_score DESC, p.id "
            "LIMIT %(limit)s"
        )

        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        return [
            RankedCandidate(
                passage_id=row["passage_id"],
                stage=RankingStage.LEXICAL,
                score=float(row["score"]),
                rank=rank,
            )
            for rank, row in enumerate(rows)
        ]
