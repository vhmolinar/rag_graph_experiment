"""Doubles locais dos provedores de modelo (SPEC §11, T07): satisfazem os
Protocols e são determinísticos."""

import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import (
    FakeEmbeddingProvider,
    FakeGeneratorProvider,
    FakeRerankerProvider,
    abstention_answer,
)

from rag.domain.answer import EvidenceRef
from rag.domain.enums import Depth
from rag.domain.errors import ModelTimeoutError
from rag.domain.providers import (
    EmbeddingProvider,
    GenerationRequest,
    GeneratorProvider,
    RerankerProvider,
)


def _generation_request() -> GenerationRequest:
    evidence = EvidenceRef(
        passage_id=uuid4(), edition_id=uuid4(), work_id=uuid4(), text="trecho", score=0.5, rank=0
    )
    return GenerationRequest(
        system_policy="p",
        output_contract="c",
        question="q",
        scope_description="s",
        evidences=[evidence],
        depth=Depth.STANDARD,
    )


class TestFakeEmbeddingProvider:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)

    async def test_same_text_yields_same_vector(self) -> None:
        provider = FakeEmbeddingProvider(dimensions=4)
        a = await provider.embed_query("mesmo texto")
        b = await provider.embed_query("mesmo texto")
        assert a == b
        assert len(a) == 4

    async def test_different_text_yields_different_vector(self) -> None:
        provider = FakeEmbeddingProvider(dimensions=4)
        a = await provider.embed_query("texto um")
        b = await provider.embed_query("texto dois")
        assert a != b

    async def test_embed_documents_matches_embed_query_per_text(self) -> None:
        provider = FakeEmbeddingProvider(dimensions=4)
        (single,) = await provider.embed_documents(["x"])
        query = await provider.embed_query("x")
        assert single == query

    async def test_injected_failure_then_success(self) -> None:
        provider = FakeEmbeddingProvider(fail_with=[ModelTimeoutError()])
        with pytest.raises(ModelTimeoutError):
            await provider.embed_query("a")
        result = await provider.embed_query("a")
        assert len(result) == provider.dimensions


class TestFakeRerankerProvider:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(FakeRerankerProvider(), RerankerProvider)

    async def test_scores_by_term_overlap(self) -> None:
        provider = FakeRerankerProvider()
        scores = await provider.rerank("gato preto", ["um gato dorme", "um cachorro late"])
        assert scores[0] > scores[1]

    async def test_empty_query_terms_does_not_divide_by_zero(self) -> None:
        provider = FakeRerankerProvider()
        scores = await provider.rerank("", ["documento"])
        assert scores == [0.0]


class TestFakeGeneratorProvider:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(FakeGeneratorProvider(), GeneratorProvider)

    async def test_default_answer_cites_first_evidence(self) -> None:
        provider = FakeGeneratorProvider()
        request = _generation_request()
        answer = await provider.generate(request)
        assert answer.claims[0].evidence_ids == (request.evidences[0].passage_id,)
        assert provider.requests == [request]

    async def test_custom_answer_factory(self) -> None:
        provider = FakeGeneratorProvider(answer_factory=lambda _req: abstention_answer("motivo x"))
        answer = await provider.generate(_generation_request())
        assert answer.abstained is True
        assert answer.abstention_reason == "motivo x"
