import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer

from fileflow.apply_controller import ApplyController, ApplyState
from fileflow.preview_workflow import PreviewWorkflowService
from fileflow.storage import Database
from fileflow.undo import UndoController, UndoControllerState
from fileflow.workers.apply_worker import ApplyWorker
from fileflow.workers.undo_worker import UndoWorker


def require_windows(test_func):
    return unittest.skipUnless(sys.platform == "win32", "real Windows worker integration")(test_func)


class TrackingDatabase(Database):
    def __init__(self, path: str, closed: threading.Event):
        super().__init__(path)
        self.closed = closed

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.closed.set()


def tracking_database_factory(closed: threading.Event):
    return lambda path: TrackingDatabase(path, closed)


def run_in_qthread(worker, timeout_ms: int = 20_000):
    app = QCoreApplication.instance() or QCoreApplication([])
    thread = QThread()
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    observed = {"result": None, "error": None, "timed_out": False}

    def finished(result):
        observed["result"] = result

    def failed(error):
        observed["error"] = error

    def timed_out():
        observed["timed_out"] = True
        thread.quit()
        loop.quit()

    worker.moveToThread(thread)
    worker.finished.connect(finished)
    worker.failed.connect(failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.started.connect(worker.run)
    thread.finished.connect(loop.quit)
    timer.timeout.connect(timed_out)

    thread.start()
    timer.start(timeout_ms)
    loop.exec()
    timer.stop()
    stopped = thread.wait(5_000)
    return observed, stopped, app


class WorkerThreadIntegrationTests(unittest.TestCase):
    @require_windows
    def test_apply_worker_owns_file_database_connection_in_real_qthread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Root"
            root.mkdir()
            (root / "Documents").mkdir()
            (root / "a.pdf").write_text("apply", encoding="utf-8")
            database_path = Path(tmp) / "fileflow.db"
            main_database = Database(database_path)
            main_database.migrate()
            try:
                plan = PreviewWorkflowService().analyse_folder(str(root)).plan
                self.assertIsNotNone(plan)
                self.assertTrue(ApplyController(main_database).validate_before_confirmation(plan).can_apply)
                closed = threading.Event()
                worker = ApplyWorker(
                    str(database_path),
                    plan,
                    database_factory=tracking_database_factory(closed),
                )

                observed, stopped, app = run_in_qthread(worker)

                self.assertFalse(observed["timed_out"])
                self.assertTrue(stopped)
                self.assertIsNone(observed["error"])
                self.assertEqual(ApplyState.COMPLETE, observed["result"].state)
                self.assertEqual(1, observed["result"].succeeded)
                self.assertTrue(closed.is_set())
                self.assertFalse((root / "a.pdf").exists())
                self.assertTrue((root / "Documents" / "a.pdf").exists())
                history = ApplyController(main_database).history_rows()
                self.assertEqual(1, len(history))
                self.assertEqual(1, history[0]["succeeded_count"])
                self.assertIsNotNone(app)
            finally:
                main_database.close()

    @require_windows
    def test_undo_worker_owns_file_database_connection_in_real_qthread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Root"
            root.mkdir()
            (root / "Documents").mkdir()
            (root / "a.pdf").write_text("undo", encoding="utf-8")
            database_path = Path(tmp) / "fileflow.db"
            main_database = Database(database_path)
            main_database.migrate()
            try:
                apply_plan = PreviewWorkflowService().analyse_folder(str(root)).plan
                apply_result = ApplyController(main_database).confirm_and_apply(apply_plan, lambda summary: True)
                self.assertEqual(ApplyState.COMPLETE, apply_result.state)
                controller = UndoController(main_database)
                undo_plan = controller.create_plan(apply_result.batch_id)
                self.assertTrue(controller.validate_before_confirmation(undo_plan).can_undo)
                closed = threading.Event()
                worker = UndoWorker(
                    str(database_path),
                    undo_plan,
                    database_factory=tracking_database_factory(closed),
                )

                observed, stopped, app = run_in_qthread(worker)

                self.assertFalse(observed["timed_out"])
                self.assertTrue(stopped)
                self.assertIsNone(observed["error"])
                self.assertEqual(UndoControllerState.COMPLETE, observed["result"].state)
                self.assertEqual(1, observed["result"].succeeded)
                self.assertTrue(closed.is_set())
                self.assertTrue((root / "a.pdf").exists())
                self.assertFalse((root / "Documents" / "a.pdf").exists())
                undo_history = UndoController(main_database).list_batches()
                self.assertEqual(1, len(undo_history))
                self.assertEqual(1, undo_history[0]["succeeded_count"])
                self.assertIsNotNone(app)
            finally:
                main_database.close()

    @require_windows
    def test_apply_worker_stale_second_revalidation_fails_closed_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Root"
            root.mkdir()
            (root / "Documents").mkdir()
            source = root / "a.pdf"
            source.write_text("preview", encoding="utf-8")
            database_path = Path(tmp) / "fileflow.db"
            main_database = Database(database_path)
            main_database.migrate()
            try:
                plan = PreviewWorkflowService().analyse_folder(str(root)).plan
                self.assertTrue(ApplyController(main_database).validate_before_confirmation(plan).can_apply)
                source.write_text("changed after confirmation", encoding="utf-8")
                closed = threading.Event()
                worker = ApplyWorker(
                    str(database_path),
                    plan,
                    database_factory=tracking_database_factory(closed),
                )

                observed, stopped, app = run_in_qthread(worker)

                self.assertFalse(observed["timed_out"])
                self.assertTrue(stopped)
                self.assertIsNone(observed["error"])
                self.assertEqual(ApplyState.PREVIEW_STALE, observed["result"].state)
                self.assertTrue(closed.is_set())
                self.assertTrue(source.exists())
                self.assertFalse((root / "Documents" / "a.pdf").exists())
                self.assertEqual(0, len(ApplyController(main_database).history_rows()))
                self.assertIsNotNone(app)
            finally:
                main_database.close()

    def test_worker_connections_close_before_unexpected_failure_signal(self):
        class RaisingApplyController:
            def __init__(self, database):
                self.database = database

            def apply_confirmed(self, plan, *, progress_callback=None):
                raise RuntimeError("injected Apply failure")

        class RaisingUndoController:
            def __init__(self, database):
                self.database = database

            def undo_confirmed(self, plan, *, progress_callback=None):
                raise RuntimeError("injected Undo failure")

        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "fileflow.db"
            setup_database = Database(database_path)
            setup_database.migrate()
            setup_database.close()
            cases = (
                (ApplyWorker, RaisingApplyController, "injected Apply failure"),
                (UndoWorker, RaisingUndoController, "injected Undo failure"),
            )
            for worker_type, controller_type, expected_error in cases:
                with self.subTest(worker=worker_type.__name__):
                    closed = threading.Event()
                    worker = worker_type(
                        str(database_path),
                        object(),
                        database_factory=tracking_database_factory(closed),
                        controller_factory=controller_type,
                    )

                    observed, stopped, app = run_in_qthread(worker)

                    self.assertFalse(observed["timed_out"])
                    self.assertTrue(stopped)
                    self.assertIsNone(observed["result"])
                    self.assertIsNotNone(observed["error"])
                    self.assertIn(expected_error, observed["error"].details["error"])
                    self.assertTrue(closed.is_set())
                    self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
