import datetime as dt
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sunny_scada.db.base import Base
from sunny_scada.db.models import AlarmEvent, AlarmOccurrence
from sunny_scada.services.alarm_manager import AlarmManager


class AlarmManagerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, future=True)
        self.am = AlarmManager()

    def test_transitions_create_events(self):
        with self.SessionLocal() as db:
            r1 = self.am.set_state(
                db,
                source="backend_rule",
                key="backend_rule:1",
                new_state="OK",
                severity="info",
                message="ok",
                ts=dt.datetime(2026, 2, 19, tzinfo=dt.timezone.utc),
            )
            self.assertTrue(r1["created"])
            self.assertFalse(r1["transitioned"])  # created as OK

        with self.SessionLocal() as db:
            r2 = self.am.set_state(
                db,
                source="backend_rule",
                key="backend_rule:1",
                new_state="WARNING",
                severity="minor",
                message="warn",
                ts=dt.datetime(2026, 2, 19, 0, 0, 1, tzinfo=dt.timezone.utc),
            )
            self.assertTrue(r2["transitioned"])

        with self.SessionLocal() as db:
            r3 = self.am.set_state(
                db,
                source="backend_rule",
                key="backend_rule:1",
                new_state="WARNING",
                severity="minor",
                message="warn again",
                ts=dt.datetime(2026, 2, 19, 0, 0, 2, tzinfo=dt.timezone.utc),
            )
            self.assertFalse(r3["transitioned"])  # no spam

        with self.SessionLocal() as db:
            self.assertEqual(db.query(AlarmOccurrence).count(), 1)
            # one event from OK->WARNING
            self.assertEqual(db.query(AlarmEvent).count(), 1)

    def test_acknowledge(self):
        with self.SessionLocal() as db:
            r = self.am.set_state(
                db,
                source="plc",
                key="plc:abc",
                new_state="ALARM",
                severity="critical",
                message="boom",
                ts=dt.datetime(2026, 2, 19, tzinfo=dt.timezone.utc),
            )
            occ_id = r["occurrence_id"]

        with self.SessionLocal() as db:
            occ = self.am.acknowledge(
                db,
                occurrence_id=occ_id,
                acknowledged=True,
                user_id=1,
                client_ip="127.0.0.1",
                note="seen",
            )
            self.assertTrue(occ.acknowledged)
            self.assertIn("ack_note", occ.meta)


if __name__ == "__main__":
    unittest.main()
