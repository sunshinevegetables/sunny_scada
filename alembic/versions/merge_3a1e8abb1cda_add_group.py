"""merge heads: 3a1e8abb1cda + add_group_to_containers_equipment

Revision ID: merge_3a1e8abb1cda_add_group
Revises: 3a1e8abb1cda, add_group_to_containers_equipment
Create Date: 2026-02-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'merge_3a1e8abb1cda_add_group'
down_revision = ('3a1e8abb1cda', 'add_group_to_containers_equipment')
branch_labels = None
depends_on = None


def upgrade():
    # merge-only migration: no DB changes, just unify heads
    pass


def downgrade():
    # nothing to undo for a merge-only revision
    pass
