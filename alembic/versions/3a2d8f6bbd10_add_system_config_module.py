"""add system config module tables

Revision ID: 3a2d8f6bbd10
Revises: b1f131592650
Create Date: 2026-01-05

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3a2d8f6bbd10"
down_revision = "b1f131592650"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cfg_plcs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("ip", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_cfg_plcs_name"), "cfg_plcs", ["name"], unique=True)
    op.create_index(op.f("ix_cfg_plcs_ip"), "cfg_plcs", ["ip"], unique=False)

    op.create_table(
        "cfg_containers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plc_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["plc_id"], ["cfg_plcs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plc_id", "name", name="uq_cfg_container_plc_name"),
    )
    op.create_index(op.f("ix_cfg_containers_plc_id"), "cfg_containers", ["plc_id"], unique=False)

    op.create_table(
        "cfg_equipment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("container_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["container_id"], ["cfg_containers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("container_id", "name", name="uq_cfg_equipment_container_name"),
    )
    op.create_index(op.f("ix_cfg_equipment_container_id"), "cfg_equipment", ["container_id"], unique=False)

    op.create_table(
        "cfg_data_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_type", sa.String(length=30), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("category", sa.String(length=10), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_type", "owner_id", "label", name="uq_cfg_dp_owner_label"),
    )
    op.create_index(op.f("ix_cfg_data_points_owner_type"), "cfg_data_points", ["owner_type"], unique=False)
    op.create_index(op.f("ix_cfg_data_points_owner_id"), "cfg_data_points", ["owner_id"], unique=False)
    op.create_index(op.f("ix_cfg_data_points_category"), "cfg_data_points", ["category"], unique=False)
    op.create_index(op.f("ix_cfg_data_points_type"), "cfg_data_points", ["type"], unique=False)

    op.create_table(
        "cfg_data_point_bits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("data_point_id", sa.Integer(), nullable=False),
        sa.Column("bit", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["data_point_id"], ["cfg_data_points.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("data_point_id", "bit", name="uq_cfg_dp_bit"),
    )
    op.create_index(op.f("ix_cfg_data_point_bits_data_point_id"), "cfg_data_point_bits", ["data_point_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cfg_data_point_bits_data_point_id"), table_name="cfg_data_point_bits")
    op.drop_table("cfg_data_point_bits")
    op.drop_index(op.f("ix_cfg_data_points_type"), table_name="cfg_data_points")
    op.drop_index(op.f("ix_cfg_data_points_category"), table_name="cfg_data_points")
    op.drop_index(op.f("ix_cfg_data_points_owner_id"), table_name="cfg_data_points")
    op.drop_index(op.f("ix_cfg_data_points_owner_type"), table_name="cfg_data_points")
    op.drop_table("cfg_data_points")
    op.drop_index(op.f("ix_cfg_equipment_container_id"), table_name="cfg_equipment")
    op.drop_table("cfg_equipment")
    op.drop_index(op.f("ix_cfg_containers_plc_id"), table_name="cfg_containers")
    op.drop_table("cfg_containers")
    op.drop_index(op.f("ix_cfg_plcs_ip"), table_name="cfg_plcs")
    op.drop_index(op.f("ix_cfg_plcs_name"), table_name="cfg_plcs")
    op.drop_table("cfg_plcs")
