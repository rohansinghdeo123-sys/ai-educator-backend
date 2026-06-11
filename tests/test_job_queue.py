import os
import tempfile
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ContentIngestionJob
from services.job_queue import DbJobQueue


def _wait_for_status(session_factory, job_id, statuses, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = session_factory()
        try:
            job = db.query(ContentIngestionJob).filter(ContentIngestionJob.job_id == job_id).first()
            if job and job.status in statuses:
                return job.status, dict(job.summary or {}), job.error
        finally:
            db.close()
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach {statuses} within {timeout}s")


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.queue = DbJobQueue(session_factory=self.session_factory, poll_seconds=0.1)

    def tearDown(self):
        self.queue.stop()
        self.engine.dispose()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_submit_requires_registered_handler(self):
        db = self.session_factory()
        try:
            with self.assertRaises(ValueError):
                self.queue.submit(db, job_type="unknown_type")
        finally:
            db.close()

    def test_job_executes_and_merges_result(self):
        self.queue.register("echo", lambda db, job: {"echo": (job.summary or {}).get("request")})
        self.queue.start()
        db = self.session_factory()
        try:
            job = self.queue.submit(db, job_type="echo", payload={"value": 42})
        finally:
            db.close()
        status, summary, _error = _wait_for_status(self.session_factory, job.job_id, {"completed"})
        self.assertEqual(status, "completed")
        self.assertEqual(summary["result"]["echo"], {"value": 42})

    def test_failing_handler_marks_job_failed(self):
        def boom(db, job):
            raise RuntimeError("handler exploded")

        self.queue.register("boom", boom)
        self.queue.start()
        db = self.session_factory()
        try:
            job = self.queue.submit(db, job_type="boom")
        finally:
            db.close()
        status, _summary, error = _wait_for_status(self.session_factory, job.job_id, {"failed"})
        self.assertEqual(status, "failed")
        self.assertIn("handler exploded", error)

    def test_handler_controlled_status_is_respected(self):
        def needs_review(db, job):
            job.status = "needs_review"
            db.commit()
            return None

        self.queue.register("review", needs_review)
        self.queue.start()
        db = self.session_factory()
        try:
            job = self.queue.submit(db, job_type="review")
        finally:
            db.close()
        status, _summary, _error = _wait_for_status(self.session_factory, job.job_id, {"needs_review"})
        self.assertEqual(status, "needs_review")

    def test_startup_recovers_interrupted_and_resumes_queued(self):
        self.queue.register("echo", lambda db, job: {"ok": True})
        db = self.session_factory()
        try:
            # Simulate a crash: one job stuck running, one still queued.
            stuck = ContentIngestionJob(job_id="job_stuck", job_type="echo", status="running")
            pending = ContentIngestionJob(job_id="job_pending", job_type="echo", status="queued")
            db.add_all([stuck, pending])
            db.commit()
        finally:
            db.close()

        self.queue.start()
        status, _s, error = _wait_for_status(self.session_factory, "job_stuck", {"failed"})
        self.assertIn("restart", error)
        status, _s, _e = _wait_for_status(self.session_factory, "job_pending", {"completed"})
        self.assertEqual(status, "completed")


if __name__ == "__main__":
    unittest.main()
