import datetime as dt
import unittest

from sunny_scada.services.alarm_rules_logic import evaluate_rule, is_rule_active, validate_alarm_rule


class TestAlarmRulesLogic(unittest.TestCase):
    def test_above_warning_and_alarm(self):
        rule = {
            "enabled": True,
            "comparison": "above",
            "warning_enabled": True,
            "warning_threshold": 75.0,
            "alarm_threshold": 80.0,
            "schedule_enabled": False,
            "schedule_timezone": "UTC",
        }
        validate_alarm_rule(rule)
        self.assertEqual(evaluate_rule(rule, 74.9), "OK")
        self.assertEqual(evaluate_rule(rule, 75.0), "WARNING")
        self.assertEqual(evaluate_rule(rule, 80.0), "ALARM")

    def test_below_warning_and_alarm(self):
        rule = {
            "enabled": True,
            "comparison": "below",
            "warning_enabled": True,
            "warning_threshold": 10.0,
            "alarm_threshold": 5.0,
            "schedule_enabled": False,
            "schedule_timezone": "UTC",
        }
        validate_alarm_rule(rule)
        self.assertEqual(evaluate_rule(rule, 11.0), "OK")
        self.assertEqual(evaluate_rule(rule, 10.0), "WARNING")
        self.assertEqual(evaluate_rule(rule, 5.0), "ALARM")

    def test_schedule_cross_midnight(self):
        start = dt.time(22, 0)
        end = dt.time(6, 0)
        now1 = dt.datetime(2026, 1, 1, 23, 0, tzinfo=dt.timezone.utc)
        now2 = dt.datetime(2026, 1, 2, 5, 59, tzinfo=dt.timezone.utc)
        now3 = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.timezone.utc)

        self.assertTrue(is_rule_active(now1, schedule_enabled=True, start=start, end=end, tz="UTC"))
        self.assertTrue(is_rule_active(now2, schedule_enabled=True, start=start, end=end, tz="UTC"))
        self.assertFalse(is_rule_active(now3, schedule_enabled=True, start=start, end=end, tz="UTC"))


if __name__ == "__main__":
    unittest.main()
