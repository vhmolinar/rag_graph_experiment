"""Persiste os warnings de extração da edição (T05; correção T5-08).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29

`rag inspect` precisa exibir os warnings emitidos durante `rag ingest` depois
que o processo termina; sem persistência eles só existiam na saída de console
daquela execução.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE editions "
        "ADD COLUMN extraction_warnings jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE editions DROP COLUMN extraction_warnings")
