"""Política do estágio hierárquico versionada + auditoria dos nós (R04).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-03

O estágio hierárquico (SPEC §8.7, B03/R04) seleciona sínteses/conceitos
relevantes, desce até as passagens originais e une essas passagens aos
candidatos lexical/vetorial: a política de orçamento afeta a resposta e deve
ser versionada (SPEC §2, AC-15), gemelha de `retrieval_policy_versions`/
`expansion_policy_versions`/`context_policy_versions`. Aditiva: nova tabela +
coluna `answer_runs.hierarchical_hits` para registrar, para auditoria, qual
nó selecionado localizou qual passagem descendente (AC-12/AC-15). Trigger de
imutabilidade mesmo padrão das tabelas de versão (SPEC §6).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hierarchical_policy_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """
    )
    op.execute(
        "CREATE TRIGGER hierarchical_policy_versions_immutable "
        "BEFORE UPDATE OR DELETE ON hierarchical_policy_versions "
        "FOR EACH ROW EXECUTE FUNCTION rag_reject_mutation()"
    )
    op.execute(
        "ALTER TABLE answer_runs ADD COLUMN hierarchical_hits jsonb "
        "NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE answer_runs DROP COLUMN IF EXISTS hierarchical_hits")
    op.execute("DROP TABLE IF EXISTS hierarchical_policy_versions")
