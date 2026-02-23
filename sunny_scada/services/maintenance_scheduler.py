from __future__ import annotations

import datetime as dt
import logging

from croniter import croniter
from sqlalchemy.orm import Session

from sunny_scada.db.models import InstrumentCalibration, Schedule, TaskTemplate, WorkOrder

logger = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class MaintenanceScheduler:
    def tick(self, db: Session, *, dry_run: bool = False) -> int:
        now = _utcnow()
        schedules = (
            db.query(Schedule)
            .filter(Schedule.enabled == True)  # noqa: E712
            .all()
        )
        created = 0
        for sch in schedules:
            schedule_type = self._schedule_type(sch)
            if sch.instrument_id and schedule_type == "calibration":
                due_at = self._calibration_due_at(db, int(sch.instrument_id))
                if due_at is None:
                    logger.debug("Calibration schedule %s has no next due date", sch.id)
                    continue

                if sch.next_run_at != due_at:
                    sch.next_run_at = due_at
                    db.add(sch)

                if due_at > now:
                    continue

                if self._has_open_work_order(db, sch.id):
                    logger.info("Skipping schedule %s: open work order exists", sch.id)
                    continue
            else:
                due = sch.next_run_at is None or sch.next_run_at <= now
                if not due:
                    continue

                if self._has_open_work_order(db, sch.id):
                    logger.info("Skipping schedule %s: open work order exists", sch.id)
                    continue

            title = sch.name
            if sch.task_template_id:
                tt = db.query(TaskTemplate).filter(TaskTemplate.id == sch.task_template_id).one_or_none()
                if tt:
                    title = tt.name

            wo = WorkOrder(
                equipment_id=sch.equipment_id,
                instrument_id=sch.instrument_id,
                schedule_id=sch.id,
                task_template_id=sch.task_template_id,
                title=title,
                description=None,
                status="open",
                priority="normal",
                due_at=(sch.next_run_at if schedule_type == "calibration" else None),
                meta={"schedule": sch.id, "schedule_type": schedule_type},
            )
            if not dry_run:
                db.add(wo)
                db.flush()
                wo.work_order_code = f"WO-{wo.id:06d}"
                db.add(wo)

                if schedule_type != "calibration":
                    sch.next_run_at = self._next_run(sch, now)
                    db.add(sch)
                created += 1
                logger.info("Created work order %s for schedule %s", wo.work_order_code, sch.id)
            else:
                created += 1
                logger.info("Dry-run: would create work order for schedule %s", sch.id)

        if not dry_run:
            db.commit()
        return created

    def _next_run(self, sch: Schedule, now: dt.datetime) -> dt.datetime:
        now = now.astimezone(dt.timezone.utc)
        if sch.interval_minutes and int(sch.interval_minutes) > 0:
            return now + dt.timedelta(minutes=int(sch.interval_minutes))
        if sch.cron:
            it = croniter(str(sch.cron), now)
            nxt = it.get_next(dt.datetime)
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=dt.timezone.utc)
            return nxt.astimezone(dt.timezone.utc)
        # default: in 24h
        return now + dt.timedelta(days=1)

    def _has_open_work_order(self, db: Session, schedule_id: int) -> bool:
        return (
            db.query(WorkOrder.id)
            .filter(WorkOrder.schedule_id == int(schedule_id))
            .filter(WorkOrder.status.in_(["open", "in_progress"]))
            .first()
            is not None
        )

    def _schedule_type(self, sch: Schedule) -> str | None:
        meta = sch.meta or {}
        if not isinstance(meta, dict):
            return None
        raw = meta.get("schedule_type") or meta.get("type") or meta.get("kind")
        if raw is None:
            return None
        value = str(raw).strip()
        return value or None

    def _calibration_due_at(self, db: Session, instrument_id: int) -> dt.datetime | None:
        row = (
            db.query(InstrumentCalibration)
            .filter(InstrumentCalibration.instrument_id == int(instrument_id))
            .filter(InstrumentCalibration.next_due_at.isnot(None))
            .order_by(InstrumentCalibration.next_due_at.desc(), InstrumentCalibration.id.desc())
            .first()
        )
        if row is None:
            return None
        return row.next_due_at
