"""Núcleo determinístico de planejamento (T10; SPEC §8.2-§8.3, AC-07, AC-11).

Cobre: classificação de intenção (factual, conceitual, comparativa,
navegacional); construção da consulta lexical estruturada; resolução de
estratégia (explícita e automática) com explicação estruturada; diversidade
adaptativa; resolução de filtros naturais (inclusão/exclusão explícita,
ambigüidade não aplicada silenciosamente); prioridade de filtros explícitos.
"""

from uuid import uuid4

from rag.domain.enums import Intent, SearchStrategy
from rag.domain.planning import (
    CatalogEntry,
    build_lexical_query,
    build_semantic_query,
    classify_intent,
    diversity_for,
    hierarchical_for,
    merge_filters,
    resolve_natural_filters,
    resolve_strategy,
)
from rag.domain.query import EditionFilter


class TestClassifyIntent:
    def test_factual(self) -> None:
        assert classify_intent("Quem escreveu Dom Casmurro?") is Intent.FACTUAL
        assert classify_intent("Quantas páginas tem a obra?") is Intent.FACTUAL

    def test_conceptual(self) -> None:
        assert classify_intent("Qual é a concepção de spleen em Dom Casmurro?") is (
            Intent.CONCEPTUAL
        )
        assert classify_intent("O que é o ciúme?") is Intent.CONCEPTUAL

    def test_comparative(self) -> None:
        assert classify_intent("Compara o spleen em Dom Casmurro e Memórias.") is (
            Intent.COMPARATIVE
        )
        assert classify_intent("Qual é a diferença entre o ciúme e o spleen?") is (
            Intent.COMPARATIVE
        )

    def test_navigational(self) -> None:
        assert classify_intent("Onde fica a discussão do spleen no Dom Casmurro?") is (
            Intent.NAVIGATIONAL
        )
        assert classify_intent("Em que capítulo fica o ciúme?") is Intent.NAVIGATIONAL


class TestBuildLexicalQuery:
    def test_content_words_become_required_terms(self) -> None:
        query = build_lexical_query("O que é o spleen?")
        assert query.required_terms == ("spleen",)
        assert query.phrase is None

    def test_stopwords_and_question_words_removed(self) -> None:
        query = build_lexical_query("Qual é a concepção de spleen?")
        assert set(query.required_terms) == {"concepção", "spleen"}

    def test_generic_nouns_removed(self) -> None:
        query = build_lexical_query("O que diz o livro sobre o ciúme?")
        assert "livro" not in query.required_terms
        assert "ciúme" in query.required_terms

    def test_falls_back_to_phrase_without_content_words(self) -> None:
        query = build_lexical_query("O que é isso?")
        assert query.phrase == "O que é isso?"
        assert query.required_terms == ()

    def test_trigram_threshold_passthrough(self) -> None:
        query = build_lexical_query("O que é o spleen?", trigram_threshold=0.5)
        assert query.trigram_threshold == 0.5


class TestBuildSemanticQuery:
    def test_returns_stripped_question(self) -> None:
        assert build_semantic_query("  O que é o spleen?  ") == "O que é o spleen?"


class TestResolveStrategy:
    def test_explicit_strategy_is_respected(self) -> None:
        strategy, explanation = resolve_strategy(SearchStrategy.LITERAL, Intent.FACTUAL)
        assert strategy is SearchStrategy.LITERAL
        assert explanation.requested is SearchStrategy.LITERAL
        assert explanation.chosen is SearchStrategy.LITERAL
        assert explanation.rationale

    def test_automatic_factual_prioritizes_relevance(self) -> None:
        """AC-07/§8.6: factual maximiza relevância -> híbrida + reranking."""
        strategy, explanation = resolve_strategy(SearchStrategy.AUTOMATIC, Intent.FACTUAL)
        assert strategy is SearchStrategy.HYBRID
        assert explanation.requested is SearchStrategy.AUTOMATIC
        assert explanation.chosen is SearchStrategy.HYBRID
        assert explanation.intent_signals == ("intenção=factual",)

    def test_automatic_conceptual_uses_expanded(self) -> None:
        strategy, _ = resolve_strategy(SearchStrategy.AUTOMATIC, Intent.CONCEPTUAL)
        assert strategy is SearchStrategy.EXPANDED

    def test_automatic_comparative_uses_expanded(self) -> None:
        strategy, _ = resolve_strategy(SearchStrategy.AUTOMATIC, Intent.COMPARATIVE)
        assert strategy is SearchStrategy.EXPANDED

    def test_automatic_navigational_uses_literal(self) -> None:
        strategy, _ = resolve_strategy(SearchStrategy.AUTOMATIC, Intent.NAVIGATIONAL)
        assert strategy is SearchStrategy.LITERAL


class TestAdaptiveDiversity:
    def test_factual_does_not_seek_diversity(self) -> None:
        assert diversity_for(Intent.FACTUAL) is False
        assert hierarchical_for(Intent.FACTUAL) is False

    def test_comparative_seeks_coverage_without_quota(self) -> None:
        """§8.6: comparativa busca cobertura (diversidade verdadeira); nunca
        uma quota cega — a diversidade é adaptativa, não um número fixo."""
        assert diversity_for(Intent.COMPARATIVE) is True
        assert hierarchical_for(Intent.COMPARATIVE) is True

    def test_conceptual_seeks_diversity_and_hierarchical(self) -> None:
        assert diversity_for(Intent.CONCEPTUAL) is True
        assert hierarchical_for(Intent.CONCEPTUAL) is True

    def test_navigational_is_literal_and_without_diversity(self) -> None:
        assert diversity_for(Intent.NAVIGATIONAL) is False
        assert hierarchical_for(Intent.NAVIGATIONAL) is False


def _catalog() -> dict[str, CatalogEntry]:
    work_a, work_b = uuid4(), uuid4()
    return {
        "dom casmurro": CatalogEntry(work_id=work_a, title="Dom Casmurro", edition_ids=()),
        "memorias postumas de bras cubas": CatalogEntry(
            work_id=work_b, title="Memórias Póstumas de Brás Cubas", edition_ids=()
        ),
    }


class TestResolveNaturalFilters:
    def test_explicit_inclusion_by_cue(self) -> None:
        filters = resolve_natural_filters(
            "Só no Dom Casmurro, qual é a concepção de spleen?", _catalog()
        )
        assert len(filters.include_work_ids) == 1

    def test_explicit_exclusion_by_cue(self) -> None:
        filters = resolve_natural_filters("Exceto Dom Casmurro, o que trata o ciúme?", _catalog())
        assert len(filters.exclude_work_ids) == 1
        assert not filters.include_work_ids

    def test_ambiguous_mention_is_not_silently_applied(self) -> None:
        """Menção sem polaridade clara NÃO é inferida (ambigüidade não é
        aplicada silenciosamente)."""
        filters = resolve_natural_filters("Quem escreveu Dom Casmurro?", _catalog())
        assert filters.is_empty()

    def test_comparative_mention_without_filter_is_not_applied(self) -> None:
        filters = resolve_natural_filters(
            "Compara o spleen em Dom Casmurro e Memórias Póstumas de Brás Cubas.",
            _catalog(),
        )
        assert filters.is_empty()

    def test_conflicting_polarity_is_dropped(self) -> None:
        filters = resolve_natural_filters(
            "Exceto Dom Casmurro, só Dom Casmurro, qual é o tema?", _catalog()
        )
        assert filters.is_empty()

    def test_sem_means_exclusion(self) -> None:
        filters = resolve_natural_filters("Sem Dom Casmurro, o que trata o ciúme?", _catalog())
        assert len(filters.exclude_work_ids) == 1

    def test_word_level_cue_matching_avoids_substring_false_positive(self) -> None:
        """ "`sem` dentro de `semana` NÃO é sina de exclusão (correspondência por
        palavra, nunca substring)."""
        filters = resolve_natural_filters(
            "Na semana de Dom Casmurro, o que trata o ciúme?", _catalog()
        )
        assert filters.is_empty()


class TestMergeFilters:
    def test_explicit_exclusion_overrides_inferred_inclusion(self) -> None:
        work = uuid4()
        explicit = EditionFilter(exclude_work_ids=frozenset({work}))
        inferred = EditionFilter(include_work_ids=frozenset({work}))
        merged = merge_filters(explicit, inferred)
        assert work not in merged.include_work_ids
        assert work in merged.exclude_work_ids

    def test_explicit_inclusion_overrides_inferred_exclusion(self) -> None:
        edition = uuid4()
        explicit = EditionFilter(include_edition_ids=frozenset({edition}))
        inferred = EditionFilter(exclude_edition_ids=frozenset({edition}))
        merged = merge_filters(explicit, inferred)
        assert edition in merged.include_edition_ids
        assert edition not in merged.exclude_edition_ids

    def test_disjoint_sets_are_union(self) -> None:
        a, b, c = uuid4(), uuid4(), uuid4()
        explicit = EditionFilter(include_work_ids=frozenset({a}))
        inferred = EditionFilter(include_work_ids=frozenset({b}), exclude_work_ids=frozenset({c}))
        merged = merge_filters(explicit, inferred)
        assert merged.include_work_ids == frozenset({a, b})
        assert merged.exclude_work_ids == frozenset({c})

    def test_empty_inferred_is_noop(self) -> None:
        edition = uuid4()
        explicit = EditionFilter(exclude_edition_ids=frozenset({edition}))
        merged = merge_filters(explicit, EditionFilter())
        assert merged.exclude_edition_ids == frozenset({edition})

    def test_never_produces_include_exclude_conflict(self) -> None:
        work = uuid4()
        merged = merge_filters(
            EditionFilter(include_work_ids=frozenset({work})),
            EditionFilter(exclude_work_ids=frozenset({work})),
        )
        assert not (merged.include_work_ids & merged.exclude_work_ids)
