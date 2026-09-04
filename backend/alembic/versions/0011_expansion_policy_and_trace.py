"""Política de expansão versionada + rastreabilidade das expansões (R03).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-03

A estratégia `expanded` (SPEC §8.3, B02/R03) executa consultas de expansão
(subperguntas e aliases) com um orçamento TOTAL por profundidade: essa
política afeta a resposta e deve ser versionada (SPEC §2, AC-15), gemelha de
`retrieval_policy_versions`/`context_policy_versions`. Aditiva: nova tabela +
coluna `answer_runs.expansions` para registrar as consultas executadas e os
scores/posições de cada expansão (rastreabilidade — B02/R03). Trigger de
imutabilidade mesmo padrão das tabelas de versão (SPEC §6).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE expansion_policy_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """
    )
    op.execute(
        "CREATE TRIGGER expansion_policy_versions_immutable "
        "BEFORE UPDATE OR DELETE ON expansion_policy_versions "
        "FOR EACH ROW EXECUTE FUNCTION rag_reject_mutation()"
    )
    op.execute(
        "ALTER TABLE answer_runs ADD COLUMN expansions jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE answer_runs DROP COLUMN IF EXISTS expansions")
    op.execute("DROP TABLE IF EXISTS expansion_policy_versions")
