from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sunny_scada.db.base import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission: Mapped[str] = mapped_column(String(200), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    permissions: Mapped[List[RolePermission]] = relationship(
        "RolePermission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    users: Mapped[List["User"]] = relationship(
        secondary="user_roles",
        back_populates="roles",
        lazy="selectin",
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    roles: Mapped[List[Role]] = relationship(
        secondary="user_roles",
        back_populates="users",
        lazy="selectin",
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship("User", lazy="selectin")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    action: Mapped[str] = mapped_column(String(200), index=True)
    resource: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # NOTE: "metadata" is a reserved attribute name in SQLAlchemy's Declarative API.
    # We keep the underlying DB column name as "metadata" for readability, but expose it
    # as the Python attribute "meta".
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    config_revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("config_revisions.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped[Optional[User]] = relationship("User", lazy="selectin")


class ConfigRevision(Base):
    __tablename__ = "config_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    action: Mapped[str] = mapped_column(String(200), index=True)
    yaml_path: Mapped[str] = mapped_column(String(500), index=True)

    before_yaml: Mapped[str] = mapped_column(Text)
    after_yaml: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text)

    backup_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    user: Mapped[Optional[User]] = relationship("User", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("ts", "yaml_path", "id", name="uq_config_revisions_ts_path_id"),
    )


# -----------------
# Cycle 2: Commands / Logs / Alarms / Maintenance / Historian
# -----------------


def gen_external_id(prefix: str) -> str:
    import secrets
    return f"{prefix}_{secrets.token_urlsafe(12)}"


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_id: Mapped[str] = mapped_column(String(80), unique=True, index=True, default=lambda: gen_external_id("cmd"))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    plc_name: Mapped[str] = mapped_column(String(200), index=True)
    datapoint_id: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(50))  # bit|register
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(40), index=True, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    user: Mapped[Optional[User]] = relationship("User", lazy="selectin")
    events: Mapped[List["CommandEvent"]] = relationship("CommandEvent", back_populates="command", cascade="all, delete-orphan", lazy="selectin")


class CommandEvent(Base):
    __tablename__ = "command_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_row_id: Mapped[int] = mapped_column(ForeignKey("commands.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    command: Mapped[Command] = relationship("Command", back_populates="events", lazy="selectin")


class ServerLog(Base):
    __tablename__ = "server_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    level: Mapped[str] = mapped_column(String(20), index=True)
    logger: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    message: Mapped[str] = mapped_column(Text)

    source: Mapped[str] = mapped_column(String(20), default="backend", index=True)  # backend|client
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    user: Mapped[Optional[User]] = relationship("User", lazy="selectin")


class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alarm_id: Mapped[str] = mapped_column(String(80), unique=True, index=True, default=lambda: gen_external_id("al"))

    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    severity: Mapped[str] = mapped_column(String(30), index=True, default="info")
    message: Mapped[str] = mapped_column(String(500))
    source: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    acked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    acked_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acked_by_client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    acked_by: Mapped[Optional[User]] = relationship("User", lazy="selectin")


# -------- Maintenance (CMMS-lite) --------


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vendor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    vendor: Mapped[Optional[Vendor]] = relationship("Vendor", lazy="selectin")


class SparePart(Base):
    __tablename__ = "spare_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    vendor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)

    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    min_stock: Mapped[int] = mapped_column(Integer, default=0)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    vendor: Mapped[Optional[Vendor]] = relationship("Vendor", lazy="selectin")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_order_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    equipment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True, index=True)
    schedule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)
    task_template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("task_templates.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), index=True, default="open")
    priority: Mapped[str] = mapped_column(String(30), default="normal")

    assigned_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)

    due_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    equipment: Mapped[Optional[Equipment]] = relationship("Equipment", lazy="selectin")
    assigned_user: Mapped[Optional[User]] = relationship("User", foreign_keys=[assigned_user_id], lazy="selectin")
    assigned_role: Mapped[Optional[Role]] = relationship("Role", foreign_keys=[assigned_role_id], lazy="selectin")


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    part_id: Mapped[int] = mapped_column(ForeignKey("spare_parts.id", ondelete="CASCADE"), index=True)
    qty_delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    work_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    part: Mapped[SparePart] = relationship("SparePart", lazy="selectin")
    work_order: Mapped[Optional[WorkOrder]] = relationship("WorkOrder", lazy="selectin")
    user: Mapped[Optional[User]] = relationship("User", lazy="selectin")


class Breakdown(Base):
    __tablename__ = "breakdowns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id", ondelete="CASCADE"), index=True)

    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(30), default="medium")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    reported_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    equipment: Mapped[Equipment] = relationship("Equipment", lazy="selectin")
    reported_by: Mapped[Optional[User]] = relationship("User", lazy="selectin")


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checklist: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Trigger: either cron or interval_minutes
    cron: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    task_template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("task_templates.id", ondelete="SET NULL"), nullable=True)
    equipment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True)

    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    task_template: Mapped[Optional[TaskTemplate]] = relationship("TaskTemplate", lazy="selectin")
    equipment: Mapped[Optional[Equipment]] = relationship("Equipment", lazy="selectin")


# -------- Historian --------


class HistorianSample(Base):
    __tablename__ = "historian_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    plc_id: Mapped[str] = mapped_column(String(200), index=True)
    datapoint_id: Mapped[str] = mapped_column(String(200), index=True)
    value: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(20), default="good")
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)


class HistorianHourlyRollup(Base):
    __tablename__ = "historian_hourly_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)

    plc_id: Mapped[str] = mapped_column(String(200), index=True)
    datapoint_id: Mapped[str] = mapped_column(String(200), index=True)

    avg_value: Mapped[float] = mapped_column(Float)
    min_value: Mapped[float] = mapped_column(Float)
    max_value: Mapped[float] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("bucket_start", "plc_id", "datapoint_id", name="uq_rollup_bucket_plc_dp"),
    )


# -----------------
# System configuration module (PLC / Container / Equipment / Data Points)
# -----------------


class CfgPLC(Base):
    __tablename__ = "cfg_plcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    ip: Mapped[str] = mapped_column(String(255), index=True)
    port: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    containers: Mapped[List["CfgContainer"]] = relationship(
        "CfgContainer",
        back_populates="plc",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CfgContainer(Base):
    __tablename__ = "cfg_containers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plc_id: Mapped[int] = mapped_column(ForeignKey("cfg_plcs.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(200))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    plc: Mapped[CfgPLC] = relationship("CfgPLC", back_populates="containers", lazy="selectin")
    equipment: Mapped[List["CfgEquipment"]] = relationship(
        "CfgEquipment",
        back_populates="container",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("plc_id", "name", name="uq_cfg_container_plc_name"),
    )


class CfgEquipment(Base):
    __tablename__ = "cfg_equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_id: Mapped[int] = mapped_column(ForeignKey("cfg_containers.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(200))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    container: Mapped[CfgContainer] = relationship("CfgContainer", back_populates="equipment", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("container_id", "name", name="uq_cfg_equipment_container_name"),
    )


class CfgDataPoint(Base):
    __tablename__ = "cfg_data_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # polymorphic owner reference
    owner_type: Mapped[str] = mapped_column(String(30), index=True)  # plc|container|equipment
    owner_id: Mapped[int] = mapped_column(Integer, index=True)

    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(10), index=True)  # read|write
    type: Mapped[str] = mapped_column(String(20), index=True)  # INTEGER|DIGITAL|REAL
    address: Mapped[str] = mapped_column(String(200))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    bits: Mapped[List["CfgDataPointBit"]] = relationship(
        "CfgDataPointBit",
        back_populates="data_point",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "label", name="uq_cfg_dp_owner_label"),
    )


class CfgDataPointBit(Base):
    __tablename__ = "cfg_data_point_bits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_point_id: Mapped[int] = mapped_column(ForeignKey("cfg_data_points.id", ondelete="CASCADE"), index=True)
    bit: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))

    data_point: Mapped[CfgDataPoint] = relationship("CfgDataPoint", back_populates="bits", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("data_point_id", "bit", name="uq_cfg_dp_bit"),
    )
