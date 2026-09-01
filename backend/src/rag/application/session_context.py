"""Serviço de contexto de sessão (T15; SPEC §10.3, AC-13).

Carga o histórico limitado da sessão (perguntas + projeção truncada das
respostas), resolve o follow-up numa pergunta autônoma (determinística, no
domínio — decisão do usuário em T15, NOTAS.md §10.16) e registra a rodada em
`session_entries`. Nunca usa contexto de outra sessão: o histórico é lido
exclusivamente por `session_id`.
"""

from uuid import UUID

from psycopg import AsyncConnection

from rag.domain.answer import GeneratedAnswer, QuoteResponse
from rag.domain.enums import ContributorRole
from rag.domain.planning import normalize_text
from rag.domain.sessions import (
    MAX_ANSWER_CONTEXT_CHARS,
    MAX_SESSION_HISTORY,
    MAX_SESSION_PROMPT_CONTEXT_CHARS,
    RewriteResult,
    SessionCatalogEntry,
    SessionEntry,
    SessionTurn,
    rewrite_follow_up,
)
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.sessions import SessionsRepository
from rag.infrastructure.repositories.works import WorksRepository


class SessionContextService:
    """Contexto efêmero da sessão: histórico limitado + reescrita de follow-up."""

    def __init__(self, *, max_history: int = MAX_SESSION_HISTORY) -> None:
        self._max_history = max_history

    async def rewrite(
        self,
        conn: AsyncConnection,
        *,
        session_id: UUID,
        question: str,
    ) -> RewriteResult:
        """Resolve o follow-up numa pergunta autônoma (só leitura, AC-13)."""
        entries = await SessionsRepository(conn).list_entries(session_id, limit=self._max_history)
        turns = await self._load_turns(conn, entries)
        catalog = await self._load_catalog(conn)
        return rewrite_follow_up(question, turns, catalog)

    async def record(
        self,
        conn: AsyncConnection,
        *,
        session_id: UUID,
        question: str,
        autonomous_question: str,
        answer_run_id: UUID,
    ) -> None:
        """Registra a rodada em `session_entries` (pergunta original + autônoma)."""
        rewritten = autonomous_question if autonomous_question != question else None
        await SessionsRepository(conn).append_entry(
            session_id=session_id,
            question_original=question,
            question_anonymized=question,  # anonimização é responsabilidade de T18
            rewritten_query=rewritten,
            answer_run_id=answer_run_id,
        )

    async def prompt_context(
        self,
        conn: AsyncConnection,
        *,
        session_id: UUID,
    ) -> str | None:
        """Contexto de sessão para o gerador (SPEC §9.3: pergunta e contexto da
        sessão), limitado às rodadas mais recentes dentro do orçamento."""
        entries = await SessionsRepository(conn).list_entries(session_id, limit=self._max_history)
        turns = await self._load_turns(conn, entries)
        return _build_prompt_context(turns)

    @staticmethod
    async def _load_turns(
        conn: AsyncConnection, entries: list[SessionEntry]
    ) -> tuple[SessionTurn, ...]:
        runs = AnswerRunsRepository(conn)
        turns: list[SessionTurn] = []
        for entry in entries:
            answer_text = None
            if entry.answer_run_id is not None:
                run = await runs.get(entry.answer_run_id)
                if run is not None:
                    answer_text = _project_answer_text(run.response)
            turns.append(
                SessionTurn(
                    ordinal=entry.ordinal,
                    question_original=entry.question_original,
                    rewritten_query=entry.rewritten_query,
                    answer_text=answer_text,
                )
            )
        return tuple(turns)

    @staticmethod
    async def _load_catalog(conn: AsyncConnection) -> dict[str, SessionCatalogEntry]:
        catalog: dict[str, SessionCatalogEntry] = {}
        for work in await WorksRepository(conn).list_all():
            catalog[normalize_text(work.canonical_title)] = SessionCatalogEntry(
                work_id=work.id,
                title=work.canonical_title,
                authors=tuple(
                    author.name for author in work.authors if author.role is ContributorRole.AUTHOR
                ),
            )
        return catalog


def _project_answer_text(response: QuoteResponse | GeneratedAnswer | None) -> str | None:
    """Projeção truncada da resposta da rodada para o contexto de reescrita."""
    if isinstance(response, QuoteResponse):
        text = " ".join(evidence.text for evidence in response.evidences)
    elif isinstance(response, GeneratedAnswer):
        text = response.answer_markdown
    else:
        return None
    if not text.strip():
        return None
    return text[:MAX_ANSWER_CONTEXT_CHARS]


def _build_prompt_context(turns: tuple[SessionTurn, ...]) -> str | None:
    """Contexto de sessão para o prompt do gerador, dentro do orçamento.

    As rodadas mais recentes primeiro; a mais antiga é descartada quando o
    orçamento não couber. A pergunta autônoma registrada (reescrita) aparece
    quando existe (AC-13)."""
    if not turns:
        return None
    blocks: list[str] = []
    used = 0
    for turn in reversed(turns):
        block = f"Q: {turn.question_original}"
        if turn.rewritten_query and turn.rewritten_query != turn.question_original:
            block += f"\n(reescrita autônoma: {turn.rewritten_query})"
        if turn.answer_text:
            block += f"\nA: {turn.answer_text}"
        if used + len(block) > MAX_SESSION_PROMPT_CONTEXT_CHARS:
            break
        blocks.append(block)
        used += len(block)
    if not blocks:
        return None
    return "Histórico da sessão:\n" + "\n\n".join(reversed(blocks))
