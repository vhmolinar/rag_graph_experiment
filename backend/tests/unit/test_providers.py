"""Contratos de providers: Protocols verificáveis em runtime (T02, base de T07)."""

from uuid import uuid4

from rag.domain.answer import EvidenceRef, GeneratedAnswer
from rag.domain.enums import Depth
from rag.domain.providers import (
    EmbeddingProvider,
    GenerationRequest,
    GeneratorProvider,
    RerankerProvider,
)


class FakeEmbedding:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


class FakeReranker:
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.5 for _ in documents]


class FakeGenerator:
    async def generate(self, request: GenerationRequest) -> GeneratedAnswer:
        return GeneratedAnswer(
            answer_markdown="resposta",
            abstained=True,
            abstention_reason="fake",
        )


def test_fakes_satisfy_protocols() -> None:
    assert isinstance(FakeEmbedding(), EmbeddingProvider)
    assert isinstance(FakeReranker(), RerankerProvider)
    assert isinstance(FakeGenerator(), GeneratorProvider)


def test_incomplete_implementation_fails_runtime_check() -> None:
    class OnlyDocuments:
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return []

    assert not isinstance(OnlyDocuments(), EmbeddingProvider)


async def test_generation_request_roundtrip_through_fake() -> None:
    request = GenerationRequest(
        system_policy="política",
        output_contract="contrato",
        question="pergunta",
        scope_description="acervo completo",
        evidences=[
            EvidenceRef(
                passage_id=uuid4(),
                edition_id=uuid4(),
                work_id=uuid4(),
                text="trecho",
                score=0.9,
                rank=0,
            )
        ],
        depth=Depth.STANDARD,
    )
    answer = await FakeGenerator().generate(request)
    assert answer.abstained


def test_generation_request_requires_evidences() -> None:
    """O gerador nunca é chamado sem evidências numeradas (fail-closed)."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GenerationRequest(
            system_policy="p",
            output_contract="c",
            question="q",
            scope_description="s",
            evidences=[],
            depth=Depth.BRIEF,
        )
