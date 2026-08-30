"""Persist canonical extraction identity for reproducible indexing."""

from alembic import op

revision = "0003_canonical_fingerprint"
down_revision = "0002_edition_extraction_warnings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE editions ADD COLUMN canonical_fingerprint text")
    op.execute(
        "ALTER TABLE editions ADD CONSTRAINT editions_canonical_fingerprint_ck "
        "CHECK (canonical_fingerprint IS NULL OR canonical_fingerprint ~ '^[0-9a-f]{64}$')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE editions DROP CONSTRAINT editions_canonical_fingerprint_ck")
    op.execute("ALTER TABLE editions DROP COLUMN canonical_fingerprint")
