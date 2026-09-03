"""Invariantes do AnswerRun (T02; AC-06, AC-15)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer, QuoteResponse
from rag.domain.enums import QueryStatus, RankingStage
from rag.domain.errors import ErrorCode, InvalidTransitionError
from rag.domain.query import EditionFilter
from rag.domain.runs import AnswerRun, RankedCandidate, StageLatency, VersionSet
from rag.domain.versions import utcnow


def _run(
    status: QueryStatus = QueryStatus.QUEUED,
    error_code: ErrorCode | None = None,
    response: GeneratedAnswer | QuoteResponse | None = None,
    candidates: list[RankedCandidate] | tuple[RankedCandidate, ...] | None = None,
    versions: VersionSet | None = None,
    latencies: list[StageLatency] | tuple[StageLatency, ...] | None = None,
) -> AnswerRun:
    return AnswerRun(
        question_original="O que é spleen?",
        question_anonymized="O que é spleen?",
        explicit_filters=EditionFilter(),
        status=status,
        error_code=error_code,
        response=response,
        candidates=tuple(candidates or ()),
        versions=versions or VersionSet(),
        latencies=tuple(latencies or ()),
    )


class TestAnswerRun:
    def test_failed_requires_error_code(self) -> None:
        with pytest.raises(ValidationError, match="error_code"):
            _run(status=QueryStatus.FAILED)

    def test_failed_with_error_code(self) -> None:
        run = _run(status=QueryStatus.FAILED, error_code=ErrorCode.MODEL_TIMEOUT)
        assert run.error_code is ErrorCode.MODEL_TIMEOUT

    def test_succeeded_requires_response(self) -> None:
        with pytest.raises(ValidationError, match="response"):
            _run(status=QueryStatus.SUCCEEDED)

    def test_succeeded_with_quote_response(self) -> None:
        run = _run(status=QueryStatus.SUCCEEDED, response=QuoteResponse())
        assert run.status is QueryStatus.SUCCEEDED

    def test_abstained_requires_abstained_generated_answer(self) -> None:
        with pytest.raises(ValidationError, match="abstained"):
            _run(status=QueryStatus.ABSTAINED, response=QuoteResponse())
        run = _run(
            status=QueryStatus.ABSTAINED,
            response=GeneratedAnswer(
                answer_markdown="",
                abstained=True,
                abstention_reason="sem suporte",
            ),
        )
        assert run.status is QueryStatus.ABSTAINED

    def test_candidates_record_all_stages(self) -> None:
        """AC-06: rankings lexical, vetorial, RRF e reranking preservados."""
        passage = uuid4()
        run = _run(
            candidates=[
                RankedCandidate(passage_id=passage, stage=RankingStage.LEXICAL, score=12.5, rank=0),
                RankedCandidate(passage_id=passage, stage=RankingStage.VECTOR, score=0.91, rank=2),
                RankedCandidate(passage_id=passage, stage=RankingStage.FUSED, score=0.033, rank=1),
                RankedCandidate(
                    passage_id=passage, stage=RankingStage.RERANKED, score=0.87, rank=0
                ),
            ]
        )
        stages = {c.stage for c in run.candidates}
        assert stages == set(RankingStage)

    def test_versions_and_latencies(self) -> None:
        run = _run(
            versions=VersionSet(chunking_version_id=uuid4(), embedding_version_id=uuid4()),
            latencies=[StageLatency(stage="lexical", duration_ms=12.3)],
        )
        assert run.versions.chunking_version_id is not None
        assert run.latencies[0].duration_ms >= 0


class TestTransitions:
    """R05: transições de estado só via `transition`, com revalidação completa."""

    def test_frozen_blocks_direct_assignment(self) -> None:
        run = _run()
        with pytest.raises(ValidationError):
            run.status = QueryStatus.SUCCEEDED

    def test_transition_with_limitations(self) -> None:
        run = _run().transition(QueryStatus.RUNNING)
        succeeded = run.transition(
            QueryStatus.SUCCEEDED,
            response=QuoteResponse(),
            limitations=("limitação declarada",),
        )
        assert succeeded.limitations == ("limitação declarada",)

    def test_default_limitations_is_empty_tuple(self) -> None:
        run = _run()
        assert run.limitations == ()
        done = run.transition(QueryStatus.RUNNING).transition(
            QueryStatus.SUCCEEDED, response=QuoteResponse()
        )
        assert done.status is QueryStatus.SUCCEEDED
        assert done.id == run.id
        assert done.limitations == ()

    def test_valid_path_queued_running_succeeded(self) -> None:
        run = _run().transition(QueryStatus.RUNNING)
        assert run.status is QueryStatus.RUNNING
        done = run.transition(QueryStatus.SUCCEEDED, response=QuoteResponse())
        assert done.status is QueryStatus.SUCCEEDED
        assert done.id == run.id

    def test_regressive_transition_rejected(self) -> None:
        done = _run(status=QueryStatus.SUCCEEDED, response=QuoteResponse())
        with pytest.raises(InvalidTransitionError):
            done.transition(QueryStatus.RUNNING)
        with pytest.raises(InvalidTransitionError):
            done.transition(QueryStatus.QUEUED)

    def test_skipping_states_rejected(self) -> None:
        with pytest.raises(InvalidTransitionError):
            _run().transition(QueryStatus.SUCCEEDED, response=QuoteResponse())

    def test_succeeded_without_response_rejected_on_revalidation(self) -> None:
        running = _run().transition(QueryStatus.RUNNING)
        with pytest.raises(ValidationError, match="response"):
            running.transition(QueryStatus.SUCCEEDED)

    def test_failed_without_error_code_rejected_on_revalidation(self) -> None:
        running = _run().transition(QueryStatus.RUNNING)
        with pytest.raises(ValidationError, match="error_code"):
            running.transition(QueryStatus.FAILED)

    def test_abstained_requires_abstained_response_on_revalidation(self) -> None:
        running = _run().transition(QueryStatus.RUNNING)
        with pytest.raises(ValidationError, match="abstained"):
            running.transition(QueryStatus.ABSTAINED, response=QuoteResponse())
        ok = running.transition(
            QueryStatus.ABSTAINED,
            response=GeneratedAnswer(answer_markdown="", abstained=True, abstention_reason="x"),
        )
        assert ok.status is QueryStatus.ABSTAINED

    def test_terminal_states_have_no_outgoing_transitions(self) -> None:
        terminal_runs = [
            _run(status=QueryStatus.SUCCEEDED, response=QuoteResponse()),
            _run(status=QueryStatus.FAILED, error_code=ErrorCode.INTERNAL_ERROR),
            _run(status=QueryStatus.CANCELLED),
        ]
        for run in terminal_runs:
            for target in QueryStatus:
                with pytest.raises(InvalidTransitionError):
                    run.transition(target)


class TestDeepImmutability:
    """RR02: mutações profundas e mudanças de campos proibidos são rejeitadas."""

    def test_candidates_tuple_rejects_append(self) -> None:
        run = _run(
            candidates=[
                RankedCandidate(passage_id=uuid4(), stage=RankingStage.LEXICAL, score=1.0, rank=0)
            ]
        )
        with pytest.raises(AttributeError):
            run.candidates.append(  # type: ignore[attr-defined]
                RankedCandidate(passage_id=uuid4(), stage=RankingStage.VECTOR, score=0.5, rank=1)
            )

    def test_versionset_collections_reject_append(self) -> None:
        run = _run(versions=VersionSet(prompt_version_ids=(uuid4(),)))
        with pytest.raises(AttributeError):
            run.versions.prompt_version_ids.append(uuid4())  # type: ignore[attr-defined]

    def test_nested_response_is_frozen(self) -> None:
        claim = Claim(id="c1", text="afirmação", evidence_ids=(uuid4(),))
        blocks = (
            AnswerBlock(text=claim.text, claim_id="c1"),
            AnswerBlock(text=" "),
        )
        response = GeneratedAnswer(
            answer_markdown="".join(block.text for block in blocks),
            blocks=blocks,
            claims=(claim,),
            abstained=False,
        )
        with pytest.raises(ValidationError):
            response.answer_markdown = "alterado"
        with pytest.raises(AttributeError):
            response.claims.append(Claim(id="c2", text="x", evidence_ids=(uuid4(),)))  # type: ignore[attr-defined]

    def test_transition_rejects_forbidden_fields(self) -> None:
        run = _run()
        for forbidden in (
            {"id": uuid4()},
            {"question_original": "alterada"},
            {"question_anonymized": "alterada"},
            {"explicit_filters": EditionFilter()},
            {"created_at": utcnow()},
            {"request_id": "req-alterado"},
        ):
            with pytest.raises(InvalidTransitionError, match="não permitidos"):
                run.transition(QueryStatus.RUNNING, **forbidden)

    def test_versions_once_set_cannot_change(self) -> None:
        chunking_id = uuid4()
        run = _run().transition(
            QueryStatus.RUNNING, versions=VersionSet(chunking_version_id=chunking_id)
        )
        with pytest.raises(InvalidTransitionError, match="já registrada"):
            run.transition(
                QueryStatus.SUCCEEDED,
                response=QuoteResponse(),
                versions=VersionSet(chunking_version_id=uuid4()),
            )
        ok = run.transition(
            QueryStatus.SUCCEEDED,
            response=QuoteResponse(),
            versions=VersionSet(chunking_version_id=chunking_id, embedding_version_id=uuid4()),
        )
        assert ok.versions.chunking_version_id == chunking_id
        assert ok.versions.embedding_version_id is not None

    def test_candidates_are_append_only(self) -> None:
        first = RankedCandidate(passage_id=uuid4(), stage=RankingStage.LEXICAL, score=1.0, rank=0)
        run = _run().transition(QueryStatus.RUNNING, candidates=[first])
        with pytest.raises(InvalidTransitionError, match="append-only"):
            run.transition(QueryStatus.RUNNING, candidates=[])
        second = RankedCandidate(passage_id=uuid4(), stage=RankingStage.VECTOR, score=0.9, rank=0)
        grown = run.transition(QueryStatus.RUNNING, candidates=[first, second])
        assert len(grown.candidates) == 2
