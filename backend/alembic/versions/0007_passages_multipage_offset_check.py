"""Corrige o CHECK de offsets de passagens para permitir passagens multipágina.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-01

O CHECK original (`passages_check1`, migration 0001) exigia
`char_end > char_start` incondicionalmente. Para uma passagem que atravessa
páginas, `char_start` é relativo à página de início e `char_end` à página de
fim (NOTES.md §10.6 item 3) — podem ser invertidos entre páginas (ex.: início
no offset 100 da página A, fim no offset 3 da página B) e ainda ser corretos
(T12-R2-01, AC-03). A nova condição aplica `char_end > char_start` somente
quando as páginas NÃO são distintas (mesma página, ou sem páginas).

Revisa `passages_check1` no lugar (mesmo nome), evitando renomear constraintos
existentes; a migração é reversível.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE passages DROP CONSTRAINT passages_check1")
    op.execute(
        """
        ALTER TABLE passages ADD CONSTRAINT passages_check1 CHECK (
            char_end IS NULL
            OR (page_start_id IS NOT NULL AND page_end_id IS NOT NULL
                AND page_start_id <> page_end_id)
            OR char_end > char_start
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE passages DROP CONSTRAINT passages_check1")
    op.execute(
        "ALTER TABLE passages ADD CONSTRAINT passages_check1 CHECK "
        "(char_end IS NULL OR char_end > char_start)"
    )
