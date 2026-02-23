from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from sunny_scada.db.models import Instrument, InstrumentCalibration, Schedule, WorkOrder


def test_maintenance_scheduler_calibration_due_creates_work_order(client: TestClient):
    SessionLocal = client.app.state.db_sessionmaker
    scheduler = client.app.state.maintenance_scheduler
    now = dt.datetime.now(dt.timezone.utc)

    with SessionLocal() as db:
        instrument = Instrument(label="CAL-1", status="active")
        db.add(instrument)
        db.flush()

        calibration = InstrumentCalibration(
            instrument_id=int(instrument.id),
            ts=now - dt.timedelta(days=30),
            next_due_at=now - dt.timedelta(minutes=1),
        )
        schedule = Schedule(
            name="Calibration Schedule",
            enabled=True,
            instrument_id=int(instrument.id),
            meta={"schedule_type": "calibration"},
        )
        db.add_all([calibration, schedule])
        db.commit()

        created = scheduler.tick(db)
        assert created == 1

        wo = db.query(WorkOrder).filter(WorkOrder.schedule_id == int(schedule.id)).one()
        assert int(wo.instrument_id) == int(instrument.id)
        assert wo.due_at is not None

        created_again = scheduler.tick(db)
        assert created_again == 0
