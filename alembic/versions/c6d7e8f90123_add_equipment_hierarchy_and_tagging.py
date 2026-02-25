"""add equipment hierarchy and tagging

Revision ID: c6d7e8f90123
Revises: a1c2e3f4b5d6
Create Date: 2026-02-25 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c6d7e8f90123"
down_revision = "a1c2e3f4b5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("equipment") as batch:
        batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("asset_category", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("asset_type", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("criticality", sa.String(length=10), nullable=False, server_default=sa.text("'B'")))
        batch.add_column(sa.Column("duty_cycle_hours_per_day", sa.Float(), nullable=True))
        batch.add_column(sa.Column("spares_class", sa.String(length=30), nullable=False, server_default=sa.text("'standard'")))
        batch.add_column(sa.Column("safety_classification", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

        batch.create_foreign_key(
            "fk_equipment_parent_id_equipment",
            "equipment",
            ["parent_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch.create_index("ix_equipment_parent_id", ["parent_id"], unique=False)
        batch.create_index("ix_equipment_asset_category", ["asset_category"], unique=False)
        batch.create_index("ix_equipment_asset_type", ["asset_type"], unique=False)
        batch.create_index("ix_equipment_criticality", ["criticality"], unique=False)
        batch.create_index("ix_equipment_spares_class", ["spares_class"], unique=False)

    op.execute("UPDATE equipment SET criticality = 'B' WHERE criticality IS NULL OR TRIM(criticality) = ''")
    op.execute("UPDATE equipment SET spares_class = 'standard' WHERE spares_class IS NULL OR TRIM(spares_class) = ''")
    op.execute("UPDATE equipment SET safety_classification = '[]' WHERE safety_classification IS NULL")
    op.execute("UPDATE equipment SET parent_id = NULL WHERE parent_id IS NOT NULL AND parent_id = id")


def downgrade() -> None:
    with op.batch_alter_table("equipment") as batch:
        batch.drop_index("ix_equipment_spares_class")
        batch.drop_index("ix_equipment_criticality")
        batch.drop_index("ix_equipment_asset_type")
        batch.drop_index("ix_equipment_asset_category")
        batch.drop_index("ix_equipment_parent_id")

        batch.drop_constraint("fk_equipment_parent_id_equipment", type_="foreignkey")

        batch.drop_column("safety_classification")
        batch.drop_column("spares_class")
        batch.drop_column("duty_cycle_hours_per_day")
        batch.drop_column("criticality")
        batch.drop_column("asset_type")
        batch.drop_column("asset_category")
        batch.drop_column("parent_id")
