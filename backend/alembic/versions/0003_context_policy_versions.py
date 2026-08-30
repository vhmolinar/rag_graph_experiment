"""Tabela de versão da política de montagem de contexto (T12; SPEC §6, AC-15).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-30

A política de contexto (número de evidências, orçamento de contexto,
expansão parental, limite flexível por edição) afeta a resposta e deve ser
versionada (SPEC §2). Aditiva: nova tabela gemelha de
`retrieval_policy_versions` (T09), sem alterar tabelas existentes. Trigger de
imutabilidade mesmo padrão das tabelas de versão (SPEC §6).
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
        CREATE TABLE context_policy_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """
    )
    op.execute(
        "CREATE TRIGGER context_policy_versions_immutable "
        "BEFORE UPDATE OR DELETE ON context_policy_versions "
        "FOR EACH ROW EXECUTE FUNCTION rag_reject_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS context_policy_versions")
