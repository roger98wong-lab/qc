import os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.chdir(ROOT / 'backend')

from database import AnalysisBatch, QcSlice, SessionLocal, User, init_db, sqlite_error_kind, sqlite_user_message
from routers import analysis as analysis_router


class BatchControlSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _make_batch(self, status='uploading'):
        user = self.db.query(User).first()
        if not user:
            user = User(username='tester', email='t@example.com', hashed_pwd='x', role='admin')
            self.db.add(user)
            self.db.flush()
        batch = AnalysisBatch(name='ctrl-test', created_by=user.id, status=status, source_task_id='upload', source_region='unknown')
        self.db.add(batch)
        self.db.flush()
        return batch

    def test_counts_ignore_unparsed_files_and_only_count_slices(self):
        batch = self._make_batch()
        self.db.add(QcSlice(batch_id=batch.id, slice_id='a', messages_json='[]', analysis_status='pending'))
        self.db.add(QcSlice(batch_id=batch.id, slice_id='b', messages_json='[]', analysis_status='completed'))
        self.db.add(QcSlice(batch_id=batch.id, slice_id='c', messages_json='[]', analysis_status='failed'))
        self.db.add(QcSlice(batch_id=batch.id, slice_id='d', messages_json='[]', analysis_status='processing'))
        self.db.flush()
        counts = analysis_router._slice_counts(self.db, batch.id)
        self.assertEqual(counts['processed_count'], 2)
        self.assertEqual(counts['pending_count'], 2)
        self.assertEqual(counts['runnable_count'], 2)

    def test_terminal_status_helper(self):
        self.assertTrue(analysis_router._is_terminal_status('completed'))
        self.assertTrue(analysis_router._is_terminal_status('partial'))
        self.assertFalse(analysis_router._is_terminal_status('paused'))
        self.assertFalse(analysis_router._is_terminal_status('analyzing'))

    def test_abort_requested_blocks_uploadable_status(self):
        batch = self._make_batch('analyzing')
        batch.control_reason = analysis_router.ABORT_REASON
        self.assertTrue(analysis_router._is_abort_requested(batch))

    def test_progress_payload_does_not_fall_back_when_processed_is_zero_or_lower(self):
        batch = self._make_batch()
        batch.analyzed_slices = 173
        payload = analysis_router._progress_payload(batch, {
            'processed_count': 159,
            'pending_count': 227,
        })
        self.assertEqual(payload['processed_count'], 159)
        self.assertEqual(payload['pending_count'], 227)
        zero = analysis_router._progress_payload(batch, {
            'processed_count': 0,
            'pending_count': 15,
        })
        self.assertEqual(zero['processed_count'], 0)
        self.assertEqual(zero['pending_count'], 15)

    def test_sqlite_error_kind_classifies_lock_and_corruption(self):
        self.assertEqual(sqlite_error_kind(Exception("database is locked")), "locked")
        self.assertEqual(sqlite_error_kind(Exception("database disk image is malformed")), "malformed")
        self.assertEqual(sqlite_error_kind(Exception("too many SQL variables")), "too_many_vars")
        self.assertIn("占用", sqlite_user_message(Exception("database is locked"), "删除批次"))
        self.assertIn("损坏", sqlite_user_message(Exception("database disk image is malformed"), "删除批次"))
        self.assertIn("数据量过大", sqlite_user_message(Exception("too many SQL variables"), "删除批次"))

    def test_reclaiming_failed_slices_moves_them_from_processed_to_pending(self):
        batch = self._make_batch('analyzing')
        self.db.add(QcSlice(batch_id=batch.id, slice_id='done', messages_json='[]', analysis_status='completed'))
        failed = QcSlice(batch_id=batch.id, slice_id='fail', messages_json='[]', analysis_status='failed')
        self.db.add(failed)
        self.db.flush()
        before = analysis_router._slice_counts(self.db, batch.id)
        self.assertEqual(before['processed_count'], 2)
        self.assertEqual(before['pending_count'], 0)
        failed.analysis_status = 'processing'
        self.db.flush()
        after = analysis_router._slice_counts(self.db, batch.id)
        self.assertEqual(after['processed_count'], 1)
        self.assertEqual(after['pending_count'], 1)


    def test_delete_batch_rows_uses_subqueries(self):
        import inspect
        source = inspect.getsource(analysis_router._delete_batch_rows)
        self.assertIn("WHERE batch_id = :bid", source)
        self.assertNotIn("slice_ids", source)

    def test_slice_counts_from_status_map_treats_skipped_as_processed(self):
        counts = analysis_router._slice_counts_from_status_map({
            "completed": 10, "skipped": 3, "pending": 4, "processing": 1, "failed": 2,
        })
        self.assertEqual(counts["processed_count"], 15)
        self.assertEqual(counts["pending_count"], 5)
        self.assertEqual(counts["failed_count"], 2)
        self.assertEqual(counts["runnable_count"], 6)

    def test_list_batches_source_does_not_reconcile(self):
        import inspect
        source = inspect.getsource(analysis_router.batches)
        self.assertNotIn("reconcile_stale_batches(db)", source)

    def test_progress_and_pause_do_not_reconcile(self):
        import inspect
        self.assertNotIn("reconcile_stale_batches", inspect.getsource(analysis_router.progress))
        self.assertNotIn("reconcile_stale_batches", inspect.getsource(analysis_router.pause_batch))
        self.assertNotIn("reconcile_stale_batches", inspect.getsource(analysis_router.abort_batch))
        self.assertIn("reconcile_stale_batches(db, batch_id)", inspect.getsource(analysis_router.start_batch))
        self.assertIn("reconcile_stale_batches(db, batch_id)", inspect.getsource(analysis_router.resume_batch))

if __name__ == '__main__':
    unittest.main()
