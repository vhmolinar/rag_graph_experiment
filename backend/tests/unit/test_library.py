"""Invariantes do acervo (T02). Sementes para AC-01/AC-02."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from rag.domain.enums import ArtifactKind, ContributorRole, SourceType
from rag.domain.identifiers import sha256_of_text
from rag.domain.library import (
    Contributor,
    DerivedArtifactRef,
    Edition,
    Page,
    Passage,
    Section,
    Work,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _edition(
    work_id: UUID,
    sha256: str = HASH_A,
    source_type: SourceType = SourceType.PDF_TEXT,
    publication_year: int | None = None,
    edition_label: str | None = None,
    derived_artifacts: list[DerivedArtifactRef] | None = None,
) -> Edition:
    return Edition(
        work_id=work_id,
        title="Dom Casmurro",
        source_type=source_type,
        source_sha256=sha256,
        publication_year=publication_year,
        edition_label=edition_label,
        derived_artifacts=derived_artifacts or [],
    )


class TestWork:
    def test_language_is_pt_by_default(self) -> None:
        work = Work(canonical_title="Dom Casmurro")
        assert work.language == "pt"

    def test_title_cannot_be_blank(self) -> None:
        with pytest.raises(ValidationError):
            Work(canonical_title="   ")

    def test_contributors_keep_order(self) -> None:
        work = Work(
            canonical_title="Obra",
            authors=[
                Contributor(name="Machado de Assis", ordinal=0),
                Contributor(name="Tradutor", role=ContributorRole.TRANSLATOR, ordinal=1),
            ],
        )
        assert [a.name for a in work.authors] == ["Machado de Assis", "Tradutor"]


class TestEdition:
    def test_two_editions_of_same_work_are_distinguishable(self) -> None:
        """AC-02: edições distintas têm ids e hashes distintos e citam separado."""
        work = Work(canonical_title="Dom Casmurro")
        first = _edition(work.id, HASH_A, edition_label="1ª ed.")
        second = _edition(work.id, HASH_B, edition_label="2ª ed.")
        assert first.id != second.id
        assert first.source_sha256 != second.source_sha256
        assert first.work_id == second.work_id

    def test_source_sha256_must_be_64_hex(self) -> None:
        with pytest.raises(ValidationError):
            _edition(uuid4(), "not-a-hash")

    def test_source_sha256_is_normalized_to_lowercase(self) -> None:
        edition = _edition(uuid4(), "A" * 64)
        assert edition.source_sha256 == "a" * 64

    def test_publication_year_bounds(self) -> None:
        with pytest.raises(ValidationError):
            _edition(uuid4(), publication_year=2200)

    def test_derived_artifact_must_reference_original_hash(self) -> None:
        """Decisão §10.1.5 do NOTES: OCR derivado aponta para o original imutável."""
        derived = DerivedArtifactRef(
            sha256=HASH_B,
            kind=ArtifactKind.OCR_TEXT_LAYER,
            derived_from=HASH_A,
            generator="docling-ocr",
        )
        edition = _edition(uuid4(), HASH_A, source_type=SourceType.PDF_SCAN)
        edition_ok = edition.model_copy(update={"derived_artifacts": [derived]})
        assert edition_ok.derived_artifacts[0].derived_from == HASH_A

        bad = DerivedArtifactRef(
            sha256=HASH_B,
            kind=ArtifactKind.OCR_TEXT_LAYER,
            derived_from="c" * 64,
            generator="docling-ocr",
        )
        with pytest.raises(ValidationError, match="original"):
            _edition(uuid4(), HASH_A, derived_artifacts=[bad])


class TestSection:
    def test_path_length_matches_level(self) -> None:
        with pytest.raises(ValidationError, match="level"):
            Section(edition_id=uuid4(), level=1, ordinal=0, path=["Livro"])

    def test_root_section_has_no_parent(self) -> None:
        with pytest.raises(ValidationError, match="raiz"):
            Section(edition_id=uuid4(), level=0, ordinal=0, path=["L"], parent_section_id=uuid4())

    def test_end_page_not_before_start_page(self) -> None:
        with pytest.raises(ValidationError, match="end_page"):
            Section(edition_id=uuid4(), level=0, ordinal=0, path=["L"], start_page=10, end_page=5)

    def test_valid_hierarchy(self) -> None:
        root = Section(edition_id=uuid4(), level=0, ordinal=0, path=["Dom Casmurro"])
        child = Section(
            edition_id=root.edition_id,
            level=1,
            ordinal=0,
            path=["Dom Casmurro", "Capítulo I"],
            parent_section_id=root.id,
            start_page=1,
            end_page=10,
        )
        assert child.path[-1] == "Capítulo I"


class TestPage:
    def test_create_computes_hash(self) -> None:
        page = Page.create(edition_id=uuid4(), physical_index=0, text="texto da página")
        assert page.text_sha256 == sha256_of_text("texto da página")

    def test_hash_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="text_sha256"):
            Page(edition_id=uuid4(), physical_index=0, text="abc", text_sha256=HASH_A)

    def test_blank_page_is_allowed(self) -> None:
        page = Page.create(edition_id=uuid4(), physical_index=3, text="")
        assert page.text == ""


class TestPassage:
    def _passage(
        self,
        char_start: int | None = None,
        char_end: int | None = None,
        token_count: int = 3,
        context_header: str = "",
        parent_passage_id: UUID | None = None,
        ordinal: int = 0,
    ) -> Passage:
        return Passage(
            edition_id=uuid4(),
            ordinal=ordinal,
            text="trecho citável",
            token_count=token_count,
            chunking_version_id=uuid4(),
            char_start=char_start,
            char_end=char_end,
            context_header=context_header,
            parent_passage_id=parent_passage_id,
        )

    def test_offsets_must_be_paired(self) -> None:
        with pytest.raises(ValidationError, match="ambos"):
            self._passage(char_start=10)

    def test_offset_end_after_start(self) -> None:
        with pytest.raises(ValidationError, match="char_end"):
            self._passage(char_start=10, char_end=10)

    def test_multipage_offsets_valid_when_inverted_between_pages(self) -> None:
        """T12-R2-01: para páginas distintas, `char_start` (relativo à página
        inicial) e `char_end` (relativo à página final) podem ser invertidos —
        ex.: início no offset 100 da página A, fim no offset 3 da página B."""
        page_a = uuid4()
        page_b = uuid4()
        passage = Passage(
            edition_id=uuid4(),
            ordinal=0,
            text="trecho multipágina",
            token_count=3,
            chunking_version_id=uuid4(),
            page_start_id=page_a,
            page_end_id=page_b,
            char_start=100,
            char_end=3,
        )
        assert passage.char_start == 100
        assert passage.char_end == 3

    def test_token_count_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._passage(token_count=0)

    def test_context_header_is_not_citable(self) -> None:
        """AC-08/AC-12: cabeçalho contextual nunca integra o texto citável."""
        passage = self._passage(context_header="Obra > Capítulo I")
        assert passage.citable_text == "trecho citável"
        assert "Obra" not in passage.citable_text

    def test_parent_child_relation(self) -> None:
        parent = self._passage()
        child = self._passage(parent_passage_id=parent.id, ordinal=1)
        assert child.parent_passage_id == parent.id
