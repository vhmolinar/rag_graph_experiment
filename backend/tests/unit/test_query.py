"""Invariantes de QueryRequest, EditionFilter e QueryPlan (T02, AC-07)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.enums import AnswerMode, Intent, SearchStrategy
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, QueryRequest


class TestQueryRequest:
    def test_question_is_stripped_and_required(self) -> None:
        request = QueryRequest(question="  O que é spleen?  ", answer_mode=AnswerMode.QUOTE)
        assert request.question == "O que é spleen?"

    def test_blank_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryRequest(question="   ", answer_mode=AnswerMode.QUOTE)

    def test_same_edition_in_include_and_exclude_rejected(self) -> None:
        edition_id = uuid4()
        with pytest.raises(ValidationError, match="include e exclude"):
            QueryRequest(
                question="q",
                answer_mode=AnswerMode.QUOTE,
                include_edition_ids=[edition_id],
                exclude_edition_ids=[edition_id],
            )

    def test_explicit_filter_conversion(self) -> None:
        inc, exc = uuid4(), uuid4()
        request = QueryRequest(
            question="q",
            answer_mode=AnswerMode.DISSERTATIVE,
            include_edition_ids=[inc],
            exclude_edition_ids=[exc],
        )
        explicit = request.explicit_filter()
        assert explicit.include_edition_ids == {inc}
        assert explicit.exclude_edition_ids == {exc}

    def test_defaults(self) -> None:
        request = QueryRequest(question="q", answer_mode=AnswerMode.QUOTE)
        assert request.depth.value == "standard"
        assert request.search_strategy is SearchStrategy.AUTOMATIC
        assert request.session_id is None


class TestEditionFilter:
    def test_work_level_conflict_rejected(self) -> None:
        work_id = uuid4()
        with pytest.raises(ValidationError, match="obra"):
            EditionFilter(
                include_work_ids=frozenset({work_id}),
                exclude_work_ids=frozenset({work_id}),
            )

    def test_empty_filter(self) -> None:
        assert EditionFilter().is_empty()
        assert not EditionFilter(exclude_edition_ids=frozenset({uuid4()})).is_empty()


class TestLexicalQuery:
    def test_requires_phrase_or_required_terms(self) -> None:
        with pytest.raises(ValidationError, match="frase exata ou ao menos um termo"):
            LexicalQuery()

    def test_phrase_alone_is_valid(self) -> None:
        query = LexicalQuery(phrase="dom casmurro")
        assert query.required_terms == ()

    def test_required_terms_alone_is_valid(self) -> None:
        query = LexicalQuery(required_terms=("capitu", "bentinho"))
        assert query.phrase is None

    def test_blank_term_rejected(self) -> None:
        with pytest.raises(ValidationError, match="não pode ser vazio"):
            LexicalQuery(required_terms=("  ",))

    def test_multi_word_term_rejected(self) -> None:
        """Sequências de várias palavras pertencem ao campo `phrase`, não a
        `required_terms`/`excluded_terms` (cada termo é uma única palavra)."""
        with pytest.raises(ValidationError, match="única palavra alfanumérica"):
            LexicalQuery(required_terms=("dom casmurro",))

    def test_punctuation_in_term_rejected(self) -> None:
        with pytest.raises(ValidationError, match="única palavra alfanumérica"):
            LexicalQuery(required_terms=("ciume!",))

    def test_same_term_required_and_excluded_rejected(self) -> None:
        with pytest.raises(ValidationError, match="obrigatório e excluído"):
            LexicalQuery(required_terms=("ciume",), excluded_terms=("ciume",))

    def test_trigram_threshold_bounds(self) -> None:
        with pytest.raises(ValidationError):
            LexicalQuery(required_terms=("ciume",), trigram_threshold=1.5)
        with pytest.raises(ValidationError):
            LexicalQuery(required_terms=("ciume",), trigram_threshold=-0.1)

    def test_default_threshold(self) -> None:
        query = LexicalQuery(required_terms=("ciume",))
        assert query.trigram_threshold == 0.3

    def test_frozen(self) -> None:
        query = LexicalQuery(required_terms=("ciume",))
        with pytest.raises(ValidationError):
            query.required_terms = ("outro",)


class TestQueryPlan:
    def _plan(
        self,
        strategy: SearchStrategy = SearchStrategy.HYBRID,
        subquestions: list[str] | None = None,
        needs_diversity: bool = False,
    ) -> QueryPlan:
        return QueryPlan(
            intent=Intent.FACTUAL,
            lexical_query="spleen",
            semantic_query="tédio existencial",
            strategy=strategy,
            justification="consulta factual curta",
            subquestions=tuple(subquestions or ()),
            needs_diversity=needs_diversity,
        )

    def test_resolved_strategy_is_never_automatic(self) -> None:
        """SPEC §8.3: automatic escolhe uma estratégia concreta e registra."""
        with pytest.raises(ValidationError, match="automatic"):
            self._plan(strategy=SearchStrategy.AUTOMATIC)

    def test_subquestions_limited(self) -> None:
        with pytest.raises(ValidationError):
            self._plan(subquestions=["q"] * 6)

    def test_valid_plan(self) -> None:
        plan = self._plan(needs_diversity=True)
        assert plan.needs_diversity
        assert plan.inferred_filters.is_empty()
