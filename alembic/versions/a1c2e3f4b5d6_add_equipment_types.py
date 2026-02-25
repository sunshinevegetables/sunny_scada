"""add equipment types

Revision ID: a1c2e3f4b5d6
Revises: f3a9b7c2d4e1
Create Date: 2026-02-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a1c2e3f4b5d6"
down_revision = "f3a9b7c2d4e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cfg_equipment_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("name", name="uq_cfg_equipment_types_name"),
    )
    op.create_index(op.f("ix_cfg_equipment_types_name"), "cfg_equipment_types", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_cfg_equipment_types_name"), table_name="cfg_equipment_types")
    op.drop_table("cfg_equipment_types")
