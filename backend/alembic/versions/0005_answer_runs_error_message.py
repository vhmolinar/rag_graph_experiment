"""Persiste a mensagem segura do falha em `answer_runs` (T14).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-30

`GET /queries/{id}` de uma execução falhada precisa devolver ao cliente o
erro tipado (code + mensagem segura, SPEC §10.1). O `AnswerRun` já tinha
`error_code`; a columna `error_message` guarda a mensagem do `RagError`
(que por construção é segura para o cliente — `domain/errors.py`) para
que o estado persista e seja inspecionável após a execução.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE answer_runs ADD COLUMN error_message text"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE answer_runs DROP COLUMN error_message")
