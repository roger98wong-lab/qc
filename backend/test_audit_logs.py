import os, sys, unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from audit import (
    AuditQueryError, action_label, resolve_audit_window, sanitize_audit_text,
    serialize_audit_log, target_label,
)


class AuditLogHelperTest(unittest.TestCase):
    def test_default_window_is_30_days(self):
        now = datetime(2026, 9, 21, 15, 30, 0)
        start, end = resolve_audit_window(None, None, now)
        self.assertEqual(end, now)
        self.assertEqual(start, now - timedelta(days=30))

    def test_reject_start_31_days_ago(self):
        now = datetime(2026, 9, 21, 15, 30, 0)
        with self.assertRaises(AuditQueryError):
            resolve_audit_window(now - timedelta(days=31), now, now)

    def test_reject_range_longer_than_30_days(self):
        now = datetime(2026, 9, 21, 15, 30, 0)
        with self.assertRaises(AuditQueryError):
            resolve_audit_window(now - timedelta(days=30, seconds=1), now, now)

    def test_labels_and_unknown_passthrough(self):
        self.assertEqual(action_label("login_failed"), "登录失败")
        self.assertEqual(action_label("force_released"), "强制释放")
        self.assertEqual(action_label("custom_action"), "custom_action")
        self.assertEqual(target_label("user", "3"), "用户 3")
        self.assertEqual(target_label("review_assignment", "12"), "审核任务 12")
        self.assertEqual(target_label("mystery", "9"), "mystery 9")

    def test_sanitize_hides_secrets_and_keeps_empty(self):
        self.assertIsNone(sanitize_audit_text(None))
        self.assertIsNone(sanitize_audit_text("  "))
        self.assertEqual(sanitize_audit_text("Bearer abc.def"), "[已隐藏]")
        self.assertEqual(sanitize_audit_text("password=123"), "[已隐藏]")
        self.assertEqual(sanitize_audit_text("unassigned"), "unassigned")

    def test_serialize_system_operator(self):
        row = SimpleNamespace(
            id=1, created_at=datetime(2026, 9, 21, 12, 0, 0), operator_id=None,
            operator_role=None, action="login_failed", target_type="auth",
            target_id=None, from_value=None, to_value=None, reason="invalid_credentials",
        )
        item = serialize_audit_log(row)
        self.assertEqual(item["operator_name"], "系统/未知")
        self.assertEqual(item["action_label"], "登录失败")
        self.assertEqual(item["target_label"], "登录")
        self.assertEqual(item["from_value"], None)
        self.assertEqual(item["reason"], "invalid_credentials")
        self.assertEqual(item["created_at"], "2026-09-21 12:00:00")


if __name__ == "__main__":
    unittest.main()
