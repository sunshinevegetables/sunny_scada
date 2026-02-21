"""merge alarm overhaul heads

Revision ID: 055f24ab3325
Revises: c0a1f2d3e4f5, c4a7d1c9e2aa
Create Date: 2026-02-19 19:07:59.948686

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '055f24ab3325'
down_revision = ('c0a1f2d3e4f5', 'c4a7d1c9e2aa')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
