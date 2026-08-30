"""Execuções de indexação versionadas e texto original citável (T06 R01).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29

Corrige REVIEW_T06.md T6-01/T6-08: reindexação deixava de comparar a
configuração ativa e `--force` apagava fisicamente o histórico de passagens,
tornando `AnswerRun`s antigos irreprodutíveis (SPEC §6). `index_runs` registra
cada execução de indexação (edição + versões de extração/chunking/embedding/
endpoint) e mantém no máximo UMA execução ativa por edição
(`index_runs_one_active_per_edition`); passagens de execuções antigas
permanecem no banco, nunca são apagadas.

`passages.index_run_id` é opcional (NULL) de propósito: linhas inseridas fora
do fluxo de `rag index` (testes de repository, dados legados) não pertencem a
nenhuma execução rastreada. `UNIQUE (edition_id, ordinal)` é substituída por
`UNIQUE (index_run_id, ordinal)` — múltiplas execuções da mesma edição
reutilizam ordinais a partir de 0; NULLs continuam sem colidir entre si
(semântica padrão do PostgreSQL para UNIQUE).

`passages.original_text` corrige T6-05: preserva o texto original (por bloco
canônico) que alimenta a citação literal — `text` (normalizado) continua
sendo a base de busca/embeddings.

Deliberadamente NÃO há UNIQUE sobre (edition_id, extraction_version_id,
chunking_version_id, embedding_version_id, model_endpoint_version_id):
`--force` minta uma nova execução mesmo com a MESMA identidade de versões
(reprocessar sem mudar parâmetro algum), o que exigiria coexistir com a
execução antiga (agora inativa) sob a mesma identidade. A serialização por
`pg_advisory_xact_lock(edition_id)` em `IndexingService`, não uma restrição
de unicidade sobre o histórico, é quem impede duas execuções ATIVAS
concorrentes da mesma edição (T6-10) — reforçada pelo índice único parcial
`index_runs_one_active_per_edition`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE index_runs (
            id uuid PRIMARY KEY,
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            extraction_version_id uuid NOT NULL REFERENCES extraction_versions(id),
            chunking_version_id uuid NOT NULL REFERENCES chunking_versions(id),
            embedding_version_id uuid NOT NULL REFERENCES embedding_versions(id),
            model_endpoint_version_id uuid NOT NULL REFERENCES model_endpoint_versions(id),
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            -- R01: FK composta de passages garante que a execução referenciada
            -- pertence à mesma edição da passagem.
            UNIQUE (id, edition_id)
        )
        """
    )
    op.execute("CREATE INDEX index_runs_edition_idx ON index_runs (edition_id)")
    # Nunca mais de uma execução ativa por edição — a seleção do conjunto
    # corrente é explícita (T6-01), nunca inferida pela mera existência de
    # passagens.
    op.execute(
        "CREATE UNIQUE INDEX index_runs_one_active_per_edition "
        "ON index_runs (edition_id) WHERE is_active"
    )

    op.execute("ALTER TABLE passages ADD COLUMN index_run_id uuid")
    op.execute("ALTER TABLE passages ADD COLUMN original_text text")
    op.execute(
        """
        ALTER TABLE passages ADD CONSTRAINT passages_index_run_edition_fk
            FOREIGN KEY (index_run_id, edition_id) REFERENCES index_runs (id, edition_id)
        """
    )
    # O nome do UNIQUE (edition_id, ordinal) criado sem nome explícito em 0001
    # segue a convenção padrão do PostgreSQL, mas é resolvido dinamicamente
    # para nunca depender de um palpite de nome de identificador.
    op.execute(
        """
        DO $$
        DECLARE
            cname text;
        BEGIN
            SELECT conname INTO cname FROM pg_constraint
            WHERE conrelid = 'passages'::regclass AND contype = 'u'
              AND conkey = (
                  SELECT array_agg(attnum ORDER BY attnum) FROM pg_attribute
                  WHERE attrelid = 'passages'::regclass AND attname IN ('edition_id', 'ordinal')
              );
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE passages DROP CONSTRAINT %I', cname);
            END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE passages ADD CONSTRAINT passages_index_run_ordinal_key "
        "UNIQUE (index_run_id, ordinal)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE passages DROP CONSTRAINT passages_index_run_ordinal_key")
    op.execute(
        "ALTER TABLE passages ADD CONSTRAINT passages_edition_id_ordinal_key "
        "UNIQUE (edition_id, ordinal)"
    )
    op.execute("ALTER TABLE passages DROP CONSTRAINT passages_index_run_edition_fk")
    op.execute("ALTER TABLE passages DROP COLUMN original_text")
    op.execute("ALTER TABLE passages DROP COLUMN index_run_id")
    op.execute("DROP TABLE index_runs")
