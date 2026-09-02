"""Persiste o request_id da requisição HTTP que iniciou a consulta (T14).

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-02

Falhas de consultas assíncronas (estado `GET /queries/{id}` e evento SSE
terminal) precisam correlacionar com o log da requisição que as iniciou: o
`request_id` do envelope de erro deve corresponder ao `X-Request-ID` da
requisição original (SPEC §10.1, checklist §14). A columna guarda-o em
`answer_runs`; registros existentes recebem vazio ('').
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE answer_runs ADD COLUMN request_id text NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE answer_runs DROP COLUMN request_id")
