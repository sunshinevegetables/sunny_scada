"""add group_id to containers and equipment

Revision ID: add_group_to_containers_equipment
Revises: 
Create Date: 2026-02-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_group_to_containers_equipment'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('cfg_containers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_cfg_containers_group_id_cfg_data_point_groups',
            'cfg_data_point_groups',
            ['group_id'],
            ['id'],
            ondelete='RESTRICT',
        )

    with op.batch_alter_table('cfg_equipment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_cfg_equipment_group_id_cfg_data_point_groups',
            'cfg_data_point_groups',
            ['group_id'],
            ['id'],
            ondelete='RESTRICT',
        )


def downgrade():
    with op.batch_alter_table('cfg_equipment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_cfg_equipment_group_id_cfg_data_point_groups'), type_='foreignkey')
        batch_op.drop_column('group_id')

    with op.batch_alter_table('cfg_containers', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_cfg_containers_group_id_cfg_data_point_groups'), type_='foreignkey')
        batch_op.drop_column('group_id')
