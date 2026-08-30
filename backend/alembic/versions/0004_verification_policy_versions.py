"""Tabela de versão da política de verificação (T13; SPEC §6, AC-15).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-30

A política de verificação (iterações de geração/verificação e limiar de
cobertura de citações) afeta a resposta e deve ser versionada (SPEC §2).
Aditiva: nova tabela gemelha de `context_policy_versions` (T12, migration
0003), sem alterar tabelas existentes. Trigger de imutabilidade mesmo padrão
das tabelas de versão (SPEC §6).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE verification_policy_versions (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (length(btrim(label)) > 0),
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL,
            UNIQUE (label, params)
        )
        """
    )
    op.execute(
        "CREATE TRIGGER verification_policy_versions_immutable "
        "BEFORE UPDATE OR DELETE ON verification_policy_versions "
        "FOR EACH ROW EXECUTE FUNCTION rag_reject_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification_policy_versions")
