#!/usr/bin/env python3
"""Tests for stale active-role lock prevention."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path so imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Helper: patch the correct module name.
# openhands_get_task_status is defined in mcp_agent.server and imported
# inside role_tools functions via "from .server import openhands_get_task_status".
# Therefore we must patch at mcp_agent.server, not mcp_agent.role_tools.
# ---------------------------------------------------------------------------
_OH_STATUS_PATCH = "mcp_agent.server.openhands_get_task_status"


class TestStaleActiveRoleLockPrevention(unittest.TestCase):
    """Tests for stale active-role lock prevention."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_stale_lock_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        # Reset module-level singleton
        import mcp_agent.role_tools as rt

        rt._role_store = None

    # ------------------------------------------------------------------
    # Test 1: Stale persisted running + actual completed
    # ------------------------------------------------------------------

    def test_stale_persisted_running_actual_completed(self):
        """Persisted status='running' but actual OpenHands status='completed'
        should be cleared, allowing a new role_start."""
        # Create a role run JSON file with status="running"
        role_run = {
            "run_id": "test-run-1",
            "role_run_id": "stale-scout-1",
            "role": "scout",
            "openhands_task_id": "task-123",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "stale-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        # Mock openhands_get_task_status to return "completed"
        with patch(_OH_STATUS_PATCH, return_value={"status": "completed"}):
            from mcp_agent.role_store import RoleRunStore
            from mcp_agent.role_tools import _find_active_role_run

            store = RoleRunStore(self.tmpdir)
            active = _find_active_role_run(store)

        # Verify: no active role found (stale lock cleared)
        self.assertIsNone(active)

        # Verify: persisted status was updated to "completed"
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "completed")

    # ------------------------------------------------------------------
    # Test 2: role_wait(return_result=False) persists completed
    # ------------------------------------------------------------------

    def test_role_wait_return_result_false_persists_completed(self):
        """role_wait with return_result=False and actual completed status
        should persist 'completed' to the role run record."""
        # Create a role run JSON file
        role_run = {
            "run_id": "test-run-2",
            "role_run_id": "wait-scout-1",
            "role": "scout",
            "openhands_task_id": "task-456",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "wait-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        # Mock role_status_impl to return "completed" immediately
        with patch(
            "mcp_agent.role_tools.role_status_impl",
            return_value={"status": "completed"},
        ), patch(_OH_STATUS_PATCH, return_value={"status": "completed"}):
            from mcp_agent.role_tools import role_wait_impl

            result = role_wait_impl(
                role_run_id="wait-scout-1",
                timeout_seconds=10,
                poll_interval_seconds=5,
                return_result=False,
            )

        # Verify: persisted status was updated to "completed"
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "completed")

    # ------------------------------------------------------------------
    # Test 3: completed_empty_result is terminal
    # ------------------------------------------------------------------

    def test_completed_empty_result_is_terminal(self):
        """completed_empty_result must be in TERMINAL_STATUSES."""
        from mcp_agent.role_tools import TERMINAL_STATUSES

        self.assertIn("completed_empty_result", TERMINAL_STATUSES)

    # ------------------------------------------------------------------
    # Test 4: Failure-like terminal status
    # ------------------------------------------------------------------

    def test_failed_status_clears_stale_lock(self):
        """Persisted status='running' but actual OpenHands status='failed'
        should be cleared."""
        role_run = {
            "run_id": "test-run-4",
            "role_run_id": "failed-scout-1",
            "role": "scout",
            "openhands_task_id": "task-789",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "failed-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(_OH_STATUS_PATCH, return_value={"status": "failed"}):
            from mcp_agent.role_store import RoleRunStore
            from mcp_agent.role_tools import _find_active_role_run

            store = RoleRunStore(self.tmpdir)
            active = _find_active_role_run(store)

        self.assertIsNone(active)
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "failed")

    # ------------------------------------------------------------------
    # Test 5: Still-running status is detected as active
    # ------------------------------------------------------------------

    def test_still_running_detected_as_active(self):
        """Persisted status='running' and actual OpenHands status='running'
        should be returned as active."""
        role_run = {
            "run_id": "test-run-5",
            "role_run_id": "active-scout-1",
            "role": "scout",
            "openhands_task_id": "task-abc",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "active-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(_OH_STATUS_PATCH, return_value={"status": "running"}):
            from mcp_agent.role_store import RoleRunStore
            from mcp_agent.role_tools import _find_active_role_run

            store = RoleRunStore(self.tmpdir)
            active = _find_active_role_run(store)

        self.assertIsNotNone(active)
        self.assertEqual(active["role_run_id"], "active-scout-1")

    # ------------------------------------------------------------------
    # Test 6: Refresh failed — safe fallback
    # ------------------------------------------------------------------

    def test_refresh_failed_safe_fallback(self):
        """When OpenHands is unavailable, the stale candidate should still
        be returned as active (safe behavior) with _refresh_failed=True."""
        role_run = {
            "run_id": "test-run-6",
            "role_run_id": "nofail-scout-1",
            "role": "scout",
            "openhands_task_id": "task-def",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "nofail-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(
            _OH_STATUS_PATCH,
            side_effect=Exception("Connection refused"),
        ):
            from mcp_agent.role_store import RoleRunStore
            from mcp_agent.role_tools import _find_active_role_run

            store = RoleRunStore(self.tmpdir)
            active = _find_active_role_run(store)

        self.assertIsNotNone(active)
        self.assertEqual(active["role_run_id"], "nofail-scout-1")
        self.assertTrue(active.get("_refresh_failed", False))

    # ------------------------------------------------------------------
    # Test 7: Idempotent reuse still works (TERMINAL_STATUSES check)
    # ------------------------------------------------------------------

    def test_idempotent_reuse_terminal_statuses(self):
        """Verify TERMINAL_STATUSES includes expected values for idempotent
        reuse to work correctly."""
        from mcp_agent.role_tools import TERMINAL_STATUSES

        # Verify TERMINAL_STATUSES includes expected values
        self.assertIn("completed", TERMINAL_STATUSES)
        self.assertIn("failed", TERMINAL_STATUSES)
        self.assertIn("cancelled", TERMINAL_STATUSES)
        self.assertIn("canceled", TERMINAL_STATUSES)
        self.assertIn("timeout", TERMINAL_STATUSES)
        self.assertIn("timed_out", TERMINAL_STATUSES)
        self.assertIn("stuck", TERMINAL_STATUSES)
        self.assertIn("error", TERMINAL_STATUSES)
        self.assertIn("completed_empty_result", TERMINAL_STATUSES)

    # ------------------------------------------------------------------
    # Test 8: No active role when all terminal
    # ------------------------------------------------------------------

    def test_no_active_when_all_terminal(self):
        """When all role runs are terminal, _find_active_role_run returns None."""
        from mcp_agent.role_store import RoleRunStore
        from mcp_agent.role_tools import _find_active_role_run

        # Create two terminal role runs
        for name, status in [
            ("done-scout-1", "completed"),
            ("done-coder-1", "failed"),
        ]:
            role_run = {
                "run_id": f"test-run-{name}",
                "role_run_id": name,
                "role": name.split("-")[0],
                "openhands_task_id": f"task-{name}",
                "status": status,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            filepath = Path(self.tmpdir) / f"{name}.json"
            filepath.write_text(json.dumps(role_run))

        store = RoleRunStore(self.tmpdir)
        active = _find_active_role_run(store)
        self.assertIsNone(active)

    # ------------------------------------------------------------------
    # Test 9: completed_empty_result clears stale lock
    # ------------------------------------------------------------------

    def test_completed_empty_result_clears_stale_lock(self):
        """Persisted status='running' but actual OpenHands returns
        completed_empty_result should be cleared."""
        role_run = {
            "run_id": "test-run-9",
            "role_run_id": "empty-scout-1",
            "role": "scout",
            "openhands_task_id": "task-empty",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "empty-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(
            _OH_STATUS_PATCH,
            return_value={"status": "completed_empty_result"},
        ):
            from mcp_agent.role_store import RoleRunStore
            from mcp_agent.role_tools import _find_active_role_run

            store = RoleRunStore(self.tmpdir)
            active = _find_active_role_run(store)

        self.assertIsNone(active)
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "completed_empty_result")

    # ------------------------------------------------------------------
    # Test 10: unknown status clears stale lock
    # ------------------------------------------------------------------

    def test_unknown_status_clears_stale_lock(self):
        """Persisted status='running' but actual OpenHands returns 'unknown'
        should be cleared (unknown is terminal)."""
        role_run = {
            "run_id": "test-run-10",
            "role_run_id": "unk-scout-1",
            "role": "scout",
            "openhands_task_id": "task-unk",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "unk-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(_OH_STATUS_PATCH, return_value={"status": "unknown"}):
            from mcp_agent.role_store import RoleRunStore
            from mcp_agent.role_tools import _find_active_role_run

            store = RoleRunStore(self.tmpdir)
            active = _find_active_role_run(store)

        self.assertIsNone(active)
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "unknown")

    # ------------------------------------------------------------------
    # Test 11: missing task_id — safe fallback
    # ------------------------------------------------------------------

    def test_missing_task_id_safe_fallback(self):
        """When role run has no openhands_task_id, the candidate should
        still be returned as active with _refresh_failed=True."""
        role_run = {
            "run_id": "test-run-11",
            "role_run_id": "notask-scout-1",
            "role": "scout",
            # intentionally no openhands_task_id
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "notask-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        from mcp_agent.role_store import RoleRunStore
        from mcp_agent.role_tools import _find_active_role_run

        store = RoleRunStore(self.tmpdir)
        active = _find_active_role_run(store)

        self.assertIsNotNone(active)
        self.assertEqual(active["role_run_id"], "notask-scout-1")
        self.assertTrue(active.get("_refresh_failed", False))


class TestTerminalStatuses(unittest.TestCase):
    """Verify TERMINAL_STATUSES contains all expected statuses."""

    def test_all_expected_statuses_present(self):
        from mcp_agent.role_tools import TERMINAL_STATUSES

        expected = {
            "completed",
            "completed_empty_result",
            "failed",
            "cancelled",
            "canceled",
            "timeout",
            "timed_out",
            "stuck",
            "error",
            "unknown",
        }
        self.assertTrue(expected.issubset(TERMINAL_STATUSES))

    def test_running_not_terminal(self):
        from mcp_agent.role_tools import TERMINAL_STATUSES

        self.assertNotIn("running", TERMINAL_STATUSES)


class TestRoleStatusPersists(unittest.TestCase):
    """Test that role_status_impl persists actual task status."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_status_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_tools as rt

        rt._role_store = None

    def test_role_status_impl_persists_status(self):
        """role_status_impl must persist the actual OpenHands status."""
        # Create a role run JSON file
        role_run = {
            "run_id": "test-run-rs",
            "role_run_id": "rs-scout-1",
            "role": "scout",
            "openhands_task_id": "task-rs",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "rs-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(_OH_STATUS_PATCH, return_value={"status": "completed"}):
            from mcp_agent.role_tools import role_status_impl

            result = role_status_impl("rs-scout-1")

        self.assertEqual(result["status"], "completed")
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "completed")


class TestRoleResultEmptyResultPersists(unittest.TestCase):
    """Test that role_result_impl persists completed_empty_result."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_result_empty_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_tools as rt

        rt._role_store = None

    def test_role_result_impl_persists_empty_result(self):
        """role_result_impl must persist 'completed_empty_result' when
        the answer is empty."""
        # Create a role run JSON file
        role_run = {
            "run_id": "test-run-re",
            "role_run_id": "re-scout-1",
            "role": "scout",
            "openhands_task_id": "task-re",
            "status": "running",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        filepath = Path(self.tmpdir) / "re-scout-1.json"
        filepath.write_text(json.dumps(role_run))

        with patch(_OH_STATUS_PATCH, return_value={"status": "completed"}), patch(
            "mcp_agent.server.openhands_get_task_result",
            return_value={"answer": ""},
        ):
            from mcp_agent.role_tools import role_result_impl

            result = role_result_impl("re-scout-1")

        self.assertEqual(result["status"], "completed_empty_result")
        updated = json.loads(filepath.read_text())
        self.assertEqual(updated["status"], "completed_empty_result")


if __name__ == "__main__":
    unittest.main()
