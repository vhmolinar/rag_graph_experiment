"""Enums do domínio. Os valores string são contrato com a API e a especificação."""

from enum import StrEnum


class AnswerMode(StrEnum):
    QUOTE = "quote"
    DISSERTATIVE = "dissertative"


class Depth(StrEnum):
    BRIEF = "brief"
    STANDARD = "standard"
    DEEP = "deep"


class SearchStrategy(StrEnum):
    AUTOMATIC = "automatic"
    LITERAL = "literal"
    HYBRID = "hybrid"
    EXPANDED = "expanded"


class Intent(StrEnum):
    FACTUAL = "factual"
    CONCEPTUAL = "conceptual"
    COMPARATIVE = "comparative"
    NAVIGATIONAL = "navigational"


class SourceType(StrEnum):
    PDF_TEXT = "pdf_text"
    PDF_SCAN = "pdf_scan"
    EPUB = "epub"


class LicenseStatus(StrEnum):
    UNKNOWN = "unknown"
    PUBLIC_DOMAIN = "public_domain"
    LICENSED = "licensed"
    RESTRICTED = "restricted"


class IngestionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class ConceptState(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    MERGED = "merged"
    REJECTED = "rejected"


class SummaryScope(StrEnum):
    SECTION = "section"
    CHAPTER = "chapter"
    EDITION = "edition"


class ContributorRole(StrEnum):
    AUTHOR = "author"
    TRANSLATOR = "translator"
    EDITOR = "editor"
    OTHER = "other"


class QueryStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ABSTAINED = "abstained"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RankingStage(StrEnum):
    LEXICAL = "lexical"
    VECTOR = "vector"
    FUSED = "fused"
    RERANKED = "reranked"


class ArtifactKind(StrEnum):
    ORIGINAL = "original"
    OCR_TEXT_LAYER = "ocr_text_layer"


class VerificationAction(StrEnum):
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REGENERATED = "regenerated"
    FORCED_ABSTENTION = "forced_abstention"
