from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import (
    AlarmEvent,
    AlarmOccurrence,
    CfgContainer,
    CfgDataPoint,
    CfgEquipment,
    CfgPLC,
)

logger = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def make_stable_key(*, source: str, raw: str) -> str:
    """Create a stable key for sources that don't provide a natural dedupe key."""
    h = hashlib.sha1((source + "|" + raw).encode("utf-8")).hexdigest()
    return h


def _alarm_context_for_datapoint(
    db: Session,
    *,
    datapoint_id: Optional[int],
    meta: Optional[Dict[str, Any]],
) -> Dict[str, Optional[str]]:
    context: Dict[str, Optional[str]] = {
        "plc_name": None,
        "container_name": None,
        "equipment_name": None,
        "datapoint_label": None,
    }

    m = dict(meta or {})
    if m.get("plc"):
        context["plc_name"] = str(m.get("plc"))
    if m.get("container"):
        context["container_name"] = str(m.get("container"))
    if m.get("equipment"):
        context["equipment_name"] = str(m.get("equipment"))
    if m.get("label"):
        context["datapoint_label"] = str(m.get("label"))

    if datapoint_id is None:
        return context

    dp = db.query(CfgDataPoint).filter(CfgDataPoint.id == int(datapoint_id)).one_or_none()
    if not dp:
        return context

    context["datapoint_label"] = context["datapoint_label"] or dp.label

    owner_type = (dp.owner_type or "").strip().lower()
    owner_id = int(dp.owner_id)

    if owner_type == "plc":
        plc = db.query(CfgPLC).filter(CfgPLC.id == owner_id).one_or_none()
        if plc:
            context["plc_name"] = context["plc_name"] or plc.name
        return context

    if owner_type == "container":
        container = db.query(CfgContainer).filter(CfgContainer.id == owner_id).one_or_none()
        if not container:
            return context
        context["container_name"] = context["container_name"] or container.name
        plc = db.query(CfgPLC).filter(CfgPLC.id == int(container.plc_id)).one_or_none()
        if plc:
            context["plc_name"] = context["plc_name"] or plc.name
        return context

    if owner_type == "equipment":
        equipment = db.query(CfgEquipment).filter(CfgEquipment.id == owner_id).one_or_none()
        if not equipment:
            return context
        context["equipment_name"] = context["equipment_name"] or equipment.name

        container = db.query(CfgContainer).filter(CfgContainer.id == int(equipment.container_id)).one_or_none()
        if container:
            context["container_name"] = context["container_name"] or container.name
            plc = db.query(CfgPLC).filter(CfgPLC.id == int(container.plc_id)).one_or_none()
            if plc:
                context["plc_name"] = context["plc_name"] or plc.name

    return context


class AlarmManager:
    """Central alarm state manager.

    - Upserts AlarmOccurrence by (source, key)
    - Inserts AlarmEvent only on state transition
    - Commits DB transaction
    - Optionally broadcasts only on transition
    """

    def set_state(
        self,
        db: Session,
        *,
        source: str,
        key: str,
        new_state: str,
        severity: str,
        message: str,
        ts: Optional[dt.datetime] = None,
        datapoint_id: Optional[int] = None,
        rule_id: Optional[int] = None,
        external_rule_id: Optional[str] = None,
        value: Optional[float] = None,
        warning_threshold: Optional[float] = None,
        alarm_threshold: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
        acknowledged_by_user_id: Optional[int] = None,
        acknowledged_by_client_ip: Optional[str] = None,
        broadcast_cb=None,
    ) -> dict:
        ts = ts or _utcnow()

        src = (source or "").strip() or "unknown"
        k = (key or "").strip()
        if not k:
            k = make_stable_key(source=src, raw=(message or ""))

        state = (new_state or "OK").upper().strip()
        if state not in ("OK", "WARNING", "ALARM"):
            state = "OK"

        meta = meta or {}

        try:
            occ = (
                db.query(AlarmOccurrence)
                .filter(AlarmOccurrence.source == src, AlarmOccurrence.key == k)
                .one_or_none()
            )

            created = False
            if occ is None:
                created = True
                occ = AlarmOccurrence(
                    source=src,
                    key=k,
                    datapoint_id=datapoint_id,
                    rule_id=rule_id,
                    external_rule_id=external_rule_id,
                    state="OK",
                    severity=(severity or "info"),
                    message=(message or ""),
                    value=value,
                    warning_threshold=warning_threshold,
                    alarm_threshold=alarm_threshold,
                    first_seen_at=ts,
                    last_seen_at=ts,
                    cleared_at=None,
                    is_active=False,
                    acknowledged=False,
                    acknowledged_at=None,
                    acknowledged_by_user_id=acknowledged_by_user_id,
                    acknowledged_by_client_ip=acknowledged_by_client_ip,
                    meta=meta,
                )
                db.add(occ)
                db.flush()  # assign occ.id

            prev_state = (occ.state or "OK").upper()

            # Always update "last seen" + descriptive fields
            occ.last_seen_at = ts
            if severity:
                occ.severity = severity
            if message:
                occ.message = message

            occ.value = value
            occ.warning_threshold = warning_threshold
            occ.alarm_threshold = alarm_threshold

            if datapoint_id is not None:
                occ.datapoint_id = datapoint_id
            if rule_id is not None:
                occ.rule_id = rule_id
            if external_rule_id is not None:
                occ.external_rule_id = external_rule_id

            if meta:
                merged = dict(occ.meta or {})
                merged.update(meta)
                occ.meta = merged

            transitioned = prev_state != state

            if transitioned:
                occ.state = state
                occ.is_active = state in ("WARNING", "ALARM")
                occ.cleared_at = ts if state == "OK" else None

                # Escalation clears acknowledgement
                if prev_state != "ALARM" and state == "ALARM":
                    occ.acknowledged = False
                    occ.acknowledged_at = None
                    occ.acknowledged_by_user_id = None
                    occ.acknowledged_by_client_ip = None

                evt = AlarmEvent(
                    occurrence_id=occ.id,
                    ts=ts,
                    source=src,
                    key=k,
                    datapoint_id=occ.datapoint_id,
                    rule_id=occ.rule_id,
                    external_rule_id=occ.external_rule_id,
                    prev_state=prev_state,
                    new_state=state,
                    severity=occ.severity or "info",
                    message=(message or occ.message or ""),
                    value=value,
                    meta=meta or {},
                )
                db.add(evt)

            db.add(occ)
            db.commit()

        except Exception:
            db.rollback()
            logger.exception("AlarmManager.set_state failed (rolled back). source=%s key=%s", src, k)
            raise

        context = _alarm_context_for_datapoint(
            db,
            datapoint_id=occ.datapoint_id,
            meta=(occ.meta or {}),
        )

        payload = {
            "type": "alarm_state",
            "ts": ts.isoformat(),
            "source": src,
            "datapoint_id": occ.datapoint_id,
            "datapoint_label": context.get("datapoint_label"),
            "plc_name": context.get("plc_name"),
            "container_name": context.get("container_name"),
            "equipment_name": context.get("equipment_name"),
            "rule_id": occ.rule_id,
            "external_rule_id": occ.external_rule_id,
            "occurrence_id": occ.id,
            "key": occ.key,
            "state": occ.state,
            "severity": occ.severity,
            "value": occ.value,
            "warning_threshold": occ.warning_threshold,
            "alarm_threshold": occ.alarm_threshold,
            "message": (message or occ.message or ""),
        }

        if transitioned and broadcast_cb:
            try:
                broadcast_cb(payload)
            except Exception:
                logger.exception("AlarmManager broadcast failed (ignored).")

        return {
            "created": created,
            "transitioned": transitioned,
            "occurrence_id": occ.id,
            "state": occ.state,
            "payload": payload,
        }

    def acknowledge(
        self,
        db: Session,
        *,
        occurrence_id: int,
        acknowledged: bool,
        user_id: Optional[int],
        client_ip: Optional[str],
        note: Optional[str] = None,
    ) -> AlarmOccurrence:
        occ = db.query(AlarmOccurrence).filter(AlarmOccurrence.id == int(occurrence_id)).one_or_none()
        if not occ:
            raise KeyError("occurrence not found")

        occ.acknowledged = bool(acknowledged)
        occ.acknowledged_at = _utcnow() if acknowledged else None
        occ.acknowledged_by_user_id = user_id if acknowledged else None
        occ.acknowledged_by_client_ip = client_ip if acknowledged else None

        if note:
            m = dict(occ.meta or {})
            m["ack_note"] = note
            occ.meta = m

        try:
            db.add(occ)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("AlarmManager.acknowledge failed (rolled back). occ_id=%s", occurrence_id)
            raise

        return occ

    def active_snapshot(self, db: Session) -> list[dict]:
        rows = (
            db.query(AlarmOccurrence)
            .filter(AlarmOccurrence.is_active == True)  # noqa: E712
            .order_by(AlarmOccurrence.last_seen_at.desc())
            .all()
        )

        out = []
        for r in rows:
            context = _alarm_context_for_datapoint(
                db,
                datapoint_id=r.datapoint_id,
                meta=(r.meta or {}),
            )
            out.append(
                {
                    "occurrence_id": r.id,
                    "source": r.source,
                    "key": r.key,
                    "datapoint_id": r.datapoint_id,
                    "datapoint_label": context.get("datapoint_label"),
                    "plc_name": context.get("plc_name"),
                    "container_name": context.get("container_name"),
                    "equipment_name": context.get("equipment_name"),
                    "rule_id": r.rule_id,
                    "external_rule_id": r.external_rule_id,
                    "state": r.state,
                    "severity": r.severity,
                    "message": r.message,
                    "value": r.value,
                    "warning_threshold": r.warning_threshold,
                    "alarm_threshold": r.alarm_threshold,
                    "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                    "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                    "acknowledged": bool(r.acknowledged),
                    "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
                }
            )
        return out
