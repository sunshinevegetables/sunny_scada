from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, Dict, Iterator, Optional, Tuple

from sqlalchemy.orm import Session

from sunny_scada.db.models import AlarmRule, CfgContainer, CfgDataPoint, CfgEquipment
from sunny_scada.services.alarm_manager import AlarmManager
from sunny_scada.services.alarm_rules_logic import evaluate_rule, is_rule_active

logger = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iter_leaves(obj: Any, path: Tuple[str, ...] = ()) -> Iterator[Tuple[Tuple[str, ...], Dict[str, Any]]]:
    """
    Yield (path, leaf_dict) where leaf_dict contains a 'type' field.

    Your snapshot leaf example:
      'PDC_...': {'description': 'Feedback', 'type': 'DIGITAL', 'value': ...}
    """
    if isinstance(obj, dict):
        if "type" in obj:
            yield (path, obj)
            return
        for k, v in obj.items():
            if isinstance(k, str):
                yield from _iter_leaves(v, path + (k,))


class AlarmMonitor:
    """
    Evaluate alarm rules for incoming PLC datapoint updates.

    IMPORTANT:
    - AlarmManager.set_state() already commits.
    - So this monitor should NOT commit the session.
    """

    def __init__(
        self,
        *,
        sessionmaker,
        alarm_manager: AlarmManager,
        broadcaster,
    ) -> None:
        self._SessionLocal = sessionmaker
        self._am = alarm_manager
        self._broadcaster = broadcaster
        self._lock = threading.RLock()

        self._rules_by_dp: dict[int, list[AlarmRule]] = {}
        self._dp_map: dict[tuple[str, str], int] = {}

    def invalidate_cache(self) -> None:
        with self._lock:
            self._rules_by_dp.clear()
            self._dp_map.clear()

    def _resolve_datapoint_id(self, db: Session, plc_name: str, label: str) -> Optional[int]:
        """
        Minimal resolver: label -> cfg_data_points.id

        NOTE:
        If your cfg_data_points.label does NOT match snapshot leaf key (label),
        dp_id will never resolve. In that case, the best fix is to embed datapoint_id
        into the snapshot leaves in PLCReader, or resolve by address.
        """
        key = (plc_name, label)
        with self._lock:
            if key in self._dp_map:
                return self._dp_map[key]

        row = db.query(CfgDataPoint.id).filter(CfgDataPoint.label == label).first()
        dp_id = int(row[0]) if row else None

        if dp_id is not None:
            with self._lock:
                self._dp_map[key] = dp_id
        return dp_id

    def _rules_for_dp(self, db: Session, datapoint_id: int) -> list[AlarmRule]:
        with self._lock:
            cached = self._rules_by_dp.get(datapoint_id)
            if cached is not None:
                # Merge cached rules into current session to avoid DetachedInstanceError
                merged_rules = [db.merge(rule, load=False) for rule in cached]
                return merged_rules

        rules = (
            db.query(AlarmRule)
            .filter(AlarmRule.datapoint_id == datapoint_id)
            .filter(AlarmRule.enabled == True)  # noqa: E712
            .all()
        )

        with self._lock:
            self._rules_by_dp[datapoint_id] = rules

        return rules

    def process_plc_snapshot(self, all_device_data: Any) -> None:
        """
        Process polled data from PLC polling service.
        Handles new database-driven polling structure:
        [{"plc_name": "Main PLC", "data_points": {"label": {...}, ...}}, ...]
        """
        try:
            logger.debug("AlarmMonitor.process_plc_snapshot enter data_type=%s", type(all_device_data))

            if not all_device_data:
                logger.debug("AlarmMonitor snapshot empty")
                return

            # Handle new list-based structure from database-driven polling
            if isinstance(all_device_data, list):
                with self._SessionLocal() as db:
                    for plc_entry in all_device_data:
                        if not isinstance(plc_entry, dict):
                            continue
                        plc_name = plc_entry.get("plc_name")
                        data_points = plc_entry.get("data_points", {})
                        if not plc_name or not isinstance(data_points, dict):
                            continue

                        logger.debug("AlarmMonitor processing plc=%s datapoints=%s", plc_name, len(data_points))
                        self._process_device_new_format(db, plc_name=str(plc_name), data_points=data_points)
            else:
                # Fallback for old dict-based structure (sections)
                logger.debug("AlarmMonitor using legacy dict-based snapshot format")
                if not isinstance(all_device_data, dict):
                    logger.debug("AlarmMonitor snapshot is not dict or list")
                    return

                with self._SessionLocal() as db:
                    for section_name, section_data in all_device_data.items():
                        if not isinstance(section_data, dict):
                            continue
                        for plc_name, device_data in section_data.items():
                            if not isinstance(device_data, dict):
                                continue
                            logger.debug("AlarmMonitor processing legacy plc=%s", plc_name)
                            self._process_device(db, plc_name=str(plc_name), device_data=device_data)

        except Exception as e:
            logger.exception("AlarmMonitor.process_plc_snapshot failed: %s", repr(e))

    def _process_device_new_format(self, db: Session, plc_name: str, data_points: Dict[str, Any]) -> None:
        """Process data in new database-driven format."""
        now = _utcnow()

        for label, leaf in data_points.items():
            if not isinstance(leaf, dict):
                continue

            # Extract value from the polled result
            raw_val = leaf.get("scaled_value") or leaf.get("value")
            if raw_val is None:
                continue

            try:
                value = float(raw_val)
            except Exception:
                continue

            logger.debug("AlarmMonitor numeric leaf plc=%s label=%s value=%s", plc_name, label, value)

            # Resolve datapoint ID from label
            dp_id: Optional[int] = leaf.get("id")
            if not dp_id:
                dp_id = self._resolve_datapoint_id(db, plc_name, label)

            logger.debug("AlarmMonitor resolved dp_id=%s plc=%s label=%s", dp_id, plc_name, label)

            if not dp_id:
                continue

            rules = self._rules_for_dp(db, dp_id)
            if not rules:
                continue

            for rule in rules:
                if not is_rule_active(
                    now_utc=now,
                    schedule_enabled=bool(rule.schedule_enabled),
                    start=rule.schedule_start_time,
                    end=rule.schedule_end_time,
                    timezone=rule.schedule_timezone,
                ):
                    evaluated_state = "OK"
                    msg = f"Rule {rule.name} inactive (schedule) -> OK"
                else:
                    ev = evaluate_rule(
                        comparison=rule.comparison,
                        value=value,
                        warning_enabled=bool(rule.warning_enabled),
                        warning_threshold=rule.warning_threshold,
                        alarm_threshold=rule.alarm_threshold,
                        warning_low=rule.warning_threshold_low,
                        warning_high=rule.warning_threshold_high,
                        alarm_low=rule.alarm_threshold_low,
                        alarm_high=rule.alarm_threshold_high,
                        name=rule.name,
                    )
                    evaluated_state = ev.state
                    msg = ev.message

                logger.debug("AlarmMonitor emit rule_id=%s state=%s value=%s", rule.id, evaluated_state, value)

                self._emit(
                    db=db,
                    rule=rule,
                    datapoint_id=dp_id,
                    plc_name=plc_name,
                    label=label,
                    value=value,
                    now=now,
                    evaluated_state=evaluated_state,
                    message=msg,
                )

    def _process_device(self, db: Session, plc_name: str, device_data: Dict[str, Any]) -> None:
        now = _utcnow()

        # Count leaves (for debugging)
        leaf_count = 0
        for _p, _leaf in _iter_leaves(device_data):
            leaf_count += 1
        logger.debug("AlarmMonitor _process_device plc=%s leaves=%s", plc_name, leaf_count)

        # Show first few leaves so we understand structure + keys
        shown = 0
        for path, leaf in _iter_leaves(device_data):
            if shown < 5:
                logger.debug("AlarmMonitor leaf path=%s type=%s keys=%s", path, leaf.get("type"), list(leaf.keys())[:10])
                shown += 1
            else:
                break

        # Real processing pass
        for path, leaf in _iter_leaves(device_data):
            label = path[-1] if path else ""
            if not label:
                continue

            typ = str(leaf.get("type") or "").upper()

            # Only numeric rules for now
            raw_val = None
            if typ == "REAL":
                # REAL can come as scaled_value OR value depending on reader
                raw_val = leaf.get("scaled_value")
                if raw_val is None:
                    raw_val = leaf.get("value")
            elif typ == "INTEGER":
                raw_val = leaf.get("value")
            else:
                # DIGITAL and others skipped (your rules are threshold-based numeric)
                continue

            if raw_val is None:
                continue

            try:
                value = float(raw_val)
            except Exception:
                continue

            logger.debug("AlarmMonitor numeric leaf plc=%s label=%s type=%s value=%s", plc_name, label, typ, value)

            # Best-effort dp_id embedded in leaf first
            dp_id: Optional[int] = None
            for k in ("datapoint_id", "data_point_id", "id"):
                if k in leaf and leaf.get(k) is not None:
                    try:
                        dp_id = int(leaf.get(k))
                        break
                    except Exception:
                        pass

            if dp_id is None:
                dp_id = self._resolve_datapoint_id(db, plc_name, label)

            logger.debug("AlarmMonitor resolved dp_id=%s plc=%s label=%s", dp_id, plc_name, label)

            if not dp_id:
                continue

            rules = self._rules_for_dp(db, dp_id)
            logger.debug("AlarmMonitor rules count dp_id=%s count=%s", dp_id, len(rules))

            if not rules:
                continue

            for rule in rules:
                if not is_rule_active(
                    now_utc=now,
                    schedule_enabled=bool(rule.schedule_enabled),
                    start=rule.schedule_start_time,
                    end=rule.schedule_end_time,
                    timezone=rule.schedule_timezone,
                ):
                    evaluated_state = "OK"
                    msg = f"Rule {rule.name} inactive (schedule) -> OK"
                else:
                    ev = evaluate_rule(
                        comparison=rule.comparison,
                        value=value,
                        warning_enabled=bool(rule.warning_enabled),
                        warning_threshold=rule.warning_threshold,
                        alarm_threshold=rule.alarm_threshold,
                        warning_low=rule.warning_threshold_low,
                        warning_high=rule.warning_threshold_high,
                        alarm_low=rule.alarm_threshold_low,
                        alarm_high=rule.alarm_threshold_high,
                        name=rule.name,
                    )
                    evaluated_state = ev.state
                    msg = ev.message

                logger.debug("AlarmMonitor emit rule_id=%s state=%s value=%s", rule.id, evaluated_state, value)

                self._emit(
                    db=db,
                    rule=rule,
                    datapoint_id=dp_id,
                    plc_name=plc_name,
                    label=label,
                    value=value,
                    now=now,
                    evaluated_state=evaluated_state,
                    message=msg,
                )

    def _emit(
        self,
        db: Session,
        *,
        rule: AlarmRule,
        datapoint_id: int,
        plc_name: str,
        label: str,
        value: float,
        now: dt.datetime,
        evaluated_state: str,
        message: str,
    ) -> None:
        container_name: Optional[str] = None
        equipment_name: Optional[str] = None

        dp = db.query(CfgDataPoint).filter(CfgDataPoint.id == int(datapoint_id)).one_or_none()
        if dp:
            owner_type = (dp.owner_type or "").strip().lower()
            owner_id = int(dp.owner_id)

            if owner_type == "container":
                container = db.query(CfgContainer).filter(CfgContainer.id == owner_id).one_or_none()
                if container:
                    container_name = container.name
            elif owner_type == "equipment":
                equipment = db.query(CfgEquipment).filter(CfgEquipment.id == owner_id).one_or_none()
                if equipment:
                    equipment_name = equipment.name
                    container = db.query(CfgContainer).filter(CfgContainer.id == int(equipment.container_id)).one_or_none()
                    if container:
                        container_name = container.name

        src = "frontend_rule" if (rule.rule_source or "backend") == "frontend" else "backend_rule"
        key = f"{src}:{rule.external_rule_id or rule.id}"

        logger.debug("AlarmMonitor calling AlarmManager.set_state key=%s state=%s", key, evaluated_state)

        self._am.set_state(
            db,
            source=src,
            key=key,
            new_state=evaluated_state,
            severity=rule.severity,
            message=message,
            ts=now,
            datapoint_id=datapoint_id,
            rule_id=rule.id,
            external_rule_id=rule.external_rule_id,
            value=value,
            warning_threshold=rule.warning_threshold,
            alarm_threshold=rule.alarm_threshold,
            meta={
                "plc": plc_name,
                "container": container_name,
                "equipment": equipment_name,
                "label": label,
                "comparison": rule.comparison,
            },
            broadcast_cb=self._broadcaster.broadcast if self._broadcaster else None,
        )
