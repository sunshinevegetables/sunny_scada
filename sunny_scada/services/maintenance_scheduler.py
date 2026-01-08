from __future__ import annotations

import datetime as dt
import logging

from croniter import croniter
from sqlalchemy.orm import Session

from sunny_scada.db.models import Schedule, TaskTemplate, WorkOrder

logger = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class MaintenanceScheduler:
    def tick(self, db: Session) -> int:
        now = _utcnow()
        schedules = (
            db.query(Schedule)
            .filter(Schedule.enabled == True)  # noqa: E712
            .all()
        )
        created = 0
        for sch in schedules:
            due = sch.next_run_at is None or sch.next_run_at <= now
            if not due:
                continue

            title = sch.name
            if sch.task_template_id:
                tt = db.query(TaskTemplate).filter(TaskTemplate.id == sch.task_template_id).one_or_none()
                if tt:
                    title = tt.name

            wo = WorkOrder(
                equipment_id=sch.equipment_id,
                schedule_id=sch.id,
                task_template_id=sch.task_template_id,
                title=title,
                description=None,
                status="open",
                priority="normal",
                meta={"schedule": sch.id},
            )
            db.add(wo)
            db.flush()
            wo.work_order_code = f"WO-{wo.id:06d}"
            db.add(wo)

            sch.next_run_at = self._next_run(sch, now)
            db.add(sch)
            created += 1

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
