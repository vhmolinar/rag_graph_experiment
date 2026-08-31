"""Repository de AnswerRun (rastreabilidade de execuções)."""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rag.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from rag.domain.runs import AnswerRun

_JSONB_COLUMNS = (
    "explicit_filters",
    "inferred_filters",
    "plan",
    "candidates",
    "selected_evidence_ids",
    "response",
    "verification",
    "versions",
    "latencies",
)


def _revalidate(run: AnswerRun) -> AnswerRun:
    """Revalida o dump completo antes de persistir (RR02).

    Instâncias produzidas fora do caminho normal (ex.: `model_copy(update=...)`,
    que não executa validators) são rejeitadas aqui.
    """
    return AnswerRun.model_validate(run.model_dump(mode="json"))


class AnswerRunsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, run: AnswerRun) -> AnswerRun:
        run = _revalidate(run)
        data = run.model_dump(mode="json")
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO answer_runs (id, session_id, status, question_original,
                                         question_anonymized, rewritten_query,
                                         explicit_filters, inferred_filters, plan,
                                         candidates, selected_evidence_ids, response,
                                         verification, versions, latencies, error_code,
                                         error_message, created_at)
                VALUES (%(id)s, %(session_id)s, %(status)s, %(question_original)s,
                        %(question_anonymized)s, %(rewritten_query)s,
                        %(explicit_filters)s, %(inferred_filters)s, %(plan)s,
                        %(candidates)s, %(selected_evidence_ids)s, %(response)s,
                        %(verification)s, %(versions)s, %(latencies)s, %(error_code)s,
                        %(error_message)s, %(created_at)s)
                """,
                {
                    "id": run.id,
                    "session_id": run.session_id,
                    "status": run.status.value,
                    "question_original": run.question_original,
                    "question_anonymized": run.question_anonymized,
                    "rewritten_query": run.rewritten_query,
                    "explicit_filters": Jsonb(data["explicit_filters"]),
                    "inferred_filters": Jsonb(data["inferred_filters"])
                    if data["inferred_filters"] is not None
                    else None,
                    "plan": Jsonb(data["plan"]) if data["plan"] is not None else None,
                    "candidates": Jsonb(data["candidates"]),
                    "selected_evidence_ids": Jsonb(data["selected_evidence_ids"]),
                    "response": Jsonb(data["response"]) if data["response"] is not None else None,
                    "verification": Jsonb(data["verification"])
                    if data["verification"] is not None
                    else None,
                    "versions": Jsonb(data["versions"]),
                    "latencies": Jsonb(data["latencies"]),
                    "error_code": run.error_code.value if run.error_code else None,
                    "error_message": run.error_message,
                    "created_at": run.created_at,
                },
            )
        return run

    async def save(self, run: AnswerRun) -> AnswerRun:
        """Atualiza o registro de uma execução em andamento/finalizada.

        Rejeita ID inexistente (R05) e revalida o dump completo (RR02).
        RRR01: o UPDATE é limitado à mesma allowlist de progresso do domínio;
        campos imutáveis (pergunta original/anônima, filtros explícitos,
        created_at) são comparados com o registro existente e qualquer
        divergência levanta ConflictError — nunca são atualizados.

        R4-01: controle otimista — o UPDATE exige `revision` igual à lida;
        zero linhas com ID existente significa perda de corrida e vira
        ConcurrencyError (nunca NotFoundError). A revisão é incrementada
        atomicamente e o objeto retornado é relido do banco.
        """
        run = _revalidate(run)
        data = run.model_dump(mode="json")
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT question_original, question_anonymized, explicit_filters, "
                "created_at FROM answer_runs WHERE id = %(id)s",
                {"id": run.id},
            )
            existing = await cur.fetchone()
            if existing is None:
                raise NotFoundError(
                    "Execução não encontrada para atualização.",
                    context={"run_id": str(run.id)},
                )
            divergent = [
                field
                for field, current in (
                    ("question_original", run.question_original),
                    ("question_anonymized", run.question_anonymized),
                    ("explicit_filters", data["explicit_filters"]),
                    ("created_at", run.created_at),
                )
                if existing[field] != current
            ]
            if divergent:
                raise ConflictError(
                    "Campos imutáveis da execução divergem do registro.",
                    context={"run_id": str(run.id), "fields": divergent},
                )
            await cur.execute(
                """
                UPDATE answer_runs SET
                    session_id = %(session_id)s,
                    status = %(status)s,
                    rewritten_query = %(rewritten_query)s,
                    inferred_filters = %(inferred_filters)s,
                    plan = %(plan)s,
                    candidates = %(candidates)s,
                    selected_evidence_ids = %(selected_evidence_ids)s,
                    response = %(response)s,
                    verification = %(verification)s,
                    versions = %(versions)s,
                    latencies = %(latencies)s,
                    error_code = %(error_code)s,
                    error_message = %(error_message)s,
                    revision = revision + 1
                WHERE id = %(id)s AND revision = %(expected_revision)s
                RETURNING id
                """,
                {
                    "id": run.id,
                    "session_id": run.session_id,
                    "status": run.status.value,
                    "rewritten_query": run.rewritten_query,
                    "inferred_filters": Jsonb(data["inferred_filters"])
                    if data["inferred_filters"] is not None
                    else None,
                    "plan": Jsonb(data["plan"]) if data["plan"] is not None else None,
                    "candidates": Jsonb(data["candidates"]),
                    "selected_evidence_ids": Jsonb(data["selected_evidence_ids"]),
                    "response": Jsonb(data["response"]) if data["response"] is not None else None,
                    "verification": Jsonb(data["verification"])
                    if data["verification"] is not None
                    else None,
                    "versions": Jsonb(data["versions"]),
                    "latencies": Jsonb(data["latencies"]),
                    "error_code": run.error_code.value if run.error_code else None,
                    "error_message": run.error_message,
                    "expected_revision": run.revision,
                },
            )
            if await cur.fetchone() is None:
                raise ConcurrencyError(
                    "Execução modificada concorrentemente; recarregue e tente de novo.",
                    context={"run_id": str(run.id), "expected_revision": run.revision},
                )
        updated = await self.get(run.id)
        if updated is None:  # pragma: no cover - defensivo: o UPDATE acabou de gravar
            raise RuntimeError("execução não encontrada após atualização")
        return updated

    async def get(self, run_id: UUID) -> AnswerRun | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, session_id, status, question_original, question_anonymized, "
                "rewritten_query, explicit_filters, inferred_filters, plan, candidates, "
                "selected_evidence_ids, response, verification, versions, latencies, "
                "error_code, error_message, created_at, revision FROM answer_runs WHERE id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return AnswerRun(**row)
