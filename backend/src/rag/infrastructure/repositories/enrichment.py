"""Repositories de resumos e conceitos (T11; SPEC §7.4, AC-12).

`SummariesRepository` persiste sínteses + suportes; `ConceptsRepository`
persiste conceitos globais com aliases e evidencias. A recuperação
descendente ("descer até passagens originais", SPEC §8.7) devolve SEMPRE
`Passage` — nunca o texto de síntese/conceito como citação (AC-12). A
integridade referencial é imposta pelo banco: `summary_supports` e
`concept_evidence` apontam a `passages` via FKs compostas (R01).
"""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.enums import ConceptState, SummaryScope
from rag.domain.knowledge import Concept, EnrichmentRun, Summary
from rag.domain.library import Passage
from rag.domain.query import EditionFilter

_PASSAGE_COLUMNS = (
    "p.id, p.edition_id, p.section_id, p.page_start_id, p.page_end_id, p.ordinal, "
    "p.text, p.token_count, p.char_start, p.char_end, p.context_header, "
    "p.parent_passage_id, p.embedding_version_id, p.chunking_version_id"
)

# Configuração FTS portuguesa do schema (mesma de `search.py`), usada para
# selecionar nós relevantes pelo texto da síntese/conceito (R04, SPEC §8.7).
_FTS_CONFIG = "portuguese_unaccent"

# Seleção explícita do conjunto corrente (T8-01, R04): um nó hierárquico só é
# elegível através de passagens de suporte da execução ATIVA da edição — jamais
# do histórico inativo. Linhas legadas (`index_run_id IS NULL`) continuam
# elegíveis APENAS enquanto a edição não tem execução ativa, mesmo critério de
# `LexicalSearchRepository` (compatibilidade que nunca reintroduz um conjunto
# inativo).
_ACTIVE_RUN_CONDITION = """(
    EXISTS (SELECT 1 FROM index_runs ir WHERE ir.id = p.index_run_id AND ir.is_active)
    OR (p.index_run_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM index_runs ir2
        WHERE ir2.edition_id = p.edition_id AND ir2.is_active
    ))
)"""


def _passage_from_row(row: dict[str, Any]) -> Passage:
    return Passage(**row)


def _edition_filter_conditions(
    filters: EditionFilter,
) -> tuple[list[str], dict[str, object]]:
    """Condições de filtro obra/edição sobre `passages p` JOIN `editions e`.

    Mesmo padrão dos repositories de busca (T08/T09): os filtros são aplicados
    ANTES da seleção de nós e de passagens descendentes — uma obra excluída não
    pode ser localizada por síntese/conceito (AC-07, B03/R04). Valores são
    sempre parâmetros ligados.
    """
    conditions: list[str] = []
    params: dict[str, object] = {}
    if filters.include_edition_ids:
        params["hier_include_edition_ids"] = list(filters.include_edition_ids)
        conditions.append("p.edition_id = ANY(%(hier_include_edition_ids)s)")
    if filters.exclude_edition_ids:
        params["hier_exclude_edition_ids"] = list(filters.exclude_edition_ids)
        conditions.append("p.edition_id <> ALL(%(hier_exclude_edition_ids)s)")
    if filters.include_work_ids:
        params["hier_include_work_ids"] = list(filters.include_work_ids)
        conditions.append("e.work_id = ANY(%(hier_include_work_ids)s)")
    if filters.exclude_work_ids:
        params["hier_exclude_work_ids"] = list(filters.exclude_work_ids)
        conditions.append("e.work_id <> ALL(%(hier_exclude_work_ids)s)")
    return conditions, params


def _filters_sql(condition: list[str]) -> str:
    return f" AND {' AND '.join(condition)}" if condition else ""


class EnrichmentRunsRepository:
    """Execuções de enriquecimento concluídas (T11, correção T11-03/R2-T11-01).

    A idempotência por (edição, execução de indexação, versão de síntese) é
    decidida por ESTE registro, nunca pela existência de sínteses/conceitos:
    uma execução sem itens publicados (todos os suportes rejeitados) também
    fica registrada, e `index_run_id` (o conjunto de passagens efetivamente
    enviado ao provedor) integra a identidade — reindexar com o mesmo modelo
    exige nova execução sobre o conjunto corrente.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def get_for_edition_run_version(
        self,
        edition_id: UUID,
        index_run_id: UUID,
        summarizer_version_id: UUID,
    ) -> EnrichmentRun | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, index_run_id, summarizer_version_id, "
                "extractor_version_id, created_at FROM enrichment_runs "
                "WHERE edition_id = %s AND index_run_id = %s AND summarizer_version_id = %s",
                (edition_id, index_run_id, summarizer_version_id),
            )
            row = await cur.fetchone()
        return EnrichmentRun(**row) if row else None

    async def create_if_absent(self, run: EnrichmentRun) -> bool:
        """Insere a execução; devolve `False` se a identidade já estiver
        registrada (corrida de concorrência — nada é sobrescrito)."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO enrichment_runs (id, edition_id, index_run_id,
                                             summarizer_version_id,
                                             extractor_version_id, created_at)
                VALUES (%(id)s, %(edition_id)s, %(index_run_id)s,
                        %(summarizer_version_id)s, %(extractor_version_id)s,
                        %(created_at)s)
                ON CONFLICT (edition_id, index_run_id, summarizer_version_id)
                    DO NOTHING
                """,
                {
                    "id": run.id,
                    "edition_id": run.edition_id,
                    "index_run_id": run.index_run_id,
                    "summarizer_version_id": run.summarizer_version_id,
                    "extractor_version_id": run.extractor_version_id,
                    "created_at": run.created_at,
                },
            )
            return cur.rowcount == 1

    async def count_for_edition(self, edition_id: UUID) -> int:
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM enrichment_runs WHERE edition_id = %s", (edition_id,)
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0


class SummariesRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, summary: Summary) -> None:
        """Insere a síntese e as passagens de suporte (ordinal = posição).

        A FK composta `summary_supports(passage_id, edition_id)` garante que
        cada suporte pertença à edição da síntese — o banco, não só o serviço,
        impõe a integridade (R01).
        """
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO summaries (id, edition_id, scope_type, section_id, text,
                                       generator_version_id, created_at)
                VALUES (%(id)s, %(edition_id)s, %(scope_type)s, %(section_id)s, %(text)s,
                        %(generator_version_id)s, %(created_at)s)
                """,
                {
                    "id": summary.id,
                    "edition_id": summary.edition_id,
                    "scope_type": summary.scope_type.value,
                    "section_id": summary.section_id,
                    "text": summary.text,
                    "generator_version_id": summary.generator_version_id,
                    "created_at": summary.created_at,
                },
            )
            for ordinal, passage_id in enumerate(summary.supporting_passage_ids):
                await cur.execute(
                    """
                    INSERT INTO summary_supports (summary_id, passage_id, edition_id, ordinal)
                    VALUES (%(summary_id)s, %(passage_id)s, %(edition_id)s, %(ordinal)s)
                    """,
                    {
                        "summary_id": summary.id,
                        "passage_id": passage_id,
                        "edition_id": summary.edition_id,
                        "ordinal": ordinal,
                    },
                )

    async def list_by_edition(self, edition_id: UUID) -> list[Summary]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT s.id, s.edition_id, s.scope_type, s.section_id, s.text, "
                "s.generator_version_id, s.created_at, "
                "COALESCE(array_agg(ss.passage_id ORDER BY ss.ordinal) "
                "FILTER (WHERE ss.passage_id IS NOT NULL), '{}') AS supporting_passage_ids "
                "FROM summaries s "
                "LEFT JOIN summary_supports ss ON ss.summary_id = s.id "
                "WHERE s.edition_id = %s "
                "GROUP BY s.id "
                "ORDER BY s.created_at, s.scope_type, s.section_id",
                (edition_id,),
            )
            rows = await cur.fetchall()
        summaries: list[Summary] = []
        for row in rows:
            row["scope_type"] = SummaryScope(row["scope_type"])
            summaries.append(Summary(**row))
        return summaries

    async def supporting_passages(self, summary_id: UUID) -> list[Passage]:
        """Recuperação descendente: as passagens originais que sustentam a síntese."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_PASSAGE_COLUMNS} FROM summary_supports ss "  # noqa: S608
                "JOIN passages p ON p.id = ss.passage_id "
                "WHERE ss.summary_id = %s ORDER BY ss.ordinal",
                (summary_id,),
            )
            return [_passage_from_row(row) for row in await cur.fetchall()]

    async def select_nodes(
        self,
        *,
        query: str,
        filters: EditionFilter,
        limit: int,
    ) -> list[tuple[UUID, float]]:
        """Seleciona sínteses relevantes via FTS sobre o texto (R04; SPEC §8.7).

        Só são elegíveis sínteses com pelo menos uma passagem de suporte da
        execução de indexação/enriquecimento vigente da sua edição, e dentro
        dos filtros — nunca se seleciona um nó cujo suporte está excluído ou
        inativo (B03/R04, AC-07). Devolve pares (node_id, score) ordenados por
        score desc, desempate por id — determinístico.
        """
        filters = filters if filters is not None else EditionFilter()
        conditions, params = _edition_filter_conditions(filters)
        params["query"] = query
        params["limit"] = limit
        sql = (
            f"SELECT s.id AS node_id, "  # noqa: S608 - allowlist fixa, valores parametrizados
            f"MAX(ts_rank_cd(to_tsvector('{_FTS_CONFIG}', s.text), "
            f"plainto_tsquery('{_FTS_CONFIG}', %(query)s))) AS score "
            "FROM summaries s "
            "JOIN summary_supports ss ON ss.summary_id = s.id "
            "  AND ss.edition_id = s.edition_id "
            "JOIN passages p ON p.id = ss.passage_id AND p.edition_id = ss.edition_id "
            "JOIN editions e ON e.id = p.edition_id "
            f"WHERE to_tsvector('{_FTS_CONFIG}', s.text) @@ "
            f"plainto_tsquery('{_FTS_CONFIG}', %(query)s) "
            f"AND {_ACTIVE_RUN_CONDITION}{_filters_sql(conditions)} "
            "GROUP BY s.id ORDER BY score DESC, s.id LIMIT %(limit)s"
        )
        async with self._conn.cursor() as cur:
            await cur.execute(sql, params)
            return [(row[0], float(row[1])) for row in await cur.fetchall()]

    async def supporting_passages_current(
        self,
        summary_id: UUID,
        *,
        filters: EditionFilter,
        limit: int,
    ) -> list[Passage]:
        """Recuperação descendente limitada à execução vigente e filtros (R04).

        Desce até as passagens ORIGINAIS de suporte, aplicando ANTES da
        seleção o conjunto corrente (`_ACTIVE_RUN_CONDITION`) e os filtros de
        obra/edição (AC-07). A síntese NUNCA é devolvida como passagem — só as
        passagens que a sustentam (AC-12).
        """
        filters = filters if filters is not None else EditionFilter()
        conditions, params = _edition_filter_conditions(filters)
        params["summary_id"] = summary_id
        params["limit"] = limit
        sql = (
            f"SELECT {_PASSAGE_COLUMNS} FROM summary_supports ss "  # noqa: S608
            "JOIN passages p ON p.id = ss.passage_id AND p.edition_id = ss.edition_id "
            "JOIN editions e ON e.id = p.edition_id "
            f"WHERE ss.summary_id = %(summary_id)s "
            f"AND {_ACTIVE_RUN_CONDITION}{_filters_sql(conditions)} "
            "ORDER BY ss.ordinal LIMIT %(limit)s"
        )
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return [_passage_from_row(row) for row in await cur.fetchall()]


class ConceptsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def get_or_create(self, concept: Concept) -> Concept:
        """Idempotente por `normalized_label` (UNIQUE no banco). Se o conceito
        já existir, devolve o registro existente — estado e descrição não são
        sobrescritos (curatoria futura, SPEC §5.1)."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO concepts (id, normalized_label, description, state, created_at)
                VALUES (%(id)s, %(normalized_label)s, %(description)s, %(state)s, %(created_at)s)
                ON CONFLICT (normalized_label) DO NOTHING
                """,
                {
                    "id": concept.id,
                    "normalized_label": concept.normalized_label,
                    "description": concept.description,
                    "state": concept.state.value,
                    "created_at": concept.created_at,
                },
            )
            await cur.execute(
                "SELECT id, normalized_label, description, state, created_at "
                "FROM concepts WHERE normalized_label = %s",
                (concept.normalized_label,),
            )
            row = await cur.fetchone()
        if row is None:  # pragma: no cover - defensivo
            raise RuntimeError("falha ao ler conceito recém-criado")
        row["state"] = ConceptState(row["state"])
        return Concept(**row)

    async def add_alias(self, concept_id: UUID, expression: str, confidence: float) -> None:
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO concept_aliases (concept_id, expression, confidence)
                VALUES (%s, %s, %s)
                ON CONFLICT (concept_id, expression) DO NOTHING
                """,
                (concept_id, expression, confidence),
            )

    async def add_evidence(
        self, concept_id: UUID, passage_id: UUID, confidence: float, extractor_version_id: UUID
    ) -> None:
        """Idempotente por (concept_id, passage_id, extractor_version_id):
        reexecução com a MESMA versão não duplica; com versão NOVA acumula —
        histórico preservado (AC-15)."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO concept_evidence (concept_id, passage_id, confidence,
                                              extractor_version_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (concept_id, passage_id, extractor_version_id) DO NOTHING
                """,
                (concept_id, passage_id, confidence, extractor_version_id),
            )

    async def supporting_passages(self, concept_id: UUID) -> list[Passage]:
        """Recuperação descendente: as passagens originais que sustentam o conceito."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT DISTINCT {_PASSAGE_COLUMNS} FROM concept_evidence ce "  # noqa: S608
                "JOIN passages p ON p.id = ce.passage_id "
                "WHERE ce.concept_id = %s ORDER BY p.ordinal",
                (concept_id,),
            )
            return [_passage_from_row(row) for row in await cur.fetchall()]

    async def select_nodes(
        self,
        *,
        query: str,
        filters: EditionFilter,
        limit: int,
    ) -> list[tuple[UUID, float]]:
        """Seleciona conceitos relevantes via FTS sobre rótulo+aliases (R04).

        Um conceito só é elegível através de evidências de passagens da
        execução de indexação/enriquecimento vigente, e dentro dos filtros —
        nunca se seleciona um conceito cujas evidências estão excluídas ou
        inativas (B03/R04, AC-07). O score é o MAXIMO sobre rótulo e aliases
        (um conceito pode corresponder por vários canais). Devolve pares
        (node_id, score) determinísticos.
        """
        filters = filters if filters is not None else EditionFilter()
        conditions, params = _edition_filter_conditions(filters)
        params["query"] = query
        params["limit"] = limit
        text_expr = "c.normalized_label || ' ' || COALESCE(ca.expression, '')"
        sql = (
            "SELECT c.id AS node_id, "  # noqa: S608 - allowlist fixa, valores parametrizados
            f"MAX(ts_rank_cd(to_tsvector('{_FTS_CONFIG}', {text_expr}), "
            f"plainto_tsquery('{_FTS_CONFIG}', %(query)s))) AS score "
            "FROM concepts c "
            "LEFT JOIN concept_aliases ca ON ca.concept_id = c.id "
            "JOIN concept_evidence ce ON ce.concept_id = c.id "
            "JOIN passages p ON p.id = ce.passage_id "
            "JOIN editions e ON e.id = p.edition_id "
            f"WHERE to_tsvector('{_FTS_CONFIG}', {text_expr}) @@ "
            f"plainto_tsquery('{_FTS_CONFIG}', %(query)s) "
            f"AND {_ACTIVE_RUN_CONDITION}{_filters_sql(conditions)} "
            "GROUP BY c.id ORDER BY score DESC, c.id LIMIT %(limit)s"
        )
        async with self._conn.cursor() as cur:
            await cur.execute(sql, params)
            return [(row[0], float(row[1])) for row in await cur.fetchall()]

    async def supporting_passages_current(
        self,
        concept_id: UUID,
        *,
        filters: EditionFilter,
        limit: int,
    ) -> list[Passage]:
        """Recuperação descendente de conceito limitada a execução vigente (R04).

        Desce até as passagens ORIGINAIS de evidência, aplicando ANTES da
        seleção o conjunto corrente (`_ACTIVE_RUN_CONDITION`) e os filtros de
        obra/edição (AC-07). O conceito NUNCA é devolvido como passagem — só as
        passagens que o sustentam (AC-12).
        """
        filters = filters if filters is not None else EditionFilter()
        conditions, params = _edition_filter_conditions(filters)
        params["concept_id"] = concept_id
        params["limit"] = limit
        sql = (
            f"SELECT DISTINCT {_PASSAGE_COLUMNS} FROM concept_evidence ce "  # noqa: S608
            "JOIN passages p ON p.id = ce.passage_id "
            "JOIN editions e ON e.id = p.edition_id "
            f"WHERE ce.concept_id = %(concept_id)s "
            f"AND {_ACTIVE_RUN_CONDITION}{_filters_sql(conditions)} "
            "ORDER BY p.ordinal LIMIT %(limit)s"
        )
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return [_passage_from_row(row) for row in await cur.fetchall()]

    async def count_for_edition(self, edition_id: UUID) -> int:
        """Número de conceitos distintos com evidência na edição."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT count(DISTINCT ce.concept_id) FROM concept_evidence ce "
                "JOIN passages p ON p.id = ce.passage_id WHERE p.edition_id = %s",
                (edition_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    async def count_evidence_for_edition(self, edition_id: UUID) -> int:
        """Número de linhas de evidência apontando a passagens da edição."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM concept_evidence ce "
                "JOIN passages p ON p.id = ce.passage_id WHERE p.edition_id = %s",
                (edition_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    async def list_all(self, *, limit: int = 1000) -> list[Concept]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, normalized_label, description, state, created_at "
                "FROM concepts ORDER BY normalized_label LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        concepts: list[Concept] = []
        for row in rows:
            row["state"] = ConceptState(row["state"])
            concepts.append(Concept(**row))
        return concepts
