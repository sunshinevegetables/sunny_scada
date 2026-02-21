from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sunny_scada.db.base import Base
from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgEquipment, CfgPLC
from sunny_scada.services.alarm_manager import AlarmManager
from sunny_scada.services.alarm_monitor import AlarmMonitor


class _Broadcaster:
    def broadcast(self, *args, **kwargs):
        return None


def test_alarm_monitor_scoped_resolution_with_duplicate_labels():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        plc = CfgPLC(name="PLC-A", ip="127.0.0.1", port=502)
        db.add(plc)
        db.flush()

        c1 = CfgContainer(plc_id=int(plc.id), name="C1", type="container")
        db.add(c1)
        db.flush()

        e1 = CfgEquipment(container_id=int(c1.id), name="E1", type="equipment")
        db.add(e1)
        db.flush()

        dp_container = CfgDataPoint(
            owner_type="container",
            owner_id=int(c1.id),
            label="P_SHARED",
            category="read",
            type="REAL",
            address="41000",
        )
        dp_equipment = CfgDataPoint(
            owner_type="equipment",
            owner_id=int(e1.id),
            label="P_SHARED",
            category="read",
            type="REAL",
            address="41001",
        )
        db.add(dp_container)
        db.add(dp_equipment)
        db.commit()
        db.refresh(dp_container)
        db.refresh(dp_equipment)

        mon = AlarmMonitor(sessionmaker=SessionLocal, alarm_manager=AlarmManager(), broadcaster=_Broadcaster())

        got1 = mon._resolve_datapoint_id(
            db,
            plc_name="PLC-A",
            leaf_key="P_SHARED",
            leaf={"owner_type": "container", "owner_id": int(c1.id), "label": "P_SHARED"},
        )
        got2 = mon._resolve_datapoint_id(
            db,
            plc_name="PLC-A",
            leaf_key="P_SHARED",
            leaf={"owner_type": "equipment", "owner_id": int(e1.id), "label": "P_SHARED"},
        )

        assert int(got1) == int(dp_container.id)
        assert int(got2) == int(dp_equipment.id)

        got3 = mon._resolve_datapoint_id(
            db,
            plc_name="PLC-A",
            leaf_key="P_SHARED",
            leaf={"label": "P_SHARED"},
        )
        assert got3 is None
