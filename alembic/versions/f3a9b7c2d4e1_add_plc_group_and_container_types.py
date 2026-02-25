"""add plc group and container types

Revision ID: f3a9b7c2d4e1
Revises: e2f7c9a1b6d4
Create Date: 2026-02-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f3a9b7c2d4e1"
down_revision = "e2f7c9a1b6d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cfg_container_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("name", name="uq_cfg_container_types_name"),
    )
    op.create_index(op.f("ix_cfg_container_types_name"), "cfg_container_types", ["name"], unique=True)

    with op.batch_alter_table("cfg_plcs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("group_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_cfg_plcs_group_id_cfg_data_point_groups",
            "cfg_data_point_groups",
            ["group_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index(op.f("ix_cfg_plcs_group_id"), "cfg_plcs", ["group_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cfg_plcs_group_id"), table_name="cfg_plcs")
    with op.batch_alter_table("cfg_plcs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_cfg_plcs_group_id_cfg_data_point_groups", type_="foreignkey")
        batch_op.drop_column("group_id")

    op.drop_index(op.f("ix_cfg_container_types_name"), table_name="cfg_container_types")
    op.drop_table("cfg_container_types")
