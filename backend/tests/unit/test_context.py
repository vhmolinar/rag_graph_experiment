"""Montagem de contexto e modo quote — domínio puro (T12; AC-08, AC-11)."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from rag.domain.answer import QuoteResponse
from rag.domain.context import (
    CitablePassage,
    ContextBudget,
    ContextCandidate,
    ContextPolicy,
    PackedContext,
    context_total_chars,
    select_evidences,
)
from rag.domain.enums import Depth


def _candidate(
    *,
    passage_id: UUID | None = None,
    edition_id: UUID | None = None,
    work_id: UUID | None = None,
    text: str = "trecho literal",
    rank: int = 0,
    score: float = 1.0,
    parent_text: str | None = None,
    parent_passage_id: UUID | None = None,
    concepts: tuple[str, ...] = (),
) -> ContextCandidate:
    return ContextCandidate(
        passage=CitablePassage(
            passage_id=passage_id or uuid4(),
            edition_id=edition_id or uuid4(),
            work_id=work_id or uuid4(),
            text=text,
            parent_text=parent_text,
            parent_passage_id=parent_passage_id,
            concepts=concepts,
        ),
        score=score,
        rank=rank,
    )


def _budget(
    *,
    depth: Depth = Depth.STANDARD,
    max_evidences: int = 8,
    max_context_chars: int = 8000,
    parent_expansion_chars: int = 1200,
    per_edition_limit: int | None = 6,
) -> ContextBudget:
    return ContextBudget(
        depth=depth,
        max_evidences=max_evidences,
        max_context_chars=max_context_chars,
        parent_expansion_chars=parent_expansion_chars,
        per_edition_limit=per_edition_limit,
    )


def _citable(
    *,
    physical_page: int | None = None,
    page_end: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> CitablePassage:
    return CitablePassage(
        passage_id=uuid4(),
        edition_id=uuid4(),
        work_id=uuid4(),
        text="trecho literal",
        physical_page=physical_page,
        page_end=page_end,
        char_start=char_start,
        char_end=char_end,
    )


class TestCitablePassageOffsets:
    def test_multipage_offsets_valid_when_inverted_between_pages(self) -> None:
        """T12-R2-01: para páginas distintas, `char_start` (relativo à página
        inicial) e `char_end` (relativo à página final) podem ser invertidos —
        ex.: início no offset 100 da página 10, fim no offset 3 da página 11."""
        passage = _citable(physical_page=10, page_end=11, char_start=100, char_end=3)
        assert passage.char_start == 100
        assert passage.char_end == 3

    def test_same_page_offsets_require_char_end_gt_char_start(self) -> None:
        """T12-R2-01: na MESMA página (ou sem informação de página), a
        comparação `char_end > char_start` continua a valer."""
        with pytest.raises(ValidationError, match="char_end"):
            _citable(physical_page=10, page_end=10, char_start=100, char_end=3)
        with pytest.raises(ValidationError, match="char_end"):
            _citable(char_start=100, char_end=3)

    def test_offsets_paired(self) -> None:
        with pytest.raises(ValidationError):
            _citable(char_start=3)

    def test_page_end_must_not_precede_page_start(self) -> None:
        with pytest.raises(ValidationError, match="page_end"):
            _citable(physical_page=3, page_end=2)


class TestContextPolicy:
    def test_requires_all_depths_exactly_once(self) -> None:
        with pytest.raises(ValidationError, match="três profundidades"):
            ContextPolicy(
                budgets=(
                    (Depth.BRIEF, _budget()),
                    (Depth.STANDARD, _budget()),
                )
            )
        with pytest.raises(ValidationError, match="única vez"):
            ContextPolicy(
                budgets=(
                    (Depth.BRIEF, _budget()),
                    (Depth.BRIEF, _budget()),
                    (Depth.STANDARD, _budget()),
                    (Depth.DEEP, _budget()),
                )
            )

    def test_budget_depth_must_match_key(self) -> None:
        with pytest.raises(ValidationError, match="coincidir"):
            ContextPolicy(
                budgets=(
                    (Depth.BRIEF, _budget(depth=Depth.DEEP)),
                    (Depth.STANDARD, _budget()),
                    (Depth.DEEP, _budget()),
                )
            )

    def test_defaults_are_monotonic(self) -> None:
        policy = ContextPolicy.defaults()
        budgets = {d: b for d, b in policy.budgets}
        assert budgets[Depth.BRIEF].max_evidences < budgets[Depth.STANDARD].max_evidences
        assert budgets[Depth.STANDARD].max_evidences < budgets[Depth.DEEP].max_evidences
        assert budgets[Depth.BRIEF].max_context_chars < budgets[Depth.DEEP].max_context_chars

    def test_budget_for_returns_matching_budget(self) -> None:
        policy = ContextPolicy.defaults()
        assert policy.budget_for(Depth.DEEP).depth is Depth.DEEP
        assert (
            policy.budget_for(Depth.BRIEF).max_evidences
            < policy.budget_for(Depth.DEEP).max_evidences
        )


class TestSelectEvidences:
    def test_preserves_ranking_order(self) -> None:
        a = _candidate(text="alpha", rank=0)
        b = _candidate(text="beta", rank=1)
        selected = select_evidences([a, b], budget=_budget(), needs_diversity=False)
        assert [item.evidence.rank for item in selected] == [0, 1]
        assert [item.evidence.text for item in selected] == ["alpha", "beta"]

    def test_deduplicates_by_passage_id(self) -> None:
        passage_id = uuid4()
        first = _candidate(passage_id=passage_id, text="alpha", rank=0)
        duplicate = _candidate(passage_id=passage_id, text="beta", rank=1)
        selected = select_evidences([first, duplicate], budget=_budget(), needs_diversity=False)
        assert len(selected) == 1
        assert selected[0].evidence.text == "alpha"

    def test_respects_max_evidences(self) -> None:
        candidates = [_candidate(text=f"t{i}", rank=i) for i in range(10)]
        selected = select_evidences(
            candidates, budget=_budget(max_evidences=3), needs_diversity=False
        )
        assert len(selected) == 3

    def test_context_budget_is_never_exceeded(self) -> None:
        """Orçamento de contexto: evidências que não couber no restante são
        descartadas, nunca se estoura (SPEC §9.1, T12)."""
        candidates = [_candidate(text="x" * 60, rank=0), _candidate(text="y" * 60, rank=1)]
        selected = select_evidences(
            candidates,
            budget=_budget(max_context_chars=100, max_evidences=5),
            needs_diversity=False,
        )
        assert len(selected) == 1
        assert context_total_chars(selected) <= 100

    def test_budget_accounts_parent_expansion(self) -> None:
        """A expansão parental conta no orçamento de contexto."""
        candidates = [
            _candidate(text="a" * 50, rank=0, parent_text="p" * 60),
            _candidate(text="b" * 50, rank=1, parent_text="p" * 60),
        ]
        selected = select_evidences(
            candidates,
            budget=_budget(max_context_chars=120, parent_expansion_chars=100),
            needs_diversity=False,
        )
        # Primeiro: 50 + 60 = 110 <= 120; restam 10 — o segundo (110) não couber.
        assert len(selected) == 1
        assert context_total_chars(selected) == 110
        assert selected[0].parent_text == "p" * 60

    def test_parent_expansion_is_truncated_to_limit(self) -> None:
        parent_id = uuid4()
        candidate = _candidate(
            text="t", rank=0, parent_text="parent-text-long", parent_passage_id=parent_id
        )
        selected = select_evidences(
            [candidate],
            budget=_budget(parent_expansion_chars=6),
            needs_diversity=False,
        )
        assert selected[0].parent_text == "parent"
        assert selected[0].parent_passage_id == parent_id

    def test_parent_expansion_disabled_at_zero(self) -> None:
        candidate = _candidate(text="t", rank=0, parent_text="parent")
        selected = select_evidences(
            [candidate], budget=_budget(parent_expansion_chars=0), needs_diversity=False
        )
        assert selected[0].parent_text is None
        # e sem pai: nada a expandir.
        candidate_no_parent = _candidate(text="t", rank=1)
        selected = select_evidences([candidate_no_parent], budget=_budget(), needs_diversity=False)
        assert selected[0].parent_text is None
        assert selected[0].parent_passage_id is None

    def test_factual_maximizes_relevance_without_per_edition_cap(self) -> None:
        """SPEC §8.6: factual → maximiza relevância, sem limite por edição."""
        edition_a = uuid4()
        edition_b = uuid4()
        candidates = [_candidate(text=f"a{i}", edition_id=edition_a, rank=i) for i in range(4)] + [
            _candidate(text="b0", edition_id=edition_b, rank=4)
        ]
        selected = select_evidences(
            candidates,
            budget=_budget(max_evidences=5, per_edition_limit=2),
            needs_diversity=False,
        )
        assert len(selected) == 5  # todos os candidatos, sem capa por edição

    def test_diversity_caps_per_edition_flexibly(self) -> None:
        """SPEC §8.6: comparativa/conceitual → limite flexível por edição; a
        seleção nunca inclui uma obra menos relevante para preencher."""
        edition_a = uuid4()
        edition_b = uuid4()
        candidates = [
            _candidate(text="a0", edition_id=edition_a, rank=0),
            _candidate(text="a1", edition_id=edition_a, rank=1),
            _candidate(text="a2", edition_id=edition_a, rank=2),
            _candidate(text="a3", edition_id=edition_a, rank=3),
            _candidate(text="b0", edition_id=edition_b, rank=4),
            _candidate(text="b1", edition_id=edition_b, rank=5),
        ]
        selected = select_evidences(
            candidates,
            budget=_budget(max_evidences=5, per_edition_limit=2),
            needs_diversity=True,
        )
        # A capada a 2; B capada a 2 — a3/a4 ficam fora; não se preenche.
        assert [item.evidence.text for item in selected] == ["a0", "a1", "b0", "b1"]
        editions = {item.evidence.edition_id for item in selected}
        assert editions == {edition_a, edition_b}

    def test_diversity_never_pads_less_relevant_work(self) -> None:
        """Se uma edição tem MENOS candidatos que o limite, as posições
        sobrentes NÃO são preenchidas (seleção pode ficar < max_evidences)."""
        edition_a = uuid4()
        edition_b = uuid4()
        candidates = [
            _candidate(text="a0", edition_id=edition_a, rank=0),
            _candidate(text="a1", edition_id=edition_a, rank=1),
            _candidate(text="a2", edition_id=edition_a, rank=2),
            _candidate(text="b0", edition_id=edition_b, rank=3),
        ]
        selected = select_evidences(
            candidates,
            budget=_budget(max_evidences=4, per_edition_limit=2),
            needs_diversity=True,
        )
        assert [item.evidence.text for item in selected] == ["a0", "a1", "b0"]
        assert len(selected) == 3  # < max_evidences: nada se preenche

    def test_concept_diversity_changes_selection(self) -> None:
        """SPEC §8.6/T12-02: com diversidade, candidatos que traem um conceito
        novo são preferidos sobre os que repetem conceitos já cobertos — a
        diversificação por conceito altera a seleção (sem quota cega)."""
        edition = uuid4()
        candidates = [
            _candidate(text="c0", edition_id=edition, rank=0, concepts=("liberdade",)),
            _candidate(text="c1", edition_id=edition, rank=1, concepts=("liberdade",)),
            _candidate(text="c2", edition_id=edition, rank=2, concepts=("destino",)),
        ]
        undiverse = select_evidences(
            candidates,
            budget=_budget(max_evidences=3, per_edition_limit=10),
            needs_diversity=False,
        )
        diverse = select_evidences(
            candidates,
            budget=_budget(max_evidences=3, per_edition_limit=10),
            needs_diversity=True,
        )
        # Sem diversidade, a ordem do ranking prevalece.
        assert [item.evidence.text for item in undiverse] == ["c0", "c1", "c2"]
        # Com diversidade, c2 (conceito novo) precede c1 (repetição do conceito).
        assert [item.evidence.text for item in diverse] == ["c0", "c2", "c1"]

    def test_concept_diversity_is_flexible_not_quota(self) -> None:
        """SPEC §8.6/T12-02: NUNCA se impõe quota cega por conceito — se um
        conceito tiver um único candidato, a seleção não se preenche para
        atingir `max_evidences`."""
        edition = uuid4()
        candidates = [
            _candidate(text="c0", edition_id=edition, rank=0, concepts=("liberdade",)),
            _candidate(text="c1", edition_id=edition, rank=1, concepts=("liberdade",)),
            _candidate(text="c2", edition_id=edition, rank=2, concepts=("destino",)),
        ]
        selected = select_evidences(
            candidates,
            budget=_budget(max_evidences=4, per_edition_limit=10),
            needs_diversity=True,
        )
        # max_evidences=4 mas só 3 candidatos: nada se preenche.
        assert [item.evidence.text for item in selected] == ["c0", "c2", "c1"]
        assert len(selected) == 3

    def test_multipage_metadata_projected_on_evidence(self) -> None:
        """T12-01/AC-03: uma passagem multipágina projeta início e fim da
        localização (páginas físicas, rótulos e offsets relativos a cada
        uma) no `EvidenceRef`."""
        candidate = ContextCandidate(
            passage=CitablePassage(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="início na página 1 e fim na página 2",
                physical_page=3,
                page_end=4,
                printed_label="p. 3",
                printed_end_label="p. 4",
                char_start=10,
                char_end=20,
                concepts=("destino",),
            ),
            score=0.9,
            rank=0,
        )
        selected = select_evidences([candidate], budget=_budget(), needs_diversity=False)
        ref = selected[0].evidence
        assert ref.physical_page == 3
        assert ref.page_end == 4
        assert ref.printed_label == "p. 3"
        assert ref.printed_end_label == "p. 4"
        assert ref.char_start == 10
        assert ref.char_end == 20
        assert ref.page_start_id == candidate.passage.page_start_id
        assert ref.page_end_id == candidate.passage.page_end_id

    def test_evidence_refs_carry_citable_metadata(self) -> None:
        """AC-03/AC-08: cada evidência expõe obra, edição, seção, página e
        offsets; o texto é literal, sem prosa."""
        edition = uuid4()
        work = uuid4()
        passage = uuid4()
        candidate = _candidate(
            passage_id=passage,
            edition_id=edition,
            work_id=work,
            text="trecho exato",
            rank=2,
            score=0.9,
        )
        selected = select_evidences([candidate], budget=_budget(), needs_diversity=False)
        ref = selected[0].evidence
        assert ref.passage_id == passage
        assert ref.edition_id == edition
        assert ref.work_id == work
        assert ref.text == "trecho exato"
        assert ref.rank == 2
        assert ref.score == 0.9


class TestPackedContext:
    def test_rejects_budget_overflow_structurally(self) -> None:
        """Falha fechada: um contexto acima do orçamento é inválido por
        construção (T12)."""
        with pytest.raises(ValidationError, match="excedido"):
            PackedContext(
                evidences=(),
                total_chars=9000,
                context_budget_chars=8000,
            )

    def test_accepts_within_budget(self) -> None:
        packed = PackedContext(
            evidences=(),
            total_chars=100,
            context_budget_chars=8000,
        )
        assert packed.total_chars == 100


class TestQuoteContract:
    def test_quote_has_only_evidences_field(self) -> None:
        """AC-08 (T02): o modo quote não tem campos de prosa — nenhum texto
        sintetizado pode ser devolvido como citação."""
        assert set(QuoteResponse.model_fields) == {"evidences"}

    def test_empty_quote_is_valid(self) -> None:
        assert QuoteResponse().evidences == ()
