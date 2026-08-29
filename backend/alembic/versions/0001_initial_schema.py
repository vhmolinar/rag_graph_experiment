"""Schema inicial: acervo, versões imutáveis, execuções e índices de busca.

Revision ID: 0001
Revises: None
Create Date: 2026-08-28

Estratégia de dimensão de embedding (T03, determinística — R02):
- a coluna `passages.embedding` nasce com dimensão fixa 1024, declarada nesta
  revisão. Nenhuma variável de ambiente altera o schema: a mesma revisão produz
  sempre o mesmo banco;
- `embedding_versions.dimensions` registra a dimensão de cada versão; o
  repository rejeita embeddings cuja dimensão diverge da versão registrada e a
  coluna `vector(1024)` rejeita qualquer outra dimensão no banco;
- avaliar um modelo Qwen com dimensão diferente (ex.: 2560/4096) exige uma NOVA
  migration (ex.: `000N_embedding_<dim>.py`) que altere a coluna e recrie o
  índice, acompanhada de reindexação sob nova `embedding_version`. Revisões
  antigas continuam identificando o mesmo schema de sempre;
- o limite de 2000 dimensões do índice HNSW sobre o tipo `vector` é validado
  abaixo, em tempo de migration; dimensões maiores exigiriam `halfvec` e uma
  revisão específica.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dimensão fixa desta revisão (Qwen3-Embedding-0.6B). Mudança = nova migration.
EMBEDDING_DIMENSIONS = 1024
# Limite do HNSW sobre o tipo `vector` do pgvector 0.8.x.
_HNSW_VECTOR_MAX_DIMS = 2000
if not 1 <= EMBEDDING_DIMENSIONS <= _HNSW_VECTOR_MAX_DIMS:
    msg = (
        f"dimensão {EMBEDDING_DIMENSIONS} não suportada pelo índice HNSW sobre "
        f"vector (limite {_HNSW_VECTOR_MAX_DIMS}); use halfvec em revisão própria"
    )
    raise RuntimeError(msg)

_VERSION_TABLES = (
    "extraction_versions",
    "chunking_versions",
    "embedding_versions",
    "model_endpoint_versions",
    "prompt_versions",
    "retrieval_policy_versions",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Configuração FTS português + unaccent (SPEC §8.4).
    op.execute("CREATE TEXT SEARCH CONFIGURATION portuguese_unaccent (COPY = portuguese)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION portuguese_unaccent "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, portuguese_stem"
    )
    # Wrapper IMMUTABLE para permitir índice trigram sobre texto sem acento.
    op.execute(
        "CREATE OR REPLACE FUNCTION rag_immutable_unaccent(text) RETURNS text "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$ SELECT public.unaccent($1) $$"
    )
    # Imutabilidade das tabelas de versão (SPEC §6).
    op.execute(
        "CREATE OR REPLACE FUNCTION rag_reject_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'tabela de versões é imutável: %', TG_TABLE_NAME; END $$"
    )

    op.execute(
        """
        CREATE TABLE works (
            id uuid PRIMARY KEY,
            canonical_title text NOT NULL CHECK (length(btrim(canonical_title)) > 0),
            original_title text,
            language text NOT NULL DEFAULT 'pt' CHECK (language = 'pt'),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contributors (
            work_id uuid NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            ordinal int NOT NULL CHECK (ordinal >= 0),
            name text NOT NULL CHECK (length(btrim(name)) > 0),
            role text NOT NULL CHECK (role IN ('author', 'translator', 'editor', 'other')),
            PRIMARY KEY (work_id, ordinal)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE editions (
            id uuid PRIMARY KEY,
            work_id uuid NOT NULL REFERENCES works(id),
            title text NOT NULL CHECK (length(btrim(title)) > 0),
            publisher text,
            publication_year int CHECK (publication_year BETWEEN 0 AND 2100),
            isbn text,
            edition_label text,
            source_type text NOT NULL CHECK (source_type IN ('pdf_text', 'pdf_scan', 'epub')),
            source_sha256 text NOT NULL UNIQUE CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            license_status text NOT NULL DEFAULT 'unknown'
                CHECK (license_status IN ('unknown', 'public_domain', 'licensed', 'restricted')),
            ingestion_status text NOT NULL DEFAULT 'pending'
                CHECK (ingestion_status IN
                    ('pending', 'extracting', 'extracted', 'indexing', 'indexed', 'failed')),
            created_at timestamptz NOT NULL,
            -- R4-02: chave candidata para a FK de proveniência dos derivados.
            UNIQUE (id, source_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE derived_artifacts (
            id uuid PRIMARY KEY,
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            sha256 text NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            kind text NOT NULL CHECK (kind IN ('original', 'ocr_text_layer')),
            derived_from_sha256 text NOT NULL CHECK (derived_from_sha256 ~ '^[0-9a-f]{64}$'),
            generator text NOT NULL,
            created_at timestamptz NOT NULL,
            -- R4-02: o derivado declara o hash original DA MESMA edição —
            -- proveniência imposta pelo banco, não só pelo domínio.
            FOREIGN KEY (edition_id, derived_from_sha256)
                REFERENCES editions (id, source_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sections (
            id uuid PRIMARY KEY,
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            parent_section_id uuid,
            level int NOT NULL CHECK (level >= 0),
            ordinal int NOT NULL CHECK (ordinal >= 0),
            title text,
            path text[] NOT NULL CHECK (array_length(path, 1) >= 1),
            start_page int CHECK (start_page >= 0),
            end_page int CHECK (end_page >= 0),
            UNIQUE (edition_id, ordinal),
            -- R01: FKs compostas impedem referências cruzadas entre edições.
            UNIQUE (id, edition_id),
            FOREIGN KEY (parent_section_id, edition_id)
                REFERENCES sections (id, edition_id),
            CHECK (end_page IS NULL OR start_page IS NULL OR end_page >= start_page)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pages (
            id uuid PRIMARY KEY,
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            physical_index int NOT NULL CHECK (physical_index >= 0),
            printed_label text,
            text text NOT NULL,
            text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
            UNIQUE (edition_id, physical_index),
            UNIQUE (id, edition_id)
        )
        """
    )

    version_tables_ddl = (
        """
        CREATE TABLE extraction_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """,
        """
        CREATE TABLE chunking_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """,
        """
        CREATE TABLE embedding_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            model_name text NOT NULL CHECK (length(btrim(model_name)) > 0),
            -- RRR03: a coluna passages.embedding é vector(1024); versões com
            -- outra dimensão seriam inutilizáveis — rejeitadas no banco também.
            dimensions int NOT NULL CHECK (dimensions = 1024),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, model_name, dimensions, params)
        )
        """,
        """
        CREATE TABLE model_endpoint_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            endpoint_kind text NOT NULL
                CHECK (endpoint_kind IN ('embedding', 'reranker', 'generator')),
            provider text NOT NULL CHECK (length(btrim(provider)) > 0),
            model_name text NOT NULL CHECK (length(btrim(model_name)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, endpoint_kind, provider, model_name, params)
        )
        """,
        """
        CREATE TABLE prompt_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            template_sha256 text NOT NULL CHECK (template_sha256 ~ '^[0-9a-f]{64}$'),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            -- R03: o hash do template participa da identidade da versão.
            UNIQUE (label, template_sha256, params)
        )
        """,
        """
        CREATE TABLE retrieval_policy_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """,
    )
    for ddl in version_tables_ddl:
        op.execute(ddl)
    for table in _VERSION_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION rag_reject_mutation()"
        )

    op.execute(
        f"""
        CREATE TABLE passages (
            id uuid PRIMARY KEY,
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            section_id uuid,
            page_start_id uuid,
            page_end_id uuid,
            ordinal int NOT NULL CHECK (ordinal >= 0),
            text text NOT NULL CHECK (length(text) > 0),
            token_count int NOT NULL CHECK (token_count > 0),
            char_start int CHECK (char_start >= 0),
            char_end int CHECK (char_end >= 0),
            context_header text NOT NULL DEFAULT '',
            parent_passage_id uuid,
            embedding vector({EMBEDDING_DIMENSIONS}),
            embedding_version_id uuid REFERENCES embedding_versions(id),
            chunking_version_id uuid NOT NULL REFERENCES chunking_versions(id),
            text_search tsvector
                GENERATED ALWAYS AS (to_tsvector('portuguese_unaccent', text)) STORED,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (edition_id, ordinal),
            -- R01: toda referência estrutural carrega a mesma edition_id.
            UNIQUE (id, edition_id),
            FOREIGN KEY (section_id, edition_id)
                REFERENCES sections (id, edition_id),
            FOREIGN KEY (page_start_id, edition_id)
                REFERENCES pages (id, edition_id),
            FOREIGN KEY (page_end_id, edition_id)
                REFERENCES pages (id, edition_id),
            FOREIGN KEY (parent_passage_id, edition_id)
                REFERENCES passages (id, edition_id),
            CHECK ((char_start IS NULL) = (char_end IS NULL)),
            CHECK (char_end IS NULL OR char_end > char_start)
        )
        """
    )
    op.execute("CREATE INDEX passages_edition_idx ON passages (edition_id)")
    op.execute("CREATE INDEX passages_section_idx ON passages (section_id)")
    op.execute("CREATE INDEX passages_text_search_gin ON passages USING gin (text_search)")
    op.execute(
        "CREATE INDEX passages_text_trgm_gin ON passages "
        "USING gin ((rag_immutable_unaccent(text)) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX passages_embedding_hnsw ON passages "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX sections_edition_idx ON sections (edition_id)")
    op.execute("CREATE INDEX pages_edition_idx ON pages (edition_id)")

    op.execute(
        """
        CREATE TABLE summaries (
            id uuid PRIMARY KEY,
            -- R01: toda síntese pertence a uma edição; suportes herdam a edição.
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            scope_type text NOT NULL CHECK (scope_type IN ('section', 'chapter', 'edition')),
            -- RR01: escopo referencialmente íntegro, sem FK polimórfica.
            -- 'section' e 'chapter' (capítulo é uma Section de topo) referenciam
            -- sections via FK composta com edition_id; 'edition' refere-se à
            -- própria edição e não admite section_id.
            section_id uuid,
            text text NOT NULL CHECK (length(text) > 0),
            generator_version_id uuid NOT NULL REFERENCES model_endpoint_versions(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, edition_id),
            CHECK (
                (scope_type IN ('section', 'chapter') AND section_id IS NOT NULL)
                OR (scope_type = 'edition' AND section_id IS NULL)
            ),
            FOREIGN KEY (section_id, edition_id) REFERENCES sections (id, edition_id)
        )
        """
    )
    # R4-03: 'chapter' é uma Section de topo (level = 0) — imposto pelo banco.
    # O domínio não carrega a seção; a pré-validação amigável cabe ao serviço
    # de sínteses (T11), mas a regra estrutural vive aqui.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION summaries_chapter_is_top_level()
        RETURNS trigger AS $$
        DECLARE
            sec_level integer;
        BEGIN
            -- section_id NULL é rejeitado pelo CHECK da tabela, não aqui.
            IF NEW.scope_type = 'chapter' AND NEW.section_id IS NOT NULL THEN
                SELECT level INTO sec_level FROM sections
                 WHERE id = NEW.section_id AND edition_id = NEW.edition_id;
                IF sec_level IS DISTINCT FROM 0 THEN
                    RAISE EXCEPTION
                        'scope chapter exige seção de topo (level = 0), recebido: %',
                        sec_level;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER summaries_chapter_top_level
        BEFORE INSERT OR UPDATE ON summaries
        FOR EACH ROW EXECUTE FUNCTION summaries_chapter_is_top_level()
        """
    )
    op.execute(
        """
        CREATE TABLE summary_supports (
            summary_id uuid NOT NULL,
            passage_id uuid NOT NULL,
            edition_id uuid NOT NULL,
            ordinal int NOT NULL DEFAULT 0,
            PRIMARY KEY (summary_id, passage_id),
            -- R01: passagem de suporte deve pertencer à edição da síntese.
            FOREIGN KEY (summary_id, edition_id)
                REFERENCES summaries (id, edition_id) ON DELETE CASCADE,
            FOREIGN KEY (passage_id, edition_id)
                REFERENCES passages (id, edition_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE concepts (
            id uuid PRIMARY KEY,
            normalized_label text NOT NULL UNIQUE,
            description text NOT NULL DEFAULT '',
            state text NOT NULL DEFAULT 'proposed'
                CHECK (state IN ('proposed', 'accepted', 'merged', 'rejected')),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE concept_aliases (
            concept_id uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            expression text NOT NULL,
            confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            PRIMARY KEY (concept_id, expression)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE concept_evidence (
            concept_id uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            passage_id uuid NOT NULL REFERENCES passages(id),
            confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            extractor_version_id uuid NOT NULL REFERENCES model_endpoint_versions(id),
            PRIMARY KEY (concept_id, passage_id, extractor_version_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sessions (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_activity_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE answer_runs (
            id uuid PRIMARY KEY,
            session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
            status text NOT NULL CHECK (status IN
                ('queued', 'running', 'succeeded', 'abstained', 'failed', 'cancelled')),
            question_original text NOT NULL,
            question_anonymized text NOT NULL,
            rewritten_query text,
            explicit_filters jsonb NOT NULL,
            inferred_filters jsonb,
            plan jsonb,
            candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
            selected_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            response jsonb,
            verification jsonb,
            versions jsonb NOT NULL DEFAULT '{}'::jsonb,
            latencies jsonb NOT NULL DEFAULT '[]'::jsonb,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            -- R4-01: controle otimista de concorrência (compare-and-swap).
            revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
            -- R05: coerência de estado terminal também imposta pelo banco.
            CHECK (status <> 'failed' OR error_code IS NOT NULL),
            CHECK (status NOT IN ('succeeded', 'abstained') OR response IS NOT NULL),
            CHECK (status <> 'abstained'
                   OR (response->>'abstained')::boolean IS TRUE)
        )
        """
    )
    op.execute("CREATE INDEX answer_runs_created_idx ON answer_runs (created_at)")
    op.execute("CREATE INDEX answer_runs_session_idx ON answer_runs (session_id)")
    # R4-01: estado terminal nunca regresse, mesmo fora do repository.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION answer_runs_no_terminal_regression()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('succeeded', 'abstained', 'failed', 'cancelled')
               AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION
                    'execução em estado terminal (%) não pode mudar de status',
                    OLD.status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER answer_runs_terminal_regression
        BEFORE UPDATE ON answer_runs
        FOR EACH ROW EXECUTE FUNCTION answer_runs_no_terminal_regression()
        """
    )
    op.execute(
        """
        CREATE TABLE session_entries (
            id uuid PRIMARY KEY,
            session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            ordinal int NOT NULL CHECK (ordinal >= 0),
            question_original text NOT NULL,
            question_anonymized text NOT NULL,
            rewritten_query text,
            answer_run_id uuid REFERENCES answer_runs(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (session_id, ordinal)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_entries")
    op.execute("DROP TABLE IF EXISTS answer_runs")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS concept_evidence")
    op.execute("DROP TABLE IF EXISTS concept_aliases")
    op.execute("DROP TABLE IF EXISTS concepts")
    op.execute("DROP TABLE IF EXISTS summary_supports")
    op.execute("DROP TABLE IF EXISTS summaries")
    op.execute("DROP TABLE IF EXISTS passages")
    for table in _VERSION_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP TABLE IF EXISTS pages")
    op.execute("DROP TABLE IF EXISTS sections")
    op.execute("DROP TABLE IF EXISTS derived_artifacts")
    op.execute("DROP TABLE IF EXISTS editions")
    op.execute("DROP TABLE IF EXISTS contributors")
    op.execute("DROP TABLE IF EXISTS works")
    op.execute("DROP FUNCTION IF EXISTS answer_runs_no_terminal_regression")
    op.execute("DROP FUNCTION IF EXISTS summaries_chapter_is_top_level")
    op.execute("DROP FUNCTION IF EXISTS rag_reject_mutation")
    op.execute("DROP FUNCTION IF EXISTS rag_immutable_unaccent")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS portuguese_unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS vector")
