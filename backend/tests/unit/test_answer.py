"""Contratos de resposta e verificação (T02; AC-08, AC-09, AC-10)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.answer import (
    AnswerBlock,
    Claim,
    Contradiction,
    EvidenceRef,
    GeneratedAnswer,
    QuoteResponse,
    VerificationResult,
)
from rag.domain.enums import VerificationAction


class TestClaim:
    def test_factual_claim_requires_evidence(self) -> None:
        """AC-09: afirmação factual sem evidência é inválida por construção."""
        with pytest.raises(ValidationError, match="evidência"):
            Claim(id="c1", text="O autor define spleen.", inference=False)

    def test_inference_claim_may_lack_direct_evidence(self) -> None:
        claim = Claim(id="c1", text="Infere-se que...", inference=True)
        assert claim.inference

    def test_inference_claim_may_cite_evidence(self) -> None:
        claim = Claim(id="c1", text="Infere-se X", evidence_ids=(uuid4(),), inference=True)
        assert len(claim.evidence_ids) == 1

    def test_evidence_ids_are_immutable(self) -> None:
        """RRR01: evidências de uma afirmação verificada não podem mudar."""
        claim = Claim(id="c1", text="Fato", evidence_ids=(uuid4(),))
        with pytest.raises(AttributeError):
            claim.evidence_ids.append(uuid4())  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            claim.evidence_ids = ()


class TestGeneratedAnswer:
    def test_abstention_requires_reason(self) -> None:
        """AC-10."""
        with pytest.raises(ValidationError, match="abstention_reason"):
            GeneratedAnswer(answer_markdown="Sem suporte.", abstained=True)

    def test_abstained_answer_cannot_have_claims(self) -> None:
        with pytest.raises(ValidationError, match="abstida"):
            GeneratedAnswer(
                answer_markdown="...",
                abstained=True,
                abstention_reason="acervo não cobre",
                claims=(Claim(id="c1", text="x", evidence_ids=(uuid4(),)),),
            )

    def test_reason_forbidden_when_not_abstained(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedAnswer(
                answer_markdown="...",
                abstained=False,
                abstention_reason="indevido",
            )

    def test_claim_ids_unique(self) -> None:
        with pytest.raises(ValidationError, match="únicos"):
            GeneratedAnswer(
                answer_markdown="...",
                abstained=False,
                claims=(
                    Claim(id="c1", text="a", evidence_ids=(uuid4(),)),
                    Claim(id="c1", text="b", evidence_ids=(uuid4(),)),
                ),
            )

    def test_valid_answer(self) -> None:
        claim = Claim(id="c1", text="Spleen é tédio", evidence_ids=(uuid4(),))
        blocks = (
            AnswerBlock(text=claim.text, claim_id="c1"),
            AnswerBlock(text=" "),
        )
        answer = GeneratedAnswer(
            answer_markdown="".join(block.text for block in blocks),
            blocks=blocks,
            abstained=False,
            claims=(claim,),
        )
        assert not answer.abstained

    def test_generated_answer_has_no_limitations_field(self) -> None:
        """T13-FULL-01: o contrato do gerador NUNCA carrega limitações — as
        limitações são derivadas deterministicamente pelo serviço; nenhuna prosa
        factual do modelo pode atravessar por esse canal."""
        assert "limitations" not in GeneratedAnswer.model_fields

    # --- T13-01: a abstenção NUNCA carrega prosa factual arbitrária (AC-10) ---

    def test_abstained_answer_cannot_have_text(self) -> None:
        with pytest.raises(ValidationError, match="texto"):
            GeneratedAnswer(
                answer_markdown="Fato inventado apresentado ao usuário.",
                abstained=True,
                abstention_reason="Sem suporte.",
            )

    def test_abstained_answer_cannot_have_blocks(self) -> None:
        with pytest.raises(ValidationError, match="blocos"):
            GeneratedAnswer(
                answer_markdown="",
                abstained=True,
                abstention_reason="Sem suporte.",
                blocks=(AnswerBlock(text="prosa"),),
            )

    # --- T13-03: o Markdown entregue fica ligado às afirmações verificadas ---

    def test_answer_requires_blocks_covering_markdown(self) -> None:
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="blocos"):
            GeneratedAnswer(
                answer_markdown="Afirmação verificada.",
                claims=(claim,),
                abstained=False,
            )

    def test_markdown_must_equal_concatenation_of_blocks(self) -> None:
        """T13-03: texto do Markdown fora dos blocos é rejeitado (falha
        fechada) — uma afirmação factual extra não listada não pode ser
        entregue sem verificação."""
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="concatenação"):
            GeneratedAnswer(
                answer_markdown="Afirmação verificada. Outra afirmação inventada.",
                blocks=(AnswerBlock(text="Afirmação verificada.", claim_id="c1"),),
                claims=(claim,),
                abstained=False,
            )

    def test_block_referencing_unknown_claim_is_rejected(self) -> None:
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="inexistente"):
            GeneratedAnswer(
                answer_markdown="Afirmação verificada.",
                blocks=(AnswerBlock(text="Afirmação verificada.", claim_id="c9"),),
                claims=(claim,),
                abstained=False,
            )

    def test_claim_block_must_match_claim_text(self) -> None:
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="corresponder"):
            GeneratedAnswer(
                answer_markdown="Outro texto.",
                blocks=(AnswerBlock(text="Outro texto.", claim_id="c1"),),
                claims=(claim,),
                abstained=False,
            )

    def test_every_claim_must_appear_as_block(self) -> None:
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="aparecer"):
            GeneratedAnswer(
                answer_markdown=" ",
                blocks=(AnswerBlock(text=" "),),
                claims=(claim,),
                abstained=False,
            )

    # --- T13-R2-01/T13-R3-01: bloco sem claim_id NÃO pode transportar conteúdo semântico ---

    def test_null_block_with_factual_prose_is_rejected(self) -> None:
        """T13-R2-01: o reprodutor da rodada 2 — uma frase factual num bloco sem
        `claim_id` ("Marte tem duas luas.") é rejeitada por construção (AC-09)."""
        claim = Claim(id="c1", text="A fonte diz X.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="whitespace"):
            GeneratedAnswer(
                answer_markdown="A fonte diz X. Marte tem duas luas.",
                blocks=(
                    AnswerBlock(text=claim.text, claim_id="c1"),
                    AnswerBlock(text=" Marte tem duas luas.", claim_id=None),
                ),
                claims=(claim,),
                abstained=False,
            )

    def test_null_block_with_natural_prose_is_rejected(self) -> None:
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="whitespace"):
            GeneratedAnswer(
                answer_markdown="Afirmação verificada. Portanto, conclúse.",
                blocks=(
                    AnswerBlock(text=claim.text, claim_id="c1"),
                    AnswerBlock(text=" Portanto, conclúse.", claim_id=None),
                ),
                claims=(claim,),
                abstained=False,
            )

    @pytest.mark.parametrize(
        "unverified",
        [
            " 2024",  # ano — reprodutor da rodada 3
            " 42",  # quantidade
            " 12.5%",  # porcentagem
            " 3 de maio",  # data — contém letras
            " https://exemplo.com",  # URL
        ],
    )
    def test_null_block_with_semantic_content_is_rejected(self, unverified: str) -> None:
        """T13-R3-01: números, datas, quantidades, URLs ou qualquer conteúdo
        semântico num bloco sem `claim_id` é rejeitado — tudo deve pertencer a
        uma afirmação verificada (AC-09)."""
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        with pytest.raises(ValidationError, match="whitespace"):
            GeneratedAnswer(
                answer_markdown="Afirmação verificada." + unverified,
                blocks=(
                    AnswerBlock(text=claim.text, claim_id="c1"),
                    AnswerBlock(text=unverified, claim_id=None),
                ),
                claims=(claim,),
                abstained=False,
            )

    def test_whitespace_null_blocks_allowed(self) -> None:
        """T13-R3-01: só whitespace (separadores/parágrafos inseridos pelo
        renderer) é permitido num bloco sem claim_id."""
        claim = Claim(id="c1", text="Afirmação verificada.", evidence_ids=(uuid4(),))
        blocks = (
            AnswerBlock(text="\n\n", claim_id=None),
            AnswerBlock(text=claim.text, claim_id="c1"),
            AnswerBlock(text=" \n", claim_id=None),
        )
        answer = GeneratedAnswer(
            answer_markdown="".join(block.text for block in blocks),
            blocks=blocks,
            claims=(claim,),
            abstained=False,
        )
        assert not answer.abstained


class TestQuoteResponse:
    def test_has_no_prose_fields(self) -> None:
        """AC-08: garantia estrutural — quote não tem campo para texto sintetizado."""
        assert set(QuoteResponse.model_fields) == {"evidences"}

    def test_empty_when_no_results(self) -> None:
        assert QuoteResponse().evidences == ()


class TestEvidenceRef:
    def test_section_path_is_immutable(self) -> None:
        """RRR01: caminho de seção de uma evidência não pode ser alterado."""
        ref = EvidenceRef(
            passage_id=uuid4(),
            edition_id=uuid4(),
            work_id=uuid4(),
            text="trecho",
            score=0.9,
            rank=0,
            section_path=("Parte 1", "Capítulo I"),
        )
        with pytest.raises(AttributeError):
            ref.section_path.append("Capítulo II")  # type: ignore[attr-defined]
        with pytest.raises(ValidationError):
            ref.section_path = ("Outra",)

    def test_offsets_paired(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceRef(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="trecho",
                score=0.9,
                rank=0,
                char_start=3,
            )

    def test_multipage_offsets_valid_when_inverted_between_pages(self) -> None:
        """T12-R2-01: para páginas distintas, `char_start` (relativo à página
        inicial) e `char_end` (relativo à página final) podem ser invertidos —
        ex.: início no offset 100 da página 10, fim no offset 3 da página 11 —
        e a referência é válida e reproduzível."""
        ref = EvidenceRef(
            passage_id=uuid4(),
            edition_id=uuid4(),
            work_id=uuid4(),
            text="trecho multipágina",
            score=0.9,
            rank=0,
            physical_page=10,
            page_end=11,
            printed_label="p. 10",
            printed_end_label="p. 11",
            char_start=100,
            char_end=3,
        )
        assert ref.char_start == 100
        assert ref.char_end == 3

    def test_same_page_offsets_require_char_end_gt_char_start(self) -> None:
        """T12-R2-01: na MESMA página (ou sem informação de página), a
        comparação `char_end > char_start` continua a valer."""
        with pytest.raises(ValidationError, match="char_end"):
            EvidenceRef(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="trecho",
                score=0.9,
                rank=0,
                physical_page=10,
                page_end=10,
                char_start=100,
                char_end=3,
            )
        # Sem informação de página (EPUB/backwards compat), também rejeitado.
        with pytest.raises(ValidationError, match="char_end"):
            EvidenceRef(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="trecho",
                score=0.9,
                rank=0,
                char_start=100,
                char_end=3,
            )

    def test_full_reference(self) -> None:
        ref = EvidenceRef(
            passage_id=uuid4(),
            edition_id=uuid4(),
            work_id=uuid4(),
            text="trecho literal",
            score=0.9,
            rank=0,
            section_path=("Obra", "Capítulo I"),
            physical_page=42,
            printed_label="p. 37",
            char_start=10,
            char_end=30,
        )
        assert ref.physical_page == 42
        # T12-01: a referência expõe a página de fim (para uma passagem de
        # página única, o banco devolve page_end == physical_page).
        assert ref.page_end is None
        assert ref.page_end_id is None

    def test_multipage_reference_carries_end_page(self) -> None:
        """T12-01/AC-03: a referência transporta início e fim da localização —
        IDs e índices físicos de ambas as páginas, rótulos e offsets."""
        start_id = uuid4()
        end_id = uuid4()
        ref = EvidenceRef(
            passage_id=uuid4(),
            edition_id=uuid4(),
            work_id=uuid4(),
            text="trecho multipágina",
            score=0.9,
            rank=0,
            section_path=("Obra", "Capítulo I"),
            page_start_id=start_id,
            page_end_id=end_id,
            physical_page=1,
            page_end=2,
            printed_label="p. 1",
            printed_end_label="p. 2",
            char_start=4,
            char_end=7,
        )
        assert ref.page_start_id == start_id
        assert ref.page_end_id == end_id
        assert ref.page_end == 2
        assert ref.printed_end_label == "p. 2"
        # o offset `char_end` é relativo à página de fim; `char_start` à de início.
        assert ref.char_start == 4
        assert ref.char_end == 7

    def test_page_end_must_not_precede_page_start(self) -> None:
        with pytest.raises(ValidationError, match="page_end"):
            EvidenceRef(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="trecho",
                score=0.9,
                rank=0,
                physical_page=3,
                page_end=2,
            )


class TestVerificationResult:
    def test_supported_cannot_exceed_total(self) -> None:
        with pytest.raises(ValidationError):
            VerificationResult(
                total_claims=1,
                supported_claims=2,
                citation_coverage=0.5,
                action=VerificationAction.ACCEPTED,
            )

    def test_contradiction_record(self) -> None:
        result = VerificationResult(
            total_claims=2,
            supported_claims=1,
            citation_coverage=0.5,
            action=VerificationAction.CORRECTED,
            contradictions=(
                Contradiction(claim_id="c2", evidence_id=uuid4(), detail="fonte afirma o oposto"),
            ),
        )
        assert result.contradictions[0].claim_id == "c2"
