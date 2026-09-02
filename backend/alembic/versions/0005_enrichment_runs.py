"""Execuções de enriquecimento hierárquico (T11, correção T11-03 e R2-T11-01).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-01

Corrige REVIEW_T11.md T11-03: a idempotência de enriquecimento era decidida
pela mera existência de sínteses (`summaries`), que falhava quando TODOS os
itens de uma execução fossem rejeitados (provedor devolveu suporte vazio em
todos os escopos — comportamento permitido pelo contrato). `enrichment_runs`
registra UMA execução de enriquecimento concluída — inclusive sem itens
publicados — na mesma transação dos itens (NOTES.md §10.12 item 5).
Reexecução com a MESMA identidade é idempotente; com identidade NOVA acumula —
histórico de execuções nunca é sobrescrito (AC-15).

Corrige REVIEW_T11_ROUND2.md R2-T11-01: a identidade de uma execução de
enriquecimento é (edição, execução de indexação, versão de síntese) —
`index_run_id` integra a chave. Reindexar a edição (novo `IndexRun`, novas
passagens ativas) com o MESMO modelo de enriquecimento NÃO é no-op: a nova
execução ativa exige uma nova execução de enriquecimento sobre o conjunto
corrente (T11-02, SPEC §7.4/§8.7), e as execuções anteriores ficam preservadas.

`UNIQUE (edition_id, index_run_id, summarizer_version_id)` impede duas
execuções da mesma identidade convivendo; `index_run_id` referencia a execução
de indexação via FK composta `(index_run_id, edition_id) → index_runs(id,
edition_id)`, garantindo que a execução pertença à edição da síntese (R01).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE enrichment_runs (
            id uuid PRIMARY KEY,
            edition_id uuid NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
            index_run_id uuid NOT NULL,
            summarizer_version_id uuid NOT NULL REFERENCES model_endpoint_versions(id),
            extractor_version_id uuid NOT NULL REFERENCES model_endpoint_versions(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            -- R01: a execução de indexação deve pertencer à edição da síntese.
            FOREIGN KEY (index_run_id, edition_id) REFERENCES index_runs (id, edition_id)
        )
        """
    )
    op.execute("CREATE INDEX enrichment_runs_edition_idx ON enrichment_runs (edition_id)")
    op.execute(
        "CREATE UNIQUE INDEX enrichment_runs_one_per_edition_run_version "
        "ON enrichment_runs (edition_id, index_run_id, summarizer_version_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE enrichment_runs")
