"""add instrument_id to schedules

Revision ID: e2f7c9a1b6d4
Revises: d9e1a6b4c3f2
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e2f7c9a1b6d4"
down_revision = "d9e1a6b4c3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("schedules") as batch:
        batch.add_column(sa.Column("instrument_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_schedules_instrument_id",
            "instruments",
            ["instrument_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_schedules_target",
            "(equipment_id IS NOT NULL AND instrument_id IS NULL) OR (equipment_id IS NULL AND instrument_id IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("schedules") as batch:
        batch.drop_constraint("ck_schedules_target", type_="check")
        batch.drop_constraint("fk_schedules_instrument_id", type_="foreignkey")
        batch.drop_column("instrument_id")
