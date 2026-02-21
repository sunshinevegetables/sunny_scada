import datetime as dt
import unittest

from sunny_scada.services.alarm_rules_logic import evaluate_rule, is_rule_active


class AlarmRulesLogicTests(unittest.TestCase):
    def test_schedule_cross_midnight(self):
        start = dt.time(22, 0)
        end = dt.time(6, 0)

        # 23:00 IST should be active
        now = dt.datetime(2026, 2, 19, 17, 30, tzinfo=dt.timezone.utc)  # 23:00 Asia/Kolkata
        self.assertTrue(
            is_rule_active(
                now_utc=now,
                schedule_enabled=True,
                start=start,
                end=end,
                timezone="Asia/Kolkata",
            )
        )

        # 12:00 IST should be inactive
        now2 = dt.datetime(2026, 2, 19, 6, 30, tzinfo=dt.timezone.utc)  # 12:00 IST
        self.assertFalse(
            is_rule_active(
                now_utc=now2,
                schedule_enabled=True,
                start=start,
                end=end,
                timezone="Asia/Kolkata",
            )
        )

    def test_above_thresholds(self):
        ev = evaluate_rule(
            comparison="above",
            value=79,
            warning_enabled=True,
            warning_threshold=75,
            alarm_threshold=80,
            warning_low=None,
            warning_high=None,
            alarm_low=None,
            alarm_high=None,
            name="temp",
        )
        self.assertEqual(ev.state, "WARNING")

        ev2 = evaluate_rule(
            comparison="above",
            value=81,
            warning_enabled=True,
            warning_threshold=75,
            alarm_threshold=80,
            warning_low=None,
            warning_high=None,
            alarm_low=None,
            alarm_high=None,
            name="temp",
        )
        self.assertEqual(ev2.state, "ALARM")


if __name__ == "__main__":
    unittest.main()
