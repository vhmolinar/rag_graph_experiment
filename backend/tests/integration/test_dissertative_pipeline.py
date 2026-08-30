"""Pipeline dissertativo: geração + verificação contra PostgreSQL real (T13).

Seeds works/editions/sections/pages/passages (T12) e exercita o fluxo completo
recuperação (T09) → montagem de contexto (T12) → geração + verificação (T13):

- citação de ID inexistente é rejeitada e regenerada com feedback (SPEC §9.4);
- afirmação não sustentada é marcada como inferência (AC-09);
- pergunta sem suporte gera abstenção (AC-10);
- timeout do verificador não libera resposta não verificada (AC-14);
- comparativa com fonte única declara a limitação (AC-11);
- versões de prompts/endpoints/política de verificação registradas (AC-15).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import (
    ConceptEmbeddingProvider,
    FakeGeneratorProvider,
    FakeRerankerProvider,
    FakeVerifierProvider,
    abstention_answer,
    verdict_factory,
)

from rag.application.context import ContextService
from rag.application.dissertative import DissertativeService
from rag.application.search import RetrievalService
from rag.domain.answer import Claim, GeneratedAnswer
from rag.domain.context import ContextPolicy, PackedContext
from rag.domain.enums import Depth, Intent, SearchStrategy, SourceType, VerificationAction
from rag.domain.errors import VerificationError
from rag.domain.library import Edition, Page, Passage, Section, Work
from rag.domain.providers import GenerationRequest
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.retrieval import RetrievalPolicy
from rag.domain.verification import VerificationBudget, VerificationPolicy
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ModelEndpointVersion,
    PromptVersion,
    VerificationPolicyVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

pytestmark = pytest.mark.integration

_SEMANTIC_QUERY = "liberdade spleen destino"

_A0_TEXT = "spleen sufoca a liberdade de Bentinho."
_A1_TEXT = "O destino e o ciúme e a liberdade misturan o spleen de Capitu."
_B0_TEXT = "O spleen guia a memória dos antigos."
_A0_EXCERPT = "spleen sufoca a"  # _A0_TEXT[0:15]

_PARENT_A0_TEXT = "Capítulo I: " + _A0_TEXT
_PARENT_A1_TEXT = "Seção 1: " + _A1_TEXT
_PARENT_B0_TEXT = "Capítulo Único: " + _B0_TEXT


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID
    a0: UUID
    a1: UUID
    b0: UUID


async def _seed(db: Database) -> Corpus:
    async with db.connection() as conn:
        work_a = await WorksRepository(conn).create(Work(canonical_title="Dom Casmurro"))
        work_b = await WorksRepository(conn).create(
            Work(canonical_title="Memórias Póstumas de Brás Cubas")
        )
        edition_a = await EditionsRepository(conn).create(
            Edition(
                work_id=work_a.id,
                title="Dom Casmurro",
                source_type=SourceType.PDF_TEXT,
                source_sha256="a" * 64,
            )
        )
        edition_b = await EditionsRepository(conn).create(
            Edition(
                work_id=work_b.id,
                title="Memórias Póstumas",
                source_type=SourceType.PDF_TEXT,
                source_sha256="b" * 64,
            )
        )

        s_a0 = Section(
            edition_id=edition_a.id,
            level=0,
            ordinal=0,
            path=["Capítulo I"],
            title="Capítulo I",
        )
        s_a1 = Section(
            edition_id=edition_a.id,
            level=1,
            ordinal=1,
            path=["Capítulo I", "Seção 1"],
            title="Seção 1",
            parent_section_id=s_a0.id,
        )
        s_b0 = Section(
            edition_id=edition_b.id,
            level=0,
            ordinal=0,
            path=["Capítulo Único"],
            title="Capítulo Único",
        )
        await SectionsRepository(conn).create_many([s_a0, s_a1, s_b0])

        page_a0 = Page.create(
            edition_id=edition_a.id, physical_index=0, text=_A0_TEXT, printed_label="p. 1"
        )
        page_a1 = Page.create(
            edition_id=edition_a.id, physical_index=1, text=_A1_TEXT, printed_label="p. 2"
        )
        page_b0 = Page.create(
            edition_id=edition_b.id, physical_index=0, text=_B0_TEXT, printed_label="p. 1"
        )
        await PagesRepository(conn).create_many([page_a0, page_a1, page_b0])

        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-dissert", created_at=utcnow())
        )
        embedding_version = await versions.get_or_create(
            EmbeddingVersion(
                label="emb-dissert", model_name="m", dimensions=1024, created_at=utcnow()
            )
        )
        repo = PassagesRepository(conn)
        provider = ConceptEmbeddingProvider()

        parent_a0 = UUID("b0000000-0000-0000-0000-000000000101")
        parent_a1 = UUID("b0000000-0000-0000-0000-000000000102")
        parent_b0 = UUID("b0000000-0000-0000-0000-000000000103")
        a0 = UUID("a0000000-0000-0000-0000-000000000101")
        a1 = UUID("a0000000-0000-0000-0000-000000000102")
        b0 = UUID("a0000000-0000-0000-0000-000000000103")

        await repo.create(
            Passage(
                id=parent_a0,
                edition_id=edition_a.id,
                ordinal=0,
                text=_PARENT_A0_TEXT,
                token_count=len(_PARENT_A0_TEXT.split()),
                chunking_version_id=chunking.id,
                section_id=s_a0.id,
                page_start_id=page_a0.id,
                page_end_id=page_a0.id,
            )
        )
        await repo.create(
            Passage(
                id=parent_a1,
                edition_id=edition_a.id,
                ordinal=1,
                text=_PARENT_A1_TEXT,
                token_count=len(_PARENT_A1_TEXT.split()),
                chunking_version_id=chunking.id,
                section_id=s_a1.id,
                page_start_id=page_a1.id,
                page_end_id=page_a1.id,
            )
        )
        await repo.create(
            Passage(
                id=parent_b0,
                edition_id=edition_b.id,
                ordinal=0,
                text=_PARENT_B0_TEXT,
                token_count=len(_PARENT_B0_TEXT.split()),
                chunking_version_id=chunking.id,
                section_id=s_b0.id,
                page_start_id=page_b0.id,
                page_end_id=page_b0.id,
            )
        )

        await repo.create(
            Passage(
                id=a0,
                edition_id=edition_a.id,
                ordinal=2,
                text=_A0_EXCERPT,
                token_count=3,
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=s_a0.id,
                page_start_id=page_a0.id,
                page_end_id=page_a0.id,
                char_start=0,
                char_end=len(_A0_EXCERPT),
                parent_passage_id=parent_a0,
            ),
            embedding=await provider.embed_query(_A0_EXCERPT),
        )
        await repo.create(
            Passage(
                id=a1,
                edition_id=edition_a.id,
                ordinal=3,
                text=_A1_TEXT,
                token_count=len(_A1_TEXT.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=s_a1.id,
                page_start_id=page_a1.id,
                page_end_id=page_a1.id,
                parent_passage_id=parent_a1,
            ),
            embedding=await provider.embed_query(_A1_TEXT),
        )
        await repo.create(
            Passage(
                id=b0,
                edition_id=edition_b.id,
                ordinal=1,
                text=_B0_TEXT,
                token_count=len(_B0_TEXT.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=s_b0.id,
                page_start_id=page_b0.id,
                page_end_id=page_b0.id,
                parent_passage_id=parent_b0,
            ),
            embedding=await provider.embed_query(_B0_TEXT),
        )
        await conn.execute("ANALYZE passages")

    return Corpus(
        work_a=work_a.id,
        work_b=work_b.id,
        edition_a=edition_a.id,
        edition_b=edition_b.id,
        a0=a0,
        a1=a1,
        b0=b0,
    )


def _retrieval_service() -> RetrievalService:
    return RetrievalService(ConceptEmbeddingProvider(), FakeRerankerProvider())


def _plan(*, intent: Intent = Intent.COMPARATIVE, needs_diversity: bool = True) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        lexical_query=LexicalQuery(required_terms=("sufoca",)),
        semantic_query=_SEMANTIC_QUERY,
        strategy=SearchStrategy.HYBRID,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.AUTOMATIC,
            chosen=SearchStrategy.HYBRID,
            intent_signals=(f"intenção={intent.value}",),
            rationale="Plano de teste de T13.",
        ),
        needs_diversity=needs_diversity,
        needs_hierarchical=True,
    )


def _verification_policy(
    *,
    max_iterations: int = 1,
    support_threshold: float = 0.7,
) -> VerificationPolicy:
    budgets = tuple(
        (
            depth,
            VerificationBudget(
                depth=depth,
                max_iterations=max_iterations,
                support_threshold=support_threshold,
            ),
        )
        for depth in Depth
    )
    return VerificationPolicy(budgets=budgets)


async def _packed(
    db: Database, *, plan: QueryPlan, filters: EditionFilter | None = None
) -> "PackedContext":
    async with db.connection() as conn:
        retrieval = await _retrieval_service().retrieve(
            conn,
            lexical_query=plan.lexical_query,
            semantic_query=plan.semantic_query,
            filters=filters,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
        )
        return await ContextService().assemble(
            conn,
            plan=plan,
            retrieval=retrieval,
            depth=Depth.STANDARD,
            policy=ContextPolicy.defaults(),
        )


def _answer_with_claims(evidence_id: UUID) -> GeneratedAnswer:
    return GeneratedAnswer(
        answer_markdown="Resposta de teste.",
        claims=(
            Claim(id="c1", text="Afirmação sustentada.", evidence_ids=(evidence_id,)),
            Claim(id="c2", text="Afirmação não sustentada.", evidence_ids=(evidence_id,)),
        ),
        limitations=(),
        abstained=False,
        abstention_reason=None,
    )


async def test_invalid_citation_is_regenerated_with_feedback(db: Database) -> None:
    """SPEC §9.4: citação de ID inexistente é rejeitada; o gerador corrige com
    o feedback da verificação na regeneração."""
    await _seed(db)
    plan = _plan(intent=Intent.FACTUAL, needs_diversity=False)
    packed = await _packed(db, plan=plan)
    valid_id = packed.evidences[0].evidence.passage_id
    invalid_id = uuid4()

    calls = 0

    def _factory(request: GenerationRequest) -> GeneratedAnswer:
        nonlocal calls
        calls += 1
        if request.verification_feedback is None:
            return GeneratedAnswer(
                answer_markdown="Resposta com citação inventada.",
                claims=(Claim(id="c1", text="Afirmação.", evidence_ids=(invalid_id,)),),
                limitations=(),
                abstained=False,
                abstention_reason=None,
            )
        return GeneratedAnswer(
            answer_markdown="Resposta corrigida.",
            claims=(Claim(id="c1", text="Afirmação.", evidence_ids=(valid_id,)),),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )

    service = DissertativeService(
        FakeGeneratorProvider(answer_factory=_factory), FakeVerifierProvider()
    )
    async with db.connection() as conn:
        outcome = await service.answer(
            conn,
            question="Pergunta de teste?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(max_iterations=1),
            model_name="test-model",
        )
    assert calls == 2
    assert outcome.verification.action is VerificationAction.ACCEPTED
    assert outcome.verification.iterations == 2
    assert outcome.answer.claims[0].evidence_ids == (valid_id,)
    assert outcome.verification.invalid_evidence_ids == ()


async def test_unsupported_claim_is_marked_as_inference(db: Database) -> None:
    """AC-09: afirmação não sustentada é marcada como inferência (CORRECTED)."""
    await _seed(db)
    plan = _plan(intent=Intent.FACTUAL, needs_diversity=False)
    packed = await _packed(db, plan=plan)
    evidence_id = packed.evidences[0].evidence.passage_id

    generator = FakeGeneratorProvider(answer_factory=lambda _r: _answer_with_claims(evidence_id))
    verifier = FakeVerifierProvider(
        verdict_factory=verdict_factory(unsupported={("c2", evidence_id)})
    )
    service = DissertativeService(generator, verifier)
    async with db.connection() as conn:
        outcome = await service.answer(
            conn,
            question="Pergunta de teste?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0, support_threshold=0.5),
            model_name="test-model",
        )
    assert outcome.verification.action is VerificationAction.CORRECTED
    assert outcome.verification.unsupported_claim_ids == ("c2",)
    assert outcome.answer.claims[0].inference is False
    assert outcome.answer.claims[1].inference is True


async def test_question_without_support_abstains(db: Database) -> None:
    """AC-10: pergunta sem resposta gera abstenção (caminho do gerador)."""
    await _seed(db)
    plan = _plan(intent=Intent.FACTUAL, needs_diversity=False)
    packed = await _packed(db, plan=plan)
    generator = FakeGeneratorProvider(answer_factory=lambda _r: abstention_answer("Sem suporte."))
    verifier = FakeVerifierProvider()
    service = DissertativeService(generator, verifier)
    async with db.connection() as conn:
        outcome = await service.answer(
            conn,
            question="Pergunta sem resposta?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(),
            model_name="test-model",
        )
    assert outcome.answer.abstained
    assert outcome.answer.abstention_reason == "Sem suporte."
    assert outcome.verification.action is VerificationAction.ACCEPTED
    assert verifier.requests == []  # nenhuna chamada ao verificador


async def test_verifier_timeout_releases_no_unverified_answer(db: Database) -> None:
    """AC-14: timeout do verificador falha fechado — nenhuna resposta não
    verificada é liberada."""
    from rag.domain.errors import ModelTimeoutError

    await _seed(db)
    plan = _plan(intent=Intent.FACTUAL, needs_diversity=False)
    packed = await _packed(db, plan=plan)
    evidence_id = packed.evidences[0].evidence.passage_id
    generator = FakeGeneratorProvider(
        answer_factory=lambda _r: GeneratedAnswer(
            answer_markdown="Resposta.",
            claims=(Claim(id="c1", text="Afirmação.", evidence_ids=(evidence_id,)),),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
    )
    verifier = FakeVerifierProvider(fail_with=[ModelTimeoutError()])
    service = DissertativeService(generator, verifier)
    async with db.connection() as conn:
        with pytest.raises(VerificationError):
            await service.answer(
                conn,
                question="Pergunta de teste?",
                session_context=None,
                depth=Depth.STANDARD,
                plan=plan,
                packed=packed,
                verification_policy=_verification_policy(),
                model_name="test-model",
            )


async def test_comparative_with_single_work_declares_limitation(db: Database) -> None:
    """AC-11: comparativa com evidências de UMA obra declara a limitação."""
    corpus = await _seed(db)
    plan = _plan(intent=Intent.COMPARATIVE)
    filters = EditionFilter(exclude_work_ids=frozenset({corpus.work_b}))
    packed = await _packed(db, plan=plan, filters=filters)
    works = {item.evidence.work_id for item in packed.evidences}
    assert works == {corpus.work_a}  # precondição: fonte única

    evidence_id = packed.evidences[0].evidence.passage_id
    generator = FakeGeneratorProvider(
        answer_factory=lambda _r: GeneratedAnswer(
            answer_markdown="Resposta.",
            claims=(Claim(id="c1", text="Afirmação.", evidence_ids=(evidence_id,)),),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
    )
    service = DissertativeService(generator, FakeVerifierProvider())
    async with db.connection() as conn:
        outcome = await service.answer(
            conn,
            question="Compare Dom Casmurro com Memórias?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
    assert any("única obra" in limitation for limitation in outcome.answer.limitations)


async def test_versions_are_registered(db: Database) -> None:
    """AC-15: prompts, endpoints e política de verificação ficam registrados."""
    await _seed(db)
    plan = _plan(intent=Intent.FACTUAL, needs_diversity=False)
    packed = await _packed(db, plan=plan)
    evidence_id = packed.evidences[0].evidence.passage_id
    generator = FakeGeneratorProvider(
        answer_factory=lambda _r: GeneratedAnswer(
            answer_markdown="Resposta.",
            claims=(Claim(id="c1", text="Afirmação.", evidence_ids=(evidence_id,)),),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
    )
    service = DissertativeService(generator, FakeVerifierProvider())
    async with db.connection() as conn:
        outcome = await service.answer(
            conn,
            question="Pergunta de teste?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
    async with db.connection() as conn:
        versions = VersionsRepository(conn)
        assert await versions.get(PromptVersion, outcome.generation_prompt_version_id) is not None
        assert await versions.get(PromptVersion, outcome.verification_prompt_version_id) is not None
        assert (
            await versions.get(ModelEndpointVersion, outcome.generator_endpoint_version_id)
            is not None
        )
        assert (
            await versions.get(ModelEndpointVersion, outcome.verifier_endpoint_version_id)
            is not None
        )
        assert (
            await versions.get(VerificationPolicyVersion, outcome.verification_policy_version_id)
            is not None
        )


async def test_same_policy_is_idempotent_version(db: Database) -> None:
    """AC-15: a mesma política de verificação devolve a MESMA versão."""
    await _seed(db)
    plan = _plan(intent=Intent.FACTUAL, needs_diversity=False)
    packed = await _packed(db, plan=plan)
    evidence_id = packed.evidences[0].evidence.passage_id
    generator = FakeGeneratorProvider(
        answer_factory=lambda _r: GeneratedAnswer(
            answer_markdown="Resposta.",
            claims=(Claim(id="c1", text="Afirmação.", evidence_ids=(evidence_id,)),),
            limitations=(),
            abstained=False,
            abstention_reason=None,
        )
    )
    service = DissertativeService(generator, FakeVerifierProvider())
    async with db.connection() as conn:
        first = await service.answer(
            conn,
            question="Pergunta de teste?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
        second = await service.answer(
            conn,
            question="Pergunta de teste?",
            session_context=None,
            depth=Depth.STANDARD,
            plan=plan,
            packed=packed,
            verification_policy=_verification_policy(max_iterations=0),
            model_name="test-model",
        )
    assert second.verification_policy_version_id == first.verification_policy_version_id
