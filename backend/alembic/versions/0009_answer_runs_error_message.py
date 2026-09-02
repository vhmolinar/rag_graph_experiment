"""Persiste a mensagem segura do falha em `answer_runs` (T14).

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-30

`GET /queries/{id}` de uma execução falhada precisa devolver ao cliente o
erro tipado (code + mensagem segura, SPEC §10.1). O `AnswerRun` já tinha
`error_code`; a columna `error_message` guarda a mensagem do `RagError`
(que por construção é segura para o cliente — `domain/errors.py`) para
que o estado persista e seja inspecionável após a execução.

Revisão T14 (2026-09-02): renumerada de 0005 para 0009 — a revisão 0005 já
era usada por `0005_enrichment_runs` (T11); a cadeia é linearizada após
`0008_verification_policy_versions` (T13).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE answer_runs ADD COLUMN error_message text"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE answer_runs DROP COLUMN error_message")
