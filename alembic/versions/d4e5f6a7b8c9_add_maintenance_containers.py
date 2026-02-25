"""add maintenance containers

Revision ID: d4e5f6a7b8c9
Revises: c6d7e8f90123
Create Date: 2026-02-25 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c6d7e8f90123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_containers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("container_code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("asset_category", sa.String(length=50), nullable=True),
        sa.Column("asset_type", sa.String(length=100), nullable=True),
        sa.Column("criticality", sa.String(length=10), nullable=False, server_default=sa.text("'B'")),
        sa.Column("duty_cycle_hours_per_day", sa.Float(), nullable=True),
        sa.Column("spares_class", sa.String(length=30), nullable=False, server_default=sa.text("'standard'")),
        sa.Column("safety_classification", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.ForeignKeyConstraint(["parent_id"], ["maintenance_containers.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("container_code", name="uq_maintenance_containers_container_code"),
    )
    op.create_index("ix_maintenance_containers_container_code", "maintenance_containers", ["container_code"], unique=True)
    op.create_index("ix_maintenance_containers_name", "maintenance_containers", ["name"], unique=False)
    op.create_index("ix_maintenance_containers_parent_id", "maintenance_containers", ["parent_id"], unique=False)
    op.create_index("ix_maintenance_containers_asset_category", "maintenance_containers", ["asset_category"], unique=False)
    op.create_index("ix_maintenance_containers_asset_type", "maintenance_containers", ["asset_type"], unique=False)
    op.create_index("ix_maintenance_containers_criticality", "maintenance_containers", ["criticality"], unique=False)
    op.create_index("ix_maintenance_containers_spares_class", "maintenance_containers", ["spares_class"], unique=False)

    with op.batch_alter_table("equipment") as batch:
        batch.add_column(sa.Column("container_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_equipment_container_id_maintenance_containers",
            "maintenance_containers",
            ["container_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_equipment_container_id", ["container_id"], unique=False)

    # Backfill maintenance containers from cfg containers (preserve IDs to simplify mapping).
    op.execute(
        """
        INSERT INTO maintenance_containers (
            id, container_code, name, location, description,
            parent_id, asset_category, asset_type,
            criticality, duty_cycle_hours_per_day, spares_class, safety_classification,
            is_active, meta
        )
        SELECT
            c.id,
            'MC-' || printf('%06d', c.id),
            c.name,
            NULL,
            'Auto-created from config tree (ID: ' || c.id || ')',
            NULL,
            NULL,
            NULL,
            'B',
            NULL,
            'standard',
            '[]',
            1,
            '{}'
        FROM cfg_containers c
        WHERE NOT EXISTS (
            SELECT 1 FROM maintenance_containers mc WHERE mc.id = c.id
        )
        """
    )

    # Backfill equipment.container_id from cfg equipment IDs when equipment IDs match cfg IDs.
    op.execute(
        """
        UPDATE equipment
        SET container_id = (
            SELECT ce.container_id FROM cfg_equipment ce WHERE ce.id = equipment.id
        )
        WHERE EXISTS (
            SELECT 1 FROM cfg_equipment ce2 WHERE ce2.id = equipment.id
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("equipment") as batch:
        batch.drop_index("ix_equipment_container_id")
        batch.drop_constraint("fk_equipment_container_id_maintenance_containers", type_="foreignkey")
        batch.drop_column("container_id")

    op.drop_index("ix_maintenance_containers_spares_class", table_name="maintenance_containers")
    op.drop_index("ix_maintenance_containers_criticality", table_name="maintenance_containers")
    op.drop_index("ix_maintenance_containers_asset_type", table_name="maintenance_containers")
    op.drop_index("ix_maintenance_containers_asset_category", table_name="maintenance_containers")
    op.drop_index("ix_maintenance_containers_parent_id", table_name="maintenance_containers")
    op.drop_index("ix_maintenance_containers_name", table_name="maintenance_containers")
    op.drop_index("ix_maintenance_containers_container_code", table_name="maintenance_containers")
    op.drop_table("maintenance_containers")
