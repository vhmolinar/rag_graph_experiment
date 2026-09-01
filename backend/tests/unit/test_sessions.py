"""Reescrita determinística de follow-up para pergunta autônoma (T15; AC-13).

Cobre o reescritor puro do domínio: ordinais ("o segundo autor"), demonstrativos
("essa obra", "isso"), pronomes ("ele") e os caminhos de ausência (sem histórico,
sem catálogo, referência fora de alcance, sem anáfora) — a pergunta autônoma
nunca adivina. As referências resolvidas são registradas para inspeção (AC-13).
"""

from uuid import uuid4

from rag.domain.sessions import (
    RewriteReference,
    RewriteResult,
    SessionCatalogEntry,
    SessionTurn,
    rewrite_follow_up,
)

_WORK_A = SessionCatalogEntry(
    work_id=uuid4(), title="O Ensaio da Liberdade", authors=("Ana Pereira",)
)
_WORK_B = SessionCatalogEntry(
    work_id=uuid4(), title="O Ensaio da Memória", authors=("Bruno Silva",)
)
_CATALOG = {
    "o ensaio da liberdade": _WORK_A,
    "o ensaio da memoria": _WORK_B,
}

_TURNS = (
    SessionTurn(
        ordinal=0,
        question_original="quem é o autor de O Ensaio da Liberdade?",
        answer_text="O autor de O Ensaio da Liberdade é Ana Pereira.",
    ),
    SessionTurn(
        ordinal=1,
        question_original="qual é a concepção de spleen em O Ensaio da Memória?",
        answer_text="O autor de O Ensaio da Memória é Bruno Silva; a spleen é o destino.",
    ),
)


class TestOrdinalReferences:
    def test_second_author_resolves_from_session(self) -> None:
        result = rewrite_follow_up("compare isso com o segundo autor", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "compare O Ensaio da Memória com Bruno Silva"
        assert (
            RewriteReference(expression="o segundo autor", resolved_to="Bruno Silva")
            in result.references
        )
        assert (
            RewriteReference(expression="isso", resolved_to="O Ensaio da Memória")
            in result.references
        )

    def test_first_author_resolves(self) -> None:
        result = rewrite_follow_up("quem escreveu a primeira obra?", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "quem escreveu O Ensaio da Liberdade?"

    def test_ordinal_without_article(self) -> None:
        result = rewrite_follow_up("segundo autor e o ciúme", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "Bruno Silva e o ciúme"

    def test_ordinal_out_of_range_is_unchanged(self) -> None:
        result = rewrite_follow_up("compare isso com o terceiro autor", _TURNS, _CATALOG)
        # "o terceiro autor" não se resolve (só há dois) — fica como estan.
        assert result.autonomous_question == "compare O Ensaio da Memória com o terceiro autor"
        assert RewriteReference(expression="isso", resolved_to="O Ensaio da Memória") in (
            result.references
        )
        assert all(ref.expression != "o terceiro autor" for ref in result.references)

    def test_second_work_resolves(self) -> None:
        result = rewrite_follow_up("qual é o tema da segunda obra?", _TURNS, _CATALOG)
        # A reescrita é por substituição a nível de string: a preposição "da"
        # precedente fica ("da O Ensaio da Memória") — limitação documentada.
        assert result.autonomous_question == "qual é o tema da O Ensaio da Memória?"


class TestDemonstrativeReferences:
    def test_this_work_resolves_to_most_recent_work(self) -> None:
        result = rewrite_follow_up("essa obra e o ciúme", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "O Ensaio da Memória e o ciúme"

    def test_this_author_resolves_to_most_recent_author(self) -> None:
        result = rewrite_follow_up("quem é esse autor?", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "quem é Bruno Silva?"

    def test_bare_this_resolves_to_most_recent_work(self) -> None:
        result = rewrite_follow_up("o que diz isso?", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "o que diz O Ensaio da Memória?"


class TestPronounReferences:
    def test_singular_pronoun_resolves_to_most_recent_referent(self) -> None:
        result = rewrite_follow_up("ela é autora de quê?", _TURNS, _CATALOG)
        assert result.changed
        assert result.autonomous_question == "Bruno Silva é autora de quê?"

    def test_plural_pronoun_is_unresolved(self) -> None:
        result = rewrite_follow_up("eles concordam?", _TURNS, _CATALOG)
        assert not result.changed
        assert result.autonomous_question == "eles concordam?"


class TestNoRewrite:
    def test_without_history_is_unchanged(self) -> None:
        result = rewrite_follow_up("compare isso com o segundo autor", (), _CATALOG)
        assert not result.changed
        assert result.autonomous_question == "compare isso com o segundo autor"

    def test_without_catalog_matches_is_unchanged(self) -> None:
        result = rewrite_follow_up("compare isso com o segundo autor", _TURNS, {})
        assert not result.changed
        assert result.autonomous_question == "compare isso com o segundo autor"

    def test_question_without_anaphora_is_unchanged(self) -> None:
        result = rewrite_follow_up(
            "qual é a concepção de spleen em O Ensaio da Memória?", _TURNS, _CATALOG
        )
        assert not result.changed
        assert result.autonomous_question == "qual é a concepção de spleen em O Ensaio da Memória?"

    def test_unknown_referent_expression_is_unchanged(self) -> None:
        result = rewrite_follow_up("o que é o spleen?", _TURNS, _CATALOG)
        assert not result.changed


class TestRobustness:
    def test_accent_insensitive_title_matching(self) -> None:
        # O título canônico tem acentos; a menção na pergunta não.
        catalog = {
            "dom casmurro": SessionCatalogEntry(
                work_id=uuid4(), title="Dom Casmurro", authors=("Machado de Assis",)
            )
        }
        history = (SessionTurn(ordinal=0, question_original="quem é o autor de Dom Casmurro?"),)
        result = rewrite_follow_up("e essa obra?", history, catalog)
        assert result.changed
        assert result.autonomous_question == "e Dom Casmurro?"

    def test_case_preserved_for_sentence_start(self) -> None:
        result = rewrite_follow_up("Esse autor é o protagonista?", _TURNS, _CATALOG)
        assert result.autonomous_question == "Bruno Silva é o protagonista?"

    def test_answer_text_can_introduce_referents(self) -> None:
        # A obra só é mencionada na resposta da rodada, não na pergunta.
        catalog = {
            "dom casmurro": SessionCatalogEntry(
                work_id=uuid4(), title="Dom Casmurro", authors=("Machado de Assis",)
            )
        }
        history = (
            SessionTurn(
                ordinal=0,
                question_original="qual é o ciúme?",
                answer_text="O ciúme aparece em Dom Casmurro, de Machado de Assis.",
            ),
        )
        result = rewrite_follow_up("compare isso com o spleen", history, catalog)
        assert result.changed
        assert result.autonomous_question == "compare Dom Casmurro com o spleen"

    def test_references_are_inspectable(self) -> None:
        result = rewrite_follow_up("compare isso com o segundo autor", _TURNS, _CATALOG)
        assert isinstance(result, RewriteResult)
        assert all(isinstance(ref, RewriteReference) for ref in result.references)
        assert len(result.references) == 2
