# -*- coding: utf-8 -*-
import os, sys, unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from database import Base, User, QcSlice, AnalysisBatch, ReviewAssignment, AuditLog, utcnow
from auth import account_blocked, require_admin, hash_password, DISABLE_GRACE, PASSWORD_CHANGE_ALLOWLIST
from routers import auth as auth_router


def _memory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(db, username, role="admin", **kwargs):
    row = User(
        username=username, email=f"{username}@t.com", hashed_pwd=hash_password("secret1"),
        role=role, is_active=True, **kwargs,
    )
    db.add(row); db.flush()
    return row


class UserAdminApiTest(unittest.TestCase):
    def setUp(self):
        self.db = _memory()
        self.admin = _user(self.db, "boss", "admin")
        self.analyst = _user(self.db, "alice", "analyst")
        self.other_admin = _user(self.db, "carol", "admin")

    def test_non_admin_cannot_mutate(self):
        with self.assertRaises(HTTPException) as ctx:
            require_admin(current_user=self.analyst, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_can_edit_other_admin_and_analyst(self):
        updated = auth_router.update_user(self.other_admin.id, auth_router.UserUpdate(username="carol2"), self.admin, self.db)
        self.assertEqual(updated["username"], "carol2")
        updated = auth_router.update_user(self.analyst.id, auth_router.UserUpdate(email="alice2@t.com"), self.admin, self.db)
        self.assertEqual(updated["email"], "alice2@t.com")

    def test_can_edit_self_username_and_role_revokes_admin_immediately(self):
        auth_router.update_user(self.admin.id, auth_router.UserUpdate(username="boss2"), self.admin, self.db)
        self.assertEqual(self.db.get(User, self.admin.id).username, "boss2")
        payload = auth_router.update_user(self.admin.id, auth_router.UserUpdate(role="analyst"), self.admin, self.db)
        self.assertTrue(payload["relogin_required"])
        with self.assertRaises(HTTPException) as ctx:
            require_admin(current_user=self.db.get(User, self.admin.id), db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_username_conflict_is_409(self):
        with self.assertRaises(HTTPException) as ctx:
            auth_router.update_user(self.analyst.id, auth_router.UserUpdate(username="boss"), self.admin, self.db)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("123", str(ctx.exception.detail))
        self.assertEqual(self.db.get(User, self.analyst.id).username, "alice")

    def test_role_change_releases_open_assignments_only(self):
        batch = AnalysisBatch(name="b", created_by=self.admin.id, source_task_id="upload", source_region="unknown")
        self.db.add(batch); self.db.flush()
        open_row = ReviewAssignment(batch_id=batch.id, item_type="quality_issue", item_id=1, assignee_id=self.analyst.id, assigned_by=self.admin.id, status="in_progress")
        done_row = ReviewAssignment(batch_id=batch.id, item_type="quality_issue", item_id=2, assignee_id=self.analyst.id, assigned_by=self.admin.id, status="completed")
        cancelled = ReviewAssignment(batch_id=batch.id, item_type="quality_issue", item_id=3, assignee_id=self.analyst.id, assigned_by=self.admin.id, status="cancelled")
        self.db.add_all([open_row, done_row, cancelled]); self.db.flush()
        payload = auth_router.update_user(self.analyst.id, auth_router.UserUpdate(role="admin"), self.admin, self.db)
        self.assertEqual(payload["released_assignments"], 1)
        self.assertEqual(self.db.get(ReviewAssignment, open_row.id).status, "cancelled")
        self.assertEqual(self.db.get(ReviewAssignment, done_row.id).status, "completed")
        self.assertEqual(self.db.get(ReviewAssignment, cancelled.id).status, "cancelled")

    def test_disable_grace_then_block_and_cancel(self):
        now = utcnow()
        payload = auth_router.toggle_user(self.analyst.id, self.admin, self.db)
        self.assertEqual(payload["status"], "pending_disable")
        user = self.db.get(User, self.analyst.id)
        self.assertFalse(account_blocked(user, now))
        self.assertTrue(account_blocked(user, now + DISABLE_GRACE + timedelta(seconds=1)))
        payload = auth_router.toggle_user(self.analyst.id, self.admin, self.db)
        self.assertEqual(payload["status"], "active")
        user = self.db.get(User, self.analyst.id)
        self.assertFalse(account_blocked(user, now + DISABLE_GRACE + timedelta(minutes=10)))

    def test_reset_password_requires_change_before_business(self):
        auth_router.reset_password(self.analyst.id, {"new_password": "abcdef"}, self.admin, self.db)
        user = self.db.get(User, self.analyst.id)
        self.assertTrue(user.must_change_password)
        self.assertNotIn("/api/analysis/batches", PASSWORD_CHANGE_ALLOWLIST)
        auth_router.change_password({"old_password": "abcdef", "new_password": "ghijkl"}, user, self.db)
        self.assertFalse(self.db.get(User, self.analyst.id).must_change_password)

    def test_audit_has_no_password(self):
        from audit import sanitize_audit_text
        auth_router.reset_password(self.analyst.id, {"new_password": "abcdef"}, self.admin, self.db)
        rows = self.db.query(AuditLog).all()
        self.assertTrue(any(row.action == "password_reset" for row in rows))
        blob = " ".join(str(getattr(row, field) or "") for row in rows for field in ("reason", "from_value", "to_value"))
        self.assertNotIn("abcdef", blob)
        self.assertEqual(sanitize_audit_text("password=abcdef"), "[已隐藏]")

    def test_can_delete_last_admin_and_keep_history(self):
        batch = AnalysisBatch(name="keep", created_by=self.admin.id, source_task_id="upload", source_region="unknown")
        self.db.add(batch); self.db.flush()
        slice_row = QcSlice(batch_id=batch.id, slice_id="hist", messages_json="[]", analysis_status="completed")
        self.db.add(slice_row); self.db.flush()
        open_row = ReviewAssignment(batch_id=batch.id, item_type="quality_issue", item_id=9, assignee_id=self.admin.id, assigned_by=self.admin.id, status="pending")
        self.db.add(open_row); self.db.flush()
        self.db.delete(self.other_admin); self.db.flush()
        result = auth_router.delete_user(self.admin.id, {"reason": "离职"}, self.admin, self.db)
        self.assertTrue(result["self"])
        self.assertIsNone(self.db.get(User, self.admin.id))
        self.assertEqual(self.db.get(QcSlice, slice_row.id).analysis_status, "completed")
        self.assertEqual(self.db.get(AnalysisBatch, batch.id).name, "keep")
        logs = self.db.query(AuditLog).filter(AuditLog.action == "deleted").all()
        self.assertTrue(logs)

    def test_delete_self_cannot_keep_admin_access(self):
        result = auth_router.delete_user(self.admin.id, {}, self.admin, self.db)
        self.assertTrue(result["self"])
        self.assertIsNone(self.db.query(User).filter(User.id == self.admin.id).first())


if __name__ == "__main__":
    unittest.main()
