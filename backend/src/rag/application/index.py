"""Serviço de indexação: chunking + embeddings em lote (T06; SPEC §7.3).

Reextrai o documento (T05 só persiste `Section`/`Page`, não o detalhe de
bloco — ver NOTES.md §10.6 item 1), valida que a reextração corresponde
exatamente ao que foi persistido na ingestão (correção T6-02), gera os
chunks pai/filho (`rag.domain.chunking`), gera embeddings em lotes
configuráveis para os filhos e persiste tudo numa única transação. Nada é
publicado parcialmente: dimensão de embedding inesperada, divergência de
reextração ou falha em qualquer lote falha antes de qualquer INSERT.

Reindexação é versionada, nunca destrutiva (correção T6-01, REVIEW_T06.md):
cada execução vira uma `IndexRun` própria (edição + versões de extração,
chunking, embedding e endpoint/modelo); passagens de execuções antigas
nunca são apagadas — apenas deixam de ser a execução `is_active` da edição.
Reexecutar com a MESMA identidade de versões (sem `--force`) é idempotente
e não chama o provedor de embeddings; parâmetros novos mintam uma nova
execução automaticamente, sem exigir `--force`; `--force` mintam uma nova
execução mesmo que a identidade não tenha mudado. Um `pg_advisory_xact_lock`
por edição serializa indexações concorrentes da mesma edição (correção
T6-10): a segunda chamada aguarda a primeira confirmar e então observa a
execução já ativa, em vez de competir por uma restrição de unicidade.
"""

import shutil
import tempfile
from pathlib import Path
from uuid import UUID

import docling
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from rag.adapters.docling_adapter import DoclingExtractor
from rag.application.ingest import derive_sections, resolve_edition_extraction_artifact
from rag.domain.canonical import CanonicalDocument
from rag.domain.chunking import ChunkingParams, ChunkNode, chunk_document, default_section_header
from rag.domain.enums import IngestionStatus
from rag.domain.errors import EmbeddingDimensionError, IngestionError, NotFoundError
from rag.domain.identifiers import sha256_of_text
from rag.domain.indexing import IndexRun
from rag.domain.library import Edition, Page, Passage, Section, Work
from rag.domain.providers import EmbeddingProvider
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ExtractionVersion,
    ModelEndpointVersion,
    utcnow,
)
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.index_runs import IndexRunsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

# T6-08: rótulos explícitos de revisão do algoritmo — mudar a heurística de
# chunking ou de extração exige bumpar o sufixo aqui (nunca reaproveitar um
# rótulo antigo para um algoritmo diferente); só assim `label` continua
# distinguindo revisões, e não apenas parâmetros.
_CHUNKING_ALGORITHM_LABEL = "structural-chunker-v1"
_EXTRACTION_ALGORITHM_LABEL = "docling-structural-v1"

_DEFAULT_EMBEDDING_BATCH_SIZE = 64


class IndexReport(BaseModel):
    """Resultado da indexação. Nunca contém texto do livro."""

    model_config = ConfigDict(frozen=True)

    edition_id: str
    index_run_id: str
    created: bool
    forced: bool
    parents: int
    children: int
    chunking_version_id: str
    embedding_version_id: str


class IndexingService:
    def __init__(
        self,
        store: ArtifactStore,
        extractor: DoclingExtractor,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._store = store
        self._extractor = extractor
        self._embedding_provider = embedding_provider

    async def index_edition(
        self,
        conn: AsyncConnection,
        *,
        edition_id: UUID,
        chunking_params: ChunkingParams,
        embedding_model_name: str,
        embedding_dimensions: int,
        force: bool = False,
        batch_size: int = _DEFAULT_EMBEDDING_BATCH_SIZE,
        embedding_endpoint: str = "",
        embedding_model_revision: str = "",
    ) -> IndexReport:
        if batch_size <= 0:
            raise IngestionError("Tamanho de lote de embeddings deve ser positivo.")
        async with conn.transaction():
            # T6-10: serializa indexações concorrentes da MESMA edição — a
            # segunda chamada aguarda aqui até a primeira confirmar (ou
            # reverter) sua transação inteira, então enxerga o estado final
            # já commitado em vez de decidir com base num instantâneo obsoleto.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(edition_id),)
            )

            editions = EditionsRepository(conn)
            edition = await editions.get(edition_id)
            if edition is None:
                raise NotFoundError(
                    "Edição não encontrada.", context={"edition_id": str(edition_id)}
                )
            work = await WorksRepository(conn).get(edition.work_id)
            if work is None:  # pragma: no cover - integridade referencial garante isto
                raise NotFoundError("Obra da edição não encontrada.")

            versions = VersionsRepository(conn)
            chunking_version = await versions.get_or_create(
                ChunkingVersion(
                    label=_CHUNKING_ALGORITHM_LABEL,
                    params=chunking_params.model_dump(),
                    created_at=utcnow(),
                )
            )

            # T6-02: a reextração é validada CONTRA o que foi persistido na
            # ingestão em toda chamada — inclusive na idempotente — antes de
            # decidir qualquer coisa. Uma divergência do extrator nunca deve
            # ser mascarada por um caminho de retorno antecipado.
            canonical = self._reextract(edition)
            extraction_version = await versions.get_or_create(
                ExtractionVersion(
                    label=_EXTRACTION_ALGORITHM_LABEL,
                    params={"docling_version": docling.__version__},
                    created_at=utcnow(),
                )
            )
            persisted_sections = await SectionsRepository(conn).list_by_edition(edition.id)
            persisted_pages = await PagesRepository(conn).list_by_edition(edition.id)
            self._assert_matches_persisted(edition, canonical, persisted_sections, persisted_pages)

            embedding_version = await versions.get_or_create(
                EmbeddingVersion(
                    label=embedding_model_name,
                    model_name=embedding_model_name,
                    dimensions=embedding_dimensions,
                    params={
                        "batch_size": batch_size,
                        "model_revision": embedding_model_revision,
                    },
                    created_at=utcnow(),
                )
            )
            model_endpoint_version = await versions.get_or_create(
                ModelEndpointVersion(
                    label=f"embedding:{embedding_model_name}",
                    endpoint_kind="embedding",
                    provider=type(self._embedding_provider).__name__,
                    model_name=embedding_model_name,
                    params={
                        "endpoint": embedding_endpoint,
                        "model_revision": embedding_model_revision,
                    },
                    created_at=utcnow(),
                )
            )

            runs = IndexRunsRepository(conn)
            passages_repo = PassagesRepository(conn)
            active_run = await runs.get_active(edition.id)
            desired_identity = (
                extraction_version.id,
                chunking_version.id,
                embedding_version.id,
                model_endpoint_version.id,
            )
            if active_run is not None and not force and active_run.identity == desired_identity:
                existing = await passages_repo.list_by_index_run(active_run.id)
                return self._idempotent_report(edition, active_run, existing)

            nodes = chunk_document(canonical, chunking_params)
            if not nodes:
                raise IngestionError(
                    "Nenhum chunk gerado: documento sem parágrafos indexáveis.",
                    context={"edition_id": str(edition.id)},
                )

            child_nodes = [n for n in nodes if n.parent_index is not None]
            embeddings = await self._embed_in_batches(child_nodes, batch_size)
            for vec in embeddings:
                if len(vec) != embedding_version.dimensions:
                    raise EmbeddingDimensionError(
                        "Dimensão do embedding retornado diverge da versão registrada.",
                        context={
                            "esperado": str(embedding_version.dimensions),
                            "recebido": str(len(vec)),
                        },
                    )
            embedding_by_child_index = dict(
                zip((n.index for n in child_nodes), embeddings, strict=True)
            )

            section_by_path = {tuple(s.path): s for s in persisted_sections}
            page_by_physical_index = {p.physical_index: p for p in persisted_pages}

            # Só a partir daqui algo muda de estado: nenhum INSERT/UPDATE
            # aconteceu ainda, então uma falha de embedding em qualquer lote
            # acima não deixa índice parcial publicado (T6-06).
            if active_run is not None:
                await runs.deactivate(active_run.id)
            new_run = await runs.create(
                IndexRun(
                    edition_id=edition.id,
                    extraction_version_id=extraction_version.id,
                    chunking_version_id=chunking_version.id,
                    embedding_version_id=embedding_version.id,
                    model_endpoint_version_id=model_endpoint_version.id,
                    is_active=True,
                    created_at=utcnow(),
                )
            )

            parents_created = 0
            children_created = 0
            id_by_node_index: dict[int, UUID] = {}
            for node in nodes:
                section = section_by_path.get(node.section_path)
                if section is None and node.section_path:
                    raise IngestionError(
                        "Chunk referencia seção não persistida.",
                        context={"edition_id": str(edition.id)},
                    )
                page_start = self._resolve_page(
                    edition, node.page_start_index, page_by_physical_index
                )
                page_end = self._resolve_page(edition, node.page_end_index, page_by_physical_index)
                is_child = node.parent_index is not None
                passage = Passage(
                    edition_id=edition.id,
                    ordinal=node.index,
                    text=node.text,
                    original_text=node.original_text,
                    token_count=node.token_count,
                    chunking_version_id=chunking_version.id,
                    section_id=section.id if section is not None else None,
                    page_start_id=page_start.id if page_start is not None else None,
                    page_end_id=page_end.id if page_end is not None else None,
                    char_start=node.char_start,
                    char_end=node.char_end,
                    context_header=self._context_header(work, edition, node.section_path),
                    parent_passage_id=(
                        id_by_node_index[node.parent_index]
                        if node.parent_index is not None
                        else None
                    ),
                    embedding_version_id=embedding_version.id if is_child else None,
                    index_run_id=new_run.id,
                )
                embedding = embedding_by_child_index[node.index] if is_child else None
                created = await passages_repo.create(passage, embedding)
                id_by_node_index[node.index] = created.id
                if is_child:
                    children_created += 1
                else:
                    parents_created += 1
            await editions.update_ingestion_status(edition.id, IngestionStatus.INDEXED)

        return IndexReport(
            edition_id=str(edition.id),
            index_run_id=str(new_run.id),
            created=True,
            forced=force and active_run is not None,
            parents=parents_created,
            children=children_created,
            chunking_version_id=str(chunking_version.id),
            embedding_version_id=str(embedding_version.id),
        )

    async def _embed_in_batches(
        self, child_nodes: list[ChunkNode], batch_size: int
    ) -> list[list[float]]:
        """T6-06: lote configurável (nunca uma única requisição para a edição
        inteira); cada lote é validado, e nada é gravado até TODOS os lotes
        terem sido gerados com sucesso (falha intermediária não publica
        índice parcial)."""
        embeddings: list[list[float]] = []
        for start in range(0, len(child_nodes), batch_size):
            batch = child_nodes[start : start + batch_size]
            batch_embeddings = await self._embedding_provider.embed_documents(
                [n.text for n in batch]
            )
            if len(batch_embeddings) != len(batch):
                raise EmbeddingDimensionError(
                    "Quantidade de embeddings retornada não corresponde à quantidade de "
                    "chunks do lote.",
                    context={
                        "esperado": str(len(batch)),
                        "recebido": str(len(batch_embeddings)),
                    },
                )
            embeddings.extend(batch_embeddings)
        return embeddings

    def _reextract(self, edition: Edition) -> CanonicalDocument:
        sha256, extraction_type, suffix = resolve_edition_extraction_artifact(edition)
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            with self._store.open_stream(sha256) as stream:
                shutil.copyfileobj(stream, tmp)
            tmp.flush()
            return self._extractor.extract(Path(tmp.name), extraction_type)

    @staticmethod
    def _assert_matches_persisted(
        edition: Edition,
        canonical: CanonicalDocument,
        persisted_sections: list[Section],
        persisted_pages: list[Page],
    ) -> None:
        """T6-02: a reextração usada por `rag index` precisa reproduzir
        EXATAMENTE a estrutura e o conteúdo persistidos por `rag ingest` —
        nunca apenas tentar casar chaves e seguir em frente com `None`
        silencioso. Qualquer divergência falha fechado antes de qualquer
        chunk ser gerado ou persistido. O contexto do erro nunca inclui
        texto do livro — apenas contagens e índices estruturais (T5-10)."""
        expected_sections = {tuple(s.path): s for s in derive_sections(edition.id, canonical)}
        if edition.canonical_fingerprint is None:
            raise IngestionError(
                "Edição sem fingerprint canônico; execute o backfill ou reingestão "
                "administrativa antes de indexar.",
                context={"edition_id": str(edition.id)},
            )
        if canonical.fingerprint() != edition.canonical_fingerprint:
            raise IngestionError(
                "Reextração diverge da representação canônica persistida na ingestão.",
                context={"edition_id": str(edition.id)},
            )
        persisted_by_path = {tuple(s.path): s for s in persisted_sections}
        if set(expected_sections) != set(persisted_by_path):
            raise IngestionError(
                "Reextração diverge da estrutura de seções persistida na ingestão "
                "original: seções ausentes ou adicionais.",
                context={
                    "edition_id": str(edition.id),
                    "esperado": str(len(expected_sections)),
                    "persistido": str(len(persisted_by_path)),
                },
            )
        for path, expected in expected_sections.items():
            persisted = persisted_by_path[path]
            if (
                expected.level != persisted.level
                or expected.title != persisted.title
                or expected.start_page != persisted.start_page
                or expected.end_page != persisted.end_page
            ):
                raise IngestionError(
                    "Reextração diverge dos metadados de seção persistidos na "
                    "ingestão original (nível, título ou páginas).",
                    context={"edition_id": str(edition.id)},
                )

        expected_pages = {p.physical_index: p for p in canonical.pages}
        persisted_pages_by_index = {p.physical_index: p for p in persisted_pages}
        if set(expected_pages) != set(persisted_pages_by_index):
            raise IngestionError(
                "Reextração diverge da paginação persistida na ingestão original: "
                "páginas ausentes ou adicionais.",
                context={
                    "edition_id": str(edition.id),
                    "esperado": str(len(expected_pages)),
                    "persistido": str(len(persisted_pages_by_index)),
                },
            )
        for physical_index, page in expected_pages.items():
            if sha256_of_text(page.text) != persisted_pages_by_index[physical_index].text_sha256:
                raise IngestionError(
                    "Reextração diverge do texto de página persistido na ingestão "
                    "original: o extrator produziu um resultado diferente do usado "
                    "para persistir esta edição.",
                    context={
                        "edition_id": str(edition.id),
                        "physical_index": str(physical_index),
                    },
                )

    @staticmethod
    def _resolve_page(
        edition: Edition, page_index: int | None, page_by_physical_index: dict[int, Page]
    ) -> Page | None:
        if page_index is None:
            return None
        page = page_by_physical_index.get(page_index)
        if page is None:  # pragma: no cover - validado por _assert_matches_persisted
            raise IngestionError(
                "Chunk referencia página sem correspondência persistida "
                "(inesperado após validação de reextração).",
                context={"edition_id": str(edition.id)},
            )
        return page

    @staticmethod
    def _context_header(work: Work, edition: Edition, section_path: tuple[str, ...]) -> str:
        parts = [work.canonical_title]
        if edition.edition_label:
            parts.append(edition.edition_label)
        header = " — ".join(parts)
        section_part = default_section_header(section_path)
        if section_part:
            header = f"{header}. {section_part}"
        return header[:1000]

    @staticmethod
    def _idempotent_report(
        edition: Edition, active_run: IndexRun, existing: list[Passage]
    ) -> IndexReport:
        return IndexReport(
            edition_id=str(edition.id),
            index_run_id=str(active_run.id),
            created=False,
            forced=False,
            parents=sum(1 for p in existing if p.embedding_version_id is None),
            children=sum(1 for p in existing if p.embedding_version_id is not None),
            chunking_version_id=str(active_run.chunking_version_id),
            embedding_version_id=str(active_run.embedding_version_id),
        )
