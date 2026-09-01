"""Doubles locais dos provedores de modelo (SPEC §11, T07).

Implementações em memória de `EmbeddingProvider`, `RerankerProvider` e
`GeneratorProvider` para uso por testes de outras camadas (recuperação,
geração, planejador — T08+) sem depender de rede ou de um endpoint HTTP
simulado. Determinísticas por padrão; cada double aceita uma fila opcional
de exceções para simular falhas transitórias antes do comportamento normal.
"""

import hashlib
import re
from collections import deque
from collections.abc import Callable, Sequence

from rag.domain.answer import Claim, EvidenceRef, GeneratedAnswer
from rag.domain.providers import GenerationRequest
from rag.domain.versions import EmbeddingVersion, utcnow


def _deterministic_vector(text: str, dimensions: int) -> list[float]:
    """Vetor reprodutível: mesmo texto -> mesmo vetor, sem depender de um
    modelo real. Usa o hash do texto como fonte de bytes pseudo-aleatórios."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dimensions:
        digest = hashlib.sha256(digest).digest()
        values.extend(b / 255.0 for b in digest)
    return values[:dimensions]


class FakeEmbeddingProvider:
    """Double de `EmbeddingProvider`: vetores determinísticos por hash do texto."""

    def __init__(
        self,
        *,
        dimensions: int = 8,
        fail_with: Sequence[Exception] = (),
    ) -> None:
        self.dimensions = dimensions
        self._pending_failures: deque[Exception] = deque(fail_with)
        self.calls: list[str] = []

    def _maybe_fail(self) -> None:
        if self._pending_failures:
            raise self._pending_failures.popleft()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._maybe_fail()
        self.calls.extend(texts)
        return [_deterministic_vector(text, self.dimensions) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self._maybe_fail()
        self.calls.append(text)
        return _deterministic_vector(text, self.dimensions)

    @property
    def embedding_version(self) -> EmbeddingVersion:
        return EmbeddingVersion(
            label="fake-embedding",
            model_name="fake-embedding",
            dimensions=self.dimensions,
            created_at=utcnow(),
        )


_CONCEPT_WORDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("fate", frozenset({"destino", "spleen", "fado"})),
    ("memory", frozenset({"memória", "lembrança", "recordação"})),
    ("jealousy", frozenset({"ciúme", "ciume", "desconfiança", "inveja"})),
    ("freedom", frozenset({"liberdade", "autonomia"})),
)


def concept_embedding(text: str, dimensions: int = 1024) -> list[float]:
    """Vetor determinístico por conceito (T09, AC-05).

    Palavras DISTINTAS do mesmo conceito mapeam à mesma dimensão: dois textos
    podem ser "paráfrases" (sem termos principais comuns) e ainda ter vetores
    próximos. A função é local às fixtures de teste — não é um modelo real.
    """
    words = set(re.findall(r"\w+", text.lower(), flags=re.UNICODE))
    vector = [0.0] * dimensions
    for index, (_, synonyms) in enumerate(_CONCEPT_WORDS):
        if words & synonyms:
            vector[index] = 1.0
    return vector


class ConceptEmbeddingProvider:
    """Double de `EmbeddingProvider`: parárfase (mesmo conceito, palavras
    distintas) -> vetor próximo. Determinístico, sem rede."""

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [concept_embedding(text, self.dimensions) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return concept_embedding(text, self.dimensions)

    @property
    def embedding_version(self) -> EmbeddingVersion:
        return EmbeddingVersion(
            label="concept-embedding",
            model_name="concept-embedding",
            dimensions=self.dimensions,
            created_at=utcnow(),
        )


class FakeRerankerProvider:
    """Double de `RerankerProvider`: pontuação por sobreposição de termos
    entre a consulta e cada documento (heurística determinística, sem
    depender de um modelo real)."""

    def __init__(self, *, fail_with: Sequence[Exception] = ()) -> None:
        self._pending_failures: deque[Exception] = deque(fail_with)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def _maybe_fail(self) -> None:
        if self._pending_failures:
            raise self._pending_failures.popleft()

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self._maybe_fail()
        self.calls.append((query, tuple(documents)))
        query_terms = set(query.lower().split())
        scores = []
        for document in documents:
            document_terms = set(document.lower().split())
            overlap = len(query_terms & document_terms)
            scores.append(overlap / max(len(query_terms), 1))
        return scores


class FakeGeneratorProvider:
    """Double de `GeneratorProvider`: por padrão, cita a primeira evidência
    recebida; aceita uma fábrica customizada para exercitar outros casos
    (abstenção, várias afirmações, citação inválida)."""

    def __init__(
        self,
        *,
        answer_factory: Callable[[GenerationRequest], GeneratedAnswer] | None = None,
        fail_with: Sequence[Exception] = (),
    ) -> None:
        self._answer_factory = answer_factory or _default_answer
        self._pending_failures: deque[Exception] = deque(fail_with)
        self.requests: list[GenerationRequest] = []

    def _maybe_fail(self) -> None:
        if self._pending_failures:
            raise self._pending_failures.popleft()

    async def generate(self, request: GenerationRequest) -> GeneratedAnswer:
        self._maybe_fail()
        self.requests.append(request)
        return self._answer_factory(request)


def _default_answer(request: GenerationRequest) -> GeneratedAnswer:
    first: EvidenceRef = request.evidences[0]
    return GeneratedAnswer(
        answer_markdown=f"Resposta com base em {len(request.evidences)} evidência(s).",
        claims=(Claim(id="c1", text="Afirmação de exemplo.", evidence_ids=(first.passage_id,)),),
        limitations=(),
        abstained=False,
        abstention_reason=None,
    )


def abstention_answer(reason: str = "Sem suporte suficiente no acervo.") -> GeneratedAnswer:
    """Fábrica auxiliar para testes que precisam de uma resposta abstida."""
    return GeneratedAnswer(
        answer_markdown="",
        claims=(),
        limitations=(),
        abstained=True,
        abstention_reason=reason,
    )
