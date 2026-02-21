"""add cfg_data_point_id to historian tables

Revision ID: 8b7d2c4f1e9a
Revises: f0d5c915b710, a8b2c4d6e8f0
Create Date: 2026-02-21
"""

from __future__ import annotations

from collections import defaultdict

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8b7d2c4f1e9a"
down_revision = ("f0d5c915b710", "a8b2c4d6e8f0")
branch_labels = None
depends_on = None


def _parse_db_dp(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    if not text.startswith("db-dp:"):
        return None
    suffix = text.split(":", 1)[1].strip()
    if not suffix.isdigit():
        return None
    return int(suffix)


def _build_unique_plc_label_map(bind) -> dict[tuple[str, str], int]:
    sql = sa.text(
        """
        SELECT p.name AS plc_name, dp.label AS label, dp.id AS dp_id
        FROM cfg_data_points dp
        JOIN cfg_plcs p
          ON dp.owner_type = 'plc' AND dp.owner_id = p.id
        UNION ALL
        SELECT p.name AS plc_name, dp.label AS label, dp.id AS dp_id
        FROM cfg_data_points dp
        JOIN cfg_containers c
          ON dp.owner_type = 'container' AND dp.owner_id = c.id
        JOIN cfg_plcs p
          ON c.plc_id = p.id
        UNION ALL
        SELECT p.name AS plc_name, dp.label AS label, dp.id AS dp_id
        FROM cfg_data_points dp
        JOIN cfg_equipment e
          ON dp.owner_type = 'equipment' AND dp.owner_id = e.id
        JOIN cfg_containers c
          ON e.container_id = c.id
        JOIN cfg_plcs p
          ON c.plc_id = p.id
        """
    )
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in bind.execute(sql).fetchall():
        plc_name = str(row[0])
        label = str(row[1])
        dp_id = int(row[2])
        buckets[(plc_name, label)].append(dp_id)

    unique_map: dict[tuple[str, str], int] = {}
    for key, vals in buckets.items():
        unique_vals = sorted(set(vals))
        if len(unique_vals) == 1:
            unique_map[key] = unique_vals[0]
    return unique_map


def _backfill_table(bind, table_name: str, id_col: str, ts_col: str, scope_map: dict[tuple[str, str], int]) -> tuple[int, int, int]:
    parsed_updates = 0
    scoped_updates = 0

    rows = bind.execute(
        sa.text(
            f"SELECT {id_col}, plc_id, datapoint_id FROM {table_name} WHERE cfg_data_point_id IS NULL"
        )
    ).fetchall()

    for row in rows:
        row_id = int(row[0])
        plc_name = str(row[1]) if row[1] is not None else ""
        datapoint_id = str(row[2]) if row[2] is not None else ""

        parsed_id = _parse_db_dp(datapoint_id)
        if parsed_id is not None:
            exists = bind.execute(
                sa.text("SELECT id FROM cfg_data_points WHERE id = :id"),
                {"id": parsed_id},
            ).fetchone()
            if exists:
                bind.execute(
                    sa.text(
                        f"UPDATE {table_name} SET cfg_data_point_id = :dp_id WHERE {id_col} = :row_id"
                    ),
                    {"dp_id": parsed_id, "row_id": row_id},
                )
                parsed_updates += 1
                continue

        scoped_id = scope_map.get((plc_name, datapoint_id))
        if scoped_id is not None:
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} SET cfg_data_point_id = :dp_id WHERE {id_col} = :row_id"
                ),
                {"dp_id": int(scoped_id), "row_id": row_id},
            )
            scoped_updates += 1

    remaining = bind.execute(
        sa.text(f"SELECT COUNT(1) FROM {table_name} WHERE cfg_data_point_id IS NULL")
    ).scalar_one()

    print(
        f"[alembic] {table_name} backfill: parsed_db_dp={parsed_updates} scoped_unique={scoped_updates} remaining_null={remaining}"
    )
    return parsed_updates, scoped_updates, int(remaining)


def upgrade() -> None:
    with op.batch_alter_table("historian_samples") as batch:
        batch.add_column(sa.Column("cfg_data_point_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_hist_samples_cfg_dp_id",
            "cfg_data_points",
            ["cfg_data_point_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_hist_samples_cfg_dp_id", "historian_samples", ["cfg_data_point_id"], unique=False)
    op.create_index("ix_hist_samples_cfg_dp_ts", "historian_samples", ["cfg_data_point_id", "ts"], unique=False)

    with op.batch_alter_table("historian_hourly_rollups") as batch:
        batch.add_column(sa.Column("cfg_data_point_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_hist_rollups_cfg_dp_id",
            "cfg_data_points",
            ["cfg_data_point_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_hist_rollups_cfg_dp_id", "historian_hourly_rollups", ["cfg_data_point_id"], unique=False)
    op.create_index(
        "ix_hist_rollups_cfg_dp_bucket",
        "historian_hourly_rollups",
        ["cfg_data_point_id", "bucket_start"],
        unique=False,
    )

    bind = op.get_bind()
    scope_map = _build_unique_plc_label_map(bind)

    _backfill_table(bind, "historian_samples", "id", "ts", scope_map)
    _backfill_table(bind, "historian_hourly_rollups", "id", "bucket_start", scope_map)


def downgrade() -> None:
    op.drop_index("ix_hist_rollups_cfg_dp_bucket", table_name="historian_hourly_rollups")
    op.drop_index("ix_hist_rollups_cfg_dp_id", table_name="historian_hourly_rollups")
    with op.batch_alter_table("historian_hourly_rollups") as batch:
        batch.drop_constraint("fk_hist_rollups_cfg_dp_id", type_="foreignkey")
        batch.drop_column("cfg_data_point_id")

    op.drop_index("ix_hist_samples_cfg_dp_ts", table_name="historian_samples")
    op.drop_index("ix_hist_samples_cfg_dp_id", table_name="historian_samples")
    with op.batch_alter_table("historian_samples") as batch:
        batch.drop_constraint("fk_hist_samples_cfg_dp_id", type_="foreignkey")
        batch.drop_column("cfg_data_point_id")
