"""Busca lexical em português contra PostgreSQL real (T08; SPEC §8.4, AC-04, AC-07).

Corpus com acentos, flexões e erros de digitação; exclusões e filtros por
obra/edição exercitados via SQL real; entrada adversarial confirma que nada
é interpolado como SQL nem interpretado como sintaxe de operador de tsquery.
"""

from dataclasses import dataclass
from uuid import UUID

import pytest
from pydantic import ValidationError

from rag.domain.enums import SourceType
from rag.domain.library import Edition, Passage, Work
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.runs import RankedCandidate
from rag.domain.versions import ChunkingVersion, EmbeddingVersion, utcnow
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.search import LexicalSearchRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID
    ciume_accented: UUID
    ciume_typo: UUID
    amores: UUID
    humanitismo: UUID
    liberdade_b: UUID
    parent_ciume: UUID
    typo_literal: UUID
    typo_target: UUID
    total_passages: int


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
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-lex", created_at=utcnow())
        )
        embedding_version = await versions.get_or_create(
            EmbeddingVersion(label="emb-lex", model_name="m", dimensions=1024, created_at=utcnow())
        )
        repo = PassagesRepository(conn)

        async def child(edition_id: UUID, ordinal: int, text: str) -> UUID:
            passage = Passage(
                edition_id=edition_id,
                ordinal=ordinal,
                text=text,
                token_count=len(text.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            created = await repo.create(passage, embedding=[0.1] * 1024)
            return created.id

        async def parent(edition_id: UUID, ordinal: int, text: str) -> UUID:
            passage = Passage(
                edition_id=edition_id,
                ordinal=ordinal,
                text=text,
                token_count=len(text.split()),
                chunking_version_id=chunking.id,
            )
            created = await repo.create(passage)
            return created.id

        ciume_accented = await child(
            edition_a.id,
            0,
            "Bentinho sentia a liberdade do primeiro amor sufocada pelo ciúme.",
        )
        ciume_typo = await child(
            edition_a.id,
            1,
            "Capitu percebeu o ciume crescente antes que ele fosse confessado.",
        )
        amores = await child(
            edition_a.id,
            2,
            "Os amores da juventude raramente sobrevivem à desconfiança madura.",
        )
        humanitismo = await child(
            edition_b.id,
            0,
            "Brás Cubas descreve o humanitismo de Quincas Borba com ironia final.",
        )
        liberdade_b = await child(
            edition_b.id,
            1,
            "A liberdade era, para Brás Cubas, uma ideia vaga e inconsequente.",
        )
        parent_ciume = await parent(
            edition_a.id,
            3,
            "Capítulo sobre o ciúme: contexto completo do enredo de Bentinho.",
        )
        # Par dedicado à tolerância trigram: "oenhasco" nunca é uma palavra real
        # nem compartilha radical com "penhasco" (stemmer só corta sufixo, nunca
        # prefixo) — garante que a correspondência exata e a aproximada nunca
        # colidem por acidente de stemming.
        typo_literal = await child(
            edition_a.id,
            4,
            "O guia repetia sempre a palavra oenhasco, sem explicar seu sentido.",
        )
        typo_target = await child(
            edition_a.id,
            5,
            "O penhasco escondia, há décadas, um segredo jamais revelado.",
        )
        await conn.execute("ANALYZE passages")

    return Corpus(
        work_a=work_a.id,
        work_b=work_b.id,
        edition_a=edition_a.id,
        edition_b=edition_b.id,
        ciume_accented=ciume_accented,
        ciume_typo=ciume_typo,
        amores=amores,
        humanitismo=humanitismo,
        liberdade_b=liberdade_b,
        parent_ciume=parent_ciume,
        typo_literal=typo_literal,
        typo_target=typo_target,
        total_passages=8,
    )


def _ids(candidates: list[RankedCandidate]) -> set[UUID]:
    return {c.passage_id for c in candidates}


async def test_accent_insensitive_required_term(db: Database) -> None:
    """Consulta acentuada encontra passagem grafada sem o mesmo acento e vice-versa."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        repo = LexicalSearchRepository(conn)
        with_accent = await repo.search(LexicalQuery(required_terms=("ciúme",)))
        without_accent = await repo.search(LexicalQuery(required_terms=("ciume",)))
    expected = {corpus.ciume_accented, corpus.ciume_typo}
    assert expected <= _ids(with_accent)
    assert expected <= _ids(without_accent)


async def test_stemming_matches_inflected_form(db: Database) -> None:
    """'amor' (singular) encontra 'amores' (plural) via portuguese_stem."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(LexicalQuery(required_terms=("amor",)))
    assert corpus.amores in _ids(hits)


async def test_exact_phrase_requires_contiguous_words(db: Database) -> None:
    """AC-04: busca literal encontra frase exata em português."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        repo = LexicalSearchRepository(conn)
        hit = await repo.search(LexicalQuery(phrase="liberdade do primeiro amor"))
        miss = await repo.search(LexicalQuery(phrase="primeiro liberdade amor"))
    assert _ids(hit) == {corpus.ciume_accented}
    assert _ids(miss) == set()


async def test_required_terms_are_conjunctive(db: Database) -> None:
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("bentinho", "ciume"))
        )
    assert _ids(hits) == {corpus.ciume_accented}


async def test_excluded_terms_are_enforced_in_sql(db: Database) -> None:
    """Exclusão exercitada no SQL: passagem com termo excluído nunca retorna."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("cubas",), excluded_terms=("humanitismo",))
        )
    assert corpus.humanitismo not in _ids(hits)
    assert corpus.liberdade_b in _ids(hits)


async def test_trigram_tolerance_finds_typo(db: Database) -> None:
    """Tolerância trigram configurável: erro de digitação ainda recupera a passagem."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        repo = LexicalSearchRepository(conn)
        tolerant = await repo.search(
            LexicalQuery(required_terms=("oenhasco",), trigram_threshold=0.3)
        )
        strict = await repo.search(
            LexicalQuery(required_terms=("oenhasco",), trigram_threshold=0.9)
        )
    assert corpus.typo_target in _ids(tolerant)
    assert corpus.typo_target not in _ids(strict)


async def test_exact_fts_hit_outranks_fuzzy_only_hit(db: Database) -> None:
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("oenhasco",), trigram_threshold=0.3), limit=10
        )
    by_id = {c.passage_id: c for c in hits}
    assert corpus.typo_literal in by_id
    assert corpus.typo_target in by_id
    assert by_id[corpus.typo_literal].score > 0
    assert by_id[corpus.typo_target].score == 0
    assert by_id[corpus.typo_literal].rank < by_id[corpus.typo_target].rank


async def test_parent_passages_are_never_candidates(db: Database) -> None:
    """T06 NOTES §10.6 item 2: passagens-pai (sem embedding) não são recuperáveis."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(LexicalQuery(required_terms=("ciume",)))
    assert corpus.parent_ciume not in _ids(hits)


async def test_filter_by_edition(db: Database) -> None:
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("liberdade",)),
            filters=EditionFilter(exclude_edition_ids=frozenset({corpus.edition_b})),
        )
    assert corpus.liberdade_b not in _ids(hits)
    assert corpus.ciume_accented in _ids(hits)


async def test_filter_by_work(db: Database) -> None:
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("cubas",)),
            filters=EditionFilter(include_work_ids=frozenset({corpus.work_a})),
        )
    assert _ids(hits) == set()


async def test_malformed_required_term_is_rejected_before_reaching_sql(db: Database) -> None:
    """`required_terms`/`excluded_terms` só aceitam palavras isoladas (T08 NOTES):
    uma tentativa de injeção via esse campo nunca chega a construir SQL."""
    await _seed(db)
    with pytest.raises(ValidationError):
        LexicalQuery(required_terms=("'); DROP TABLE passages; --",))


async def test_repository_never_interpolates_user_text_into_sql(db: Database) -> None:
    """Prova em nível de repository, não só de validação de domínio: mesmo um
    `LexicalQuery` construído sem os validators de campo (`model_construct`,
    simulando um futuro chamador que os contorne) não gera SQL executável a
    partir do conteúdo do termo — ele sempre chega como parâmetro ligado a
    `plainto_tsquery`/`similarity`, nunca concatenado à consulta."""
    await _seed(db)
    hostile = LexicalQuery.model_construct(
        phrase=None,
        required_terms=("'); DROP TABLE passages; --",),
        excluded_terms=(),
        trigram_threshold=0.3,
    )
    async with db.connection() as conn:

        async def passage_count() -> int:
            cur = await conn.execute("SELECT count(*) FROM passages")
            row = await cur.fetchone()
            assert row is not None
            return int(row[0])

        before = await passage_count()
        hits = await LexicalSearchRepository(conn).search(hostile)
        after = await passage_count()

    assert hits == []
    assert before == after


async def test_phrase_with_sql_and_operator_characters_is_literal_text(db: Database) -> None:
    """`phrase` aceita texto livre com múltiplas palavras; caracteres de SQL e
    de sintaxe de tsquery (`|`, `!`, aspas) nunca são interpretados como
    operador — só como texto comum, tratado por `phraseto_tsquery`."""
    await _seed(db)
    async with db.connection() as conn:
        repo = LexicalSearchRepository(conn)
        sql_like = await repo.search(LexicalQuery(phrase="foo' OR '1'='1"))
        operator_like = await repo.search(LexicalQuery(phrase="ciume | bentinho !"))
    assert sql_like == []
    # Frase exige adjacência na ordem dada; "bentinho" vem ANTES de "ciúme" no
    # texto original, então a ordem inversa não corresponde a nenhuma passagem.
    assert operator_like == []


async def test_no_results_returns_empty_list(db: Database) -> None:
    await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("inexistentexyz",), trigram_threshold=0.9)
        )
    assert hits == []
