"""Valores dos enums são contrato com a especificação (§5, §8, §9)."""

from rag.domain.enums import (
    AnswerMode,
    ArtifactKind,
    ConceptState,
    ContributorRole,
    Depth,
    IngestionStatus,
    Intent,
    LicenseStatus,
    QueryStatus,
    RankingStage,
    SearchStrategy,
    SourceType,
    SummaryScope,
    VerificationAction,
)


def test_answer_mode_values() -> None:
    assert set(AnswerMode) == {AnswerMode.QUOTE, AnswerMode.DISSERTATIVE}
    assert AnswerMode.QUOTE.value == "quote"
    assert AnswerMode.DISSERTATIVE.value == "dissertative"


def test_depth_values() -> None:
    assert [d.value for d in Depth] == ["brief", "standard", "deep"]


def test_search_strategy_values() -> None:
    assert {s.value for s in SearchStrategy} == {"automatic", "literal", "hybrid", "expanded"}


def test_intent_values() -> None:
    assert {i.value for i in Intent} == {"factual", "conceptual", "comparative", "navigational"}


def test_source_type_values() -> None:
    assert {s.value for s in SourceType} == {"pdf_text", "pdf_scan", "epub"}


def test_license_status_values() -> None:
    assert {s.value for s in LicenseStatus} == {
        "unknown",
        "public_domain",
        "licensed",
        "restricted",
    }


def test_concept_state_values() -> None:
    assert {s.value for s in ConceptState} == {"proposed", "accepted", "merged", "rejected"}


def test_summary_scope_values() -> None:
    assert {s.value for s in SummaryScope} == {"section", "chapter", "edition"}


def test_ranking_stage_values() -> None:
    assert {s.value for s in RankingStage} == {"lexical", "vector", "fused", "reranked"}


def test_query_status_values() -> None:
    assert {s.value for s in QueryStatus} == {
        "queued",
        "running",
        "succeeded",
        "abstained",
        "failed",
        "cancelled",
    }


def test_enums_serialize_as_plain_strings() -> None:
    assert str(AnswerMode.QUOTE) == "quote"
    assert f"{Depth.DEEP}" == "deep"
    assert isinstance(IngestionStatus.PENDING.value, str)
    assert VerificationAction.FORCED_ABSTENTION.value == "forced_abstention"
    assert ArtifactKind.OCR_TEXT_LAYER.value == "ocr_text_layer"
    assert ContributorRole.AUTHOR.value == "author"
