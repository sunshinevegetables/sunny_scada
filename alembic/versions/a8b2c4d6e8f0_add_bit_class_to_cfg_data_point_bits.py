"""add bit_class to cfg_data_point_bits

Revision ID: a8b2c4d6e8f0
Revises: 055f24ab3325
Create Date: 2026-02-20

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a8b2c4d6e8f0"
down_revision = "055f24ab3325"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cfg_data_point_bits", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bit_class", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cfg_data_point_bits", schema=None) as batch_op:
        batch_op.drop_column("bit_class")
