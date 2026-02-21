from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from sunny_scada.db.models import (
    Alarm,
    AlarmEvent,
    AlarmOccurrence,
    AuditLog,
    Command,
    CommandEvent,
    ConfigRevision,
    HistorianHourlyRollup,
    HistorianSample,
    ServerLog,
)

logger = logging.getLogger(__name__)


def _cutoff(days: int) -> dt.datetime:
    days = int(days)
    if days <= 0:
        # 0 means keep forever
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


class RetentionService:
    def cleanup(
        self,
        db: Session,
        *,
        server_logs_days: int,
        audit_logs_days: int,
        commands_days: int,
        alarms_days: int,
        historian_raw_days: int,
        historian_rollup_days: int,
    ) -> dict:
        """Apply retention policy. Returns counts deleted per table."""
        summary: dict = {}

        # Server logs
        c = _cutoff(server_logs_days)
        q = db.query(ServerLog).filter(ServerLog.ts < c)
        summary["server_logs"] = q.delete(synchronize_session=False)

        # Audit logs
        c = _cutoff(audit_logs_days)
        q = db.query(AuditLog).filter(AuditLog.ts < c)
        summary["audit_logs"] = q.delete(synchronize_session=False)

        # Config revisions (tie retention to audit logs days)
        c = _cutoff(audit_logs_days)
        q = db.query(ConfigRevision).filter(ConfigRevision.ts < c)
        summary["config_revisions"] = q.delete(synchronize_session=False)

        # Commands + events
        c = _cutoff(commands_days)
        # delete events first
        cmd_ids = [r[0] for r in db.query(Command.id).filter(Command.created_at < c).all()]
        if cmd_ids:
            summary["command_events"] = db.query(CommandEvent).filter(CommandEvent.command_row_id.in_(cmd_ids)).delete(
                synchronize_session=False
            )
        else:
            summary["command_events"] = 0
        summary["commands"] = db.query(Command).filter(Command.created_at < c).delete(synchronize_session=False)

        # Alarms
        c = _cutoff(alarms_days)
        summary["alarms"] = db.query(Alarm).filter(Alarm.ts < c).delete(synchronize_session=False)
        summary["alarm_events"] = db.query(AlarmEvent).filter(AlarmEvent.ts < c).delete(synchronize_session=False)
        # occurrences are derived from events; keep longer if you want, but clean old inactive ones
        summary["alarm_occurrences"] = (
            db.query(AlarmOccurrence)
            .filter(AlarmOccurrence.is_active == False)  # noqa: E712
            .filter(AlarmOccurrence.last_seen_at < c)
            .delete(synchronize_session=False)
        )

        # Historian raw samples
        c = _cutoff(historian_raw_days)
        summary["historian_samples"] = db.query(HistorianSample).filter(HistorianSample.ts < c).delete(
            synchronize_session=False
        )

        # Historian rollups
        c = _cutoff(historian_rollup_days)
        summary["historian_hourly_rollups"] = db.query(HistorianHourlyRollup).filter(
            HistorianHourlyRollup.bucket_start < c
        ).delete(synchronize_session=False)

        db.commit()
        return summary
