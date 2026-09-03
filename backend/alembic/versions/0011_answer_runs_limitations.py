"""Persiste as limitações de resposta em `answer_runs` (R05; AC-11, AC-15).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-03

SPEC §8.6, §9.3, §10.1 (AC-11, AC-15): respostas comparativas que se apoiam
em evidências de somente uma obra derivam determinística de limitação
declarada. Para que essa limitação seja preservada na resposta da API
(GET /queries/{id} e SSE) e inspecionável pós-execução, a coluna `limitations`
guarda a lista tipada em `answer_runs`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE answer_runs ADD COLUMN limitations jsonb NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE answer_runs DROP COLUMN limitations")
