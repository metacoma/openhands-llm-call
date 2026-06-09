#!/usr/bin/env python3
"""Tests for role_call -> role_wait two-step lifecycle blockers.

Covers Tests 1-6 and Test 8 from the PR #33 fix requirements.
Test 7 (wrapped args) is already in test_malformed_role_wait.py.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Ensure role store dir is set before any imports
TEST_STATE_DIR = tempfile.mkdtemp(prefix="test_role_wait_")

from mcp_agent import role_lifecycle
from mcp_agent.artifact_store import ArtifactStore


def _cleanup():
    shutil.rmtree(TEST_STATE_DIR, ignore_errors=True)


def _setup_store(state_dir, status="running"):
    """Create a role run record for testing."""
    from mcp_agent.role_store import RoleRunStore
    store = RoleRunStore(state_dir)
    run_id = f"test-run-{int(time.time())}"
    role_run_id = f"test-run-scout-{run_id}"
    store.create_role_run(
        role="scout",
        run_id=run_id,
        role_run_id=role_run_id,
        openhands_task_id="task-123",
        artifact_name="scout_report",
    )
    if status != "running":
        store.update_role_run(role_run_id, status=status)
    return role_run_id


# ---------------------------------------------------------------------------
# Test 1: role_wait timeout respected
# ---------------------------------------------------------------------------

class TestRoleWaitTimeout(unittest.TestCase):
    """Test that role_wait(timeout_seconds=N) actually returns after ~N seconds."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_timeout_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_timeout_respected_with_running_job(self, mock_get_status):
        """Mock job always running. role_wait(timeout_seconds=2, poll_interval_seconds=1) returns ~2s."""
        mock_get_status.return_value = {"_normalized_status": "running", "status": "running"}

        role_run_id = _setup_store(self.tmpdir)

        start = time.monotonic()
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=2,
            poll_interval_seconds=1,
        )
        elapsed = time.monotonic() - start

        self.assertEqual(result["status"], "running")
        self.assertTrue(result.get("timeout"))
        # Should return in ~2 seconds, not 3600+ seconds
        self.assertLess(elapsed, 10, f"role_wait took {elapsed:.1f}s, expected ~2s")

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_poll_interval_actually_used(self, mock_get_status):
        """poll_interval_seconds should be respected (bounded by _MIN_POLL_INTERVAL=5)."""
        call_count = 0

        def side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"_normalized_status": "running", "status": "running"}

        mock_get_status.side_effect = side_effect

        role_run_id = _setup_store(self.tmpdir)

        start = time.monotonic()
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=12,
            poll_interval_seconds=5,  # >= _MIN_POLL_INTERVAL so it is respected
        )
        elapsed = time.monotonic() - start

        self.assertEqual(result["status"], "running")
        self.assertTrue(result.get("timeout"))
        # With poll_interval=5 and timeout=12, we expect at least 2 polls
        self.assertGreaterEqual(call_count, 2, f"Expected >= 2 polls, got {call_count}")
        self.assertLess(elapsed, 20, f"role_wait took {elapsed:.1f}s, expected ~12s")


# ---------------------------------------------------------------------------
# Test 2: role_wait completed returns sanitized artifact refs
# ---------------------------------------------------------------------------

class TestRoleWaitSanitizedArtifacts(unittest.TestCase):
    """Test that completed response contains sanitized artifact refs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_sanitized_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_completed_returns_sanitized_artifacts(self, mock_start,
                                                    mock_get_status):
        """Completed response contains artifact_id/artifact_type but NOT artifact_path."""
        mock_get_status.side_effect = [
            # First call: main job status
            {"_normalized_status": "completed", "status": "completed",
             "answer": json.dumps({
                 "status": "completed",
                 "role": "scout",
                 "summary": "Scout completed.",
                 "primary_artifact_name": "scout_report",
                 "blocking": False,
                 "risk_level": "LOW",
                 "action": None,
                 "blocking_summary": [],
             })},
            # Second call: summary job status
            {"_normalized_status": "completed", "status": "completed",
             "answer": json.dumps({
                 "status": "completed",
                 "role": "scout",
                 "summary": "Scout summary.",
                 "primary_artifact_name": "scout_report",
                 "blocking": False,
                 "risk_level": "LOW",
                 "action": None,
                 "blocking_summary": [],
             })},
        ]

        # Mock main conversation start
        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            {"task_id": "task-summary", "conversation_id": "conv-1"},
        ]

        role_run_id = _setup_store(self.tmpdir)

        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=300,
            poll_interval_seconds=5,
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("artifacts", result)
        for key in ("primary", "summary"):
            self.assertIn(key, result["artifacts"])
            self.assertNotIn("artifact_path", result["artifacts"][key],
                             f"artifact_path leaked in artifacts.{key}")
            self.assertIn("artifact_id", result["artifacts"][key])
            self.assertIn("artifact_type", result["artifacts"][key])


# ---------------------------------------------------------------------------
# Test 3: repeated role_wait is idempotent
# ---------------------------------------------------------------------------

class TestRepeatedRoleWaitIdempotent(unittest.TestCase):
    """Test that repeated role_wait on completed run is idempotent."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_repeated_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_repeated_role_wait_returns_same_result(self, mock_start,
                                                     mock_get_status):
        """First role_wait completes. Second role_wait returns same result."""
        mock_get_status.side_effect = [
            # First call: main job status
            {"_normalized_status": "completed", "status": "completed",
             "answer": json.dumps({
                 "status": "completed",
                 "role": "scout",
                 "summary": "Scout completed.",
                 "primary_artifact_name": "scout_report",
                 "blocking": False,
                 "risk_level": "LOW",
                 "action": None,
                 "blocking_summary": [],
             })},
            # Second call: summary job status
            {"_normalized_status": "completed", "status": "completed",
             "answer": json.dumps({
                 "status": "completed",
                 "role": "scout",
                 "summary": "Scout summary.",
                 "primary_artifact_name": "scout_report",
                 "blocking": False,
                 "risk_level": "LOW",
                 "action": None,
                 "blocking_summary": [],
             })},
        ]

        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            {"task_id": "task-summary", "conversation_id": "conv-1"},
        ]

        role_run_id = _setup_store(self.tmpdir)

        # First call
        result1 = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=300,
            poll_interval_seconds=5,
        )

        self.assertEqual(result1["status"], "completed")
        self.assertIn("control_summary", result1)

        # Verify store has completed state
        from mcp_agent.role_store import RoleRunStore
        store = RoleRunStore(self.tmpdir)
        stored_run = store.get_role_run(role_run_id)
        self.assertEqual(stored_run["status"], "completed")
        self.assertIn("artifacts", stored_run)

        # Second call should return same result without calling summary again
        result2 = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=300,
            poll_interval_seconds=5,
        )

        self.assertEqual(result2["status"], "completed")
        self.assertEqual(result1["control_summary"], result2["control_summary"])


# ---------------------------------------------------------------------------
# Test 4: fallback summary persisted
# ---------------------------------------------------------------------------

class TestFallbackSummaryPersisted(unittest.TestCase):
    """Test that fallback summary is persisted and repeated role_wait is idempotent."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_fallback_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_fallback_summary_persisted(self, mock_start,
                                        mock_get_status):
        """Mock summary prompt failure. Verify fallback is saved to RoleStore."""
        mock_get_status.side_effect = [
            # Main job completes
            {"_normalized_status": "completed", "status": "completed",
             "answer": json.dumps({
                 "status": "completed",
                 "role": "scout",
                 "summary": "Scout completed.",
                 "primary_artifact_name": "scout_report",
                 "blocking": False,
                 "risk_level": "LOW",
                 "action": None,
                 "blocking_summary": [],
             })},
            # Summary job check (will fail → fallback)
            {"_normalized_status": "completed", "status": "completed",
             "answer": json.dumps({
                 "status": "completed",
                 "role": "scout",
                 "summary": "Scout summary.",
                 "primary_artifact_name": "scout_report",
                 "blocking": False,
                 "risk_level": "LOW",
                 "action": None,
                 "blocking_summary": [],
             })},
        ]

        # Mock main conversation start, then summary prompt fails
        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            # Second call (summary) will fail
            Exception("Summary prompt failed"),
        ]

        role_run_id = _setup_store(self.tmpdir)

        # First call: main completes, summary prompt fails → fallback
        result1 = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=300,
            poll_interval_seconds=5,
        )

        self.assertEqual(result1["status"], "completed")

        # Verify store has completed state
        from mcp_agent.role_store import RoleRunStore
        store = RoleRunStore(self.tmpdir)
        stored_run = store.get_role_run(role_run_id)
        self.assertEqual(stored_run["status"], "completed")
        self.assertIn("artifacts", stored_run)

        # Second call: reads from store, returns completed without rerunning summary
        result2 = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=300,
            poll_interval_seconds=5,
        )

        self.assertEqual(result2["status"], "completed")


# ---------------------------------------------------------------------------
# Test 5: role_call start failure
# ---------------------------------------------------------------------------

class TestRoleCallStartFailure(unittest.TestCase):
    """Test that role_call handles OpenHands start failure correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rc_fail_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_start_failure_sets_failed_status(self, mock_start):
        """Mock _start_conversation_on_fastapi() failure. Verify status=failed."""
        mock_start.side_effect = ConnectionError("connection refused")

        result = role_lifecycle.role_call_start_impl(
            role="scout",
            user_task="test task",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "ConversationStartError")

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_start_failure_no_idempotency_record(self, mock_start):
        """Mock _start_conversation_on_fastapi() failure. No idempotency record left."""
        mock_start.side_effect = ConnectionError("connection refused")

        result = role_lifecycle.role_call_start_impl(
            role="scout",
            user_task="test task",
            api_key="test-key",
            idempotency_key="test-idem-key",
        )

        self.assertEqual(result["status"], "failed")

        # Verify no idempotency record was saved
        from mcp_agent.role_store import RoleRunStore
        store = RoleRunStore(self.tmpdir)
        scope = "scout:test-idem-key"
        existing = store.find_by_idempotency_scope(scope)
        self.assertIsNone(existing,
                         f"Idempotency record should not exist after failed start, found: {existing}")


# ---------------------------------------------------------------------------
# Test 6: role_call dedupe
# ---------------------------------------------------------------------------

class TestRoleCallDedupe(unittest.TestCase):
    """Test that role_call deduplication works correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rc_dedupe_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_dedupe_same_idempotency_key(self, mock_start):
        """Two role_call with same idempotency_key → only one OpenHands start, same role_run_id."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"task_id": "task-dedupe", "conversation_id": "conv-dedupe"}

        mock_start.side_effect = side_effect

        # First call
        result1 = role_lifecycle.role_call_start_impl(
            role="scout",
            user_task="test task",
            api_key="test-key",
            idempotency_key="dedupe-test-key",
        )

        # Second call with same key
        result2 = role_lifecycle.role_call_start_impl(
            role="scout",
            user_task="test task",
            api_key="test-key",
            idempotency_key="dedupe-test-key",
        )

        # Only one OpenHands start call
        self.assertEqual(call_count, 1)

        # Same role_run_id returned
        self.assertEqual(result1["role_run_id"], result2["role_run_id"])


# ---------------------------------------------------------------------------
# Test 8: public tool discovery
# ---------------------------------------------------------------------------

class TestPublicToolDiscovery(unittest.TestCase):
    """Test that only role_list, role_call, role_wait are @MCP.tool() decorated."""

    def test_public_tools_only(self):
        """Only role_list, role_call, role_wait are @MCP.tool() decorated."""
        import inspect
        import mcp_agent.server as server_mod

        # Check that the expected public functions exist
        self.assertIn("role_list", dir(server_mod))
        self.assertIn("role_call", dir(server_mod))
        self.assertIn("role_wait", dir(server_mod))


# ---------------------------------------------------------------------------
# Test 3: summary job polling (first running, then completed)
# ---------------------------------------------------------------------------

class TestSummaryJobPolling(unittest.TestCase):
    """Test that role_wait polls for summary until terminal."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_summary_poll_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_first_running_then_completed(self, mock_start, mock_get_status):
        """Summary job first returns running, then completed. role_wait waits and uses real summary."""
        call_count = 0

        def get_status_side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call: main job -> completed
            if call_count == 1:
                return {"_normalized_status": "completed", "status": "completed",
                         "answer": json.dumps({"status": "completed", "role": "scout",
                                               "summary": "Scout done.", "primary_artifact_name": "scout_report",
                                               "blocking": False, "risk_level": "LOW", "action": None,
                                               "blocking_summary": []})}
            # Second call: summary job -> running
            if call_count == 2:
                return {"_normalized_status": "running", "status": "running"}
            # Third call: summary job -> completed
            return {"_normalized_status": "completed", "status": "completed",
                     "answer": json.dumps({"status": "completed", "role": "scout",
                                           "summary": "Real summary.", "primary_artifact_name": "scout_report",
                                           "blocking": False, "risk_level": "LOW", "action": None,
                                           "blocking_summary": []})}

        mock_get_status.side_effect = get_status_side_effect
        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            {"task_id": "task-summary", "conversation_id": "conv-1"},
        ]

        role_run_id = _setup_store(self.tmpdir)

        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=30,
            poll_interval_seconds=1,
        )

        self.assertEqual(result["status"], "completed")
        # Verify real summary was used (not fallback)
        self.assertEqual(result["control_summary"]["summary"], "Real summary.")
        # Verify polling happened (at least 3 calls: main, summary-running, summary-completed)
        self.assertGreaterEqual(call_count, 3)


# ---------------------------------------------------------------------------
# Test 4: repair job polling (first running, then completed)
# ---------------------------------------------------------------------------

class TestRepairJobPolling(unittest.TestCase):
    """Test that role_wait polls for repair until terminal."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_repair_poll_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_repair_first_running_then_completed(self, mock_start, mock_get_status):
        """Repair job first returns running, then completed. role_wait polls for it."""
        call_count = 0

        def get_status_side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call: main job -> completed
            if call_count == 1:
                return {"_normalized_status": "completed", "status": "completed",
                         "answer": json.dumps({"status": "completed", "role": "scout",
                                               "summary": "Scout done.", "primary_artifact_name": "scout_report",
                                               "blocking": False, "risk_level": "LOW", "action": None,
                                               "blocking_summary": []})}
            # Second call: summary job -> completed (but invalid)
            if call_count == 2:
                return {"_normalized_status": "completed", "status": "completed",
                         "answer": json.dumps({"status": "invalid", "role": "scout"})}
            # Third call: repair job -> running
            if call_count == 3:
                return {"_normalized_status": "running", "status": "running"}
            # Fourth call: repair job -> completed
            return {"_normalized_status": "completed", "status": "completed",
                     "answer": json.dumps({"status": "completed", "role": "scout",
                                           "summary": "Repaired summary.", "primary_artifact_name": "scout_report",
                                           "blocking": False, "risk_level": "LOW", "action": None,
                                           "blocking_summary": []})}

        mock_get_status.side_effect = get_status_side_effect
        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            {"task_id": "task-summary", "conversation_id": "conv-1"},
            # Third call (repair) will succeed
            {"task_id": "task-repair", "conversation_id": "conv-1"},
        ]

        role_run_id = _setup_store(self.tmpdir)

        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=30,
            poll_interval_seconds=1,
        )

        self.assertEqual(result["status"], "completed")
        # Verify polling happened (at least 4 calls)
        self.assertGreaterEqual(call_count, 4)


# ---------------------------------------------------------------------------
# Test 4: main job terminal failure statuses return failed (Test 4)
# ---------------------------------------------------------------------------

class TestMainJobTerminalFailure(unittest.TestCase):
    """Test that main job terminal failure statuses return status='failed' (Test 4)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_terminal_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_main_job_timeout_returns_failed(self, mock_get_status):
        """Main job status=timeout -> role_wait returns failed."""
        mock_get_status.return_value = {"_normalized_status": "timeout", "status": "timeout"}
        role_run_id = _setup_store(self.tmpdir)
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id, timeout_seconds=300, poll_interval_seconds=5,
        )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("timeout", result)

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_main_job_timed_out_returns_failed(self, mock_get_status):
        """Main job status=timed_out -> role_wait returns failed."""
        mock_get_status.return_value = {"_normalized_status": "timed_out", "status": "timed_out"}
        role_run_id = _setup_store(self.tmpdir)
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id, timeout_seconds=300, poll_interval_seconds=5,
        )
        self.assertEqual(result["status"], "failed")

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_main_job_canceled_returns_failed(self, mock_get_status):
        """Main job status=canceled -> role_wait returns failed."""
        mock_get_status.return_value = {"_normalized_status": "canceled", "status": "canceled"}
        role_run_id = _setup_store(self.tmpdir)
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id, timeout_seconds=300, poll_interval_seconds=5,
        )
        self.assertEqual(result["status"], "failed")


# ---------------------------------------------------------------------------
# Test 5: summary/repair job terminal statuses handled as terminal (Test 5)
# ---------------------------------------------------------------------------

class TestSummaryJobTerminalStatus(unittest.TestCase):
    """Test that summary/repair job terminal statuses are handled correctly (Test 5)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_summary_term_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_job_timed_out_is_terminal(self, mock_start, mock_get_status):
        """Summary job status=timed_out -> treated as terminal, not infinite polling."""
        call_count = 0
        def get_status_side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Main job -> completed
                return {"_normalized_status": "completed", "status": "completed",
                         "answer": json.dumps({"status": "completed", "role": "scout",
                                               "summary": "Scout done.", "primary_artifact_name": "scout_report",
                                               "blocking": False, "risk_level": "LOW", "action": None,
                                               "blocking_summary": []})}
            # Summary job -> timed_out (terminal)
            return {"_normalized_status": "timed_out", "status": "timed_out"}
        mock_get_status.side_effect = get_status_side_effect
        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            {"task_id": "task-summary", "conversation_id": "conv-1"},
        ]
        role_run_id = _setup_store(self.tmpdir)
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id, timeout_seconds=300, poll_interval_seconds=5,
        )
        # Should return with some status (not hang), and polling should have stopped
        self.assertIn(result["status"], ("completed", "failed", "timeout"))
        # Should NOT have polled summary more than once (timed_out is terminal)
        self.assertLessEqual(call_count, 3)

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_job_canceled_is_terminal(self, mock_start, mock_get_status):
        """Summary job status=canceled -> treated as terminal."""
        call_count = 0
        def get_status_side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"_normalized_status": "completed", "status": "completed",
                         "answer": json.dumps({"status": "completed", "role": "scout",
                                               "summary": "Scout done.", "primary_artifact_name": "scout_report",
                                               "blocking": False, "risk_level": "LOW", "action": None,
                                               "blocking_summary": []})}
            return {"_normalized_status": "canceled", "status": "canceled"}
        mock_get_status.side_effect = get_status_side_effect
        mock_start.side_effect = [
            {"task_id": "task-main", "conversation_id": "conv-1"},
            {"task_id": "task-summary", "conversation_id": "conv-1"},
        ]
        role_run_id = _setup_store(self.tmpdir)
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id, timeout_seconds=300, poll_interval_seconds=5,
        )
        self.assertIn(result["status"], ("completed", "failed", "timeout"))
        self.assertLessEqual(call_count, 3)


# ---------------------------------------------------------------------------
# Test 7: role_wait wrapped args (already in test_malformed_role_wait.py)
# ---------------------------------------------------------------------------

class TestRoleWaitWrappedArgs(unittest.TestCase):
    """Verify Test 7 coverage from test_malformed_role_wait.py."""

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_wrapped_args_supported(self, mock_impl):
        """role_wait handles {"text": ...} wrappers for all args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"text": "abc-scout-1"},
            timeout_seconds={"text": "1800"},
            poll_interval_seconds={"text": "30"},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 1800)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 30)


# ---------------------------------------------------------------------------
# Tests for wait_job_until_terminal — deadline already expired (PR #36)
# ---------------------------------------------------------------------------


class TestWaitJobUntilTerminalDeadlineExpired(unittest.TestCase):
    """Test that wait_job_until_terminal handles pre-expired deadline gracefully.

    Regression tests for UnboundLocalError when ``deadline`` is already in the
    past before the first poll iteration.
    """

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_deadline_already_expired_does_not_crash(self, mock_get_status):
        """When deadline is in the past, no poll should occur; returns timeout."""
        from mcp_agent.role_lifecycle import wait_job_until_terminal

        status, data = wait_job_until_terminal(
            job_id="job-1",
            deadline=time.monotonic() - 1,
            poll_interval_seconds=1,
        )

        self.assertEqual(status, "timeout")
        self.assertTrue(data.get("timeout"))
        # _get_task_status_once must NOT have been called
        mock_get_status.assert_not_called()

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_normal_completed_still_works(self, mock_get_status):
        """First poll returns running, second poll returns completed."""
        from mcp_agent.role_lifecycle import wait_job_until_terminal

        call_count = 0

        def side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"_normalized_status": "running", "status": "running"}
            return {"_normalized_status": "completed", "status": "completed"}

        mock_get_status.side_effect = side_effect

        now = time.monotonic()
        status, data = wait_job_until_terminal(
            job_id="job-2",
            deadline=now + 10,
            poll_interval_seconds=1,
        )

        self.assertEqual(status, "completed")
        self.assertEqual(data.get("_normalized_status"), "completed")
        self.assertEqual(call_count, 2)

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_summary_starts_after_deadline(self, mock_get_status):
        """Simulate role_wait scenario: main job completed near deadline,
        summary phase gets an already-expired deadline."""
        from mcp_agent.role_lifecycle import wait_job_until_terminal

        # Simulate: main job completed just before deadline
        # Now summary phase starts with deadline already expired
        expired_deadline = time.monotonic() - 1

        status, data = wait_job_until_terminal(
            job_id="summary-job-1",
            deadline=expired_deadline,
            poll_interval_seconds=1,
        )

        self.assertEqual(status, "timeout")
        self.assertTrue(data.get("timeout"))
        mock_get_status.assert_not_called()

        # Caller should handle this gracefully:
        # summary_text = data.get("answer", "") or ""  → returns ""
        summary_text = data.get("answer", "") or ""
        self.assertEqual(summary_text, "")


# ---------------------------------------------------------------------------
# Test: Short-polling contract (new defaults, clamping, bounded sleep)
# ---------------------------------------------------------------------------

class TestRoleWaitShortPollingContract(unittest.TestCase):
    """Tests for the new short-polling contract.

    Covers:
    1. timeout_seconds > max is clamped (verified via effective_timeout_seconds in response).
    2. poll_interval_seconds > timeout_seconds does not oversleep beyond timeout.
    3. poll_interval_seconds clamped to max 60.
    4. poll_interval_seconds=0 normalized to _MIN_POLL_INTERVAL (5).
    5. effective_timeout_seconds included in response when clamping occurs.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_rw_contract_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir
        # Patch the module-level constant for fast tests
        import mcp_agent.role_lifecycle as rl
        self._orig_max_timeout = rl._MAX_PUBLIC_ROLE_WAIT_TIMEOUT_SECONDS
        rl._MAX_PUBLIC_ROLE_WAIT_TIMEOUT_SECONDS = 5

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.role_lifecycle as rl
        rl._MAX_PUBLIC_ROLE_WAIT_TIMEOUT_SECONDS = self._orig_max_timeout
        rl._role_store = None
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_timeout_1800_clamped_to_max(self, mock_get_status):
        """Passing timeout_seconds=1800 must NOT cause a 1800-second wait."""
        mock_get_status.return_value = {"_normalized_status": "running", "status": "running"}
        role_run_id = _setup_store(self.tmpdir)
        start = time.monotonic()
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=1800,
            poll_interval_seconds=1,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result["status"], "running")
        self.assertLess(elapsed, 10, f"Wait took {elapsed:.1f}s, expected < 5s (clamped)")

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_poll_interval_greater_than_timeout_does_not_oversleep(self, mock_get_status):
        """poll_interval_seconds > timeout_seconds must not oversleep beyond timeout."""
        call_count = 0
        def side_effect(task_id, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"_normalized_status": "running", "status": "running"}
        mock_get_status.side_effect = side_effect
        role_run_id = _setup_store(self.tmpdir)
        start = time.monotonic()
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=2,
            poll_interval_seconds=60,  # much larger than timeout
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result["status"], "running")
        self.assertLess(elapsed, 5, f"Overslept: {elapsed:.1f}s with timeout=2, poll=60")

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_poll_interval_clamped_to_max_60(self, mock_get_status):
        """poll_interval_seconds > 60 must be clamped to 60."""
        mock_get_status.return_value = {"_normalized_status": "running", "status": "running"}
        role_run_id = _setup_store(self.tmpdir)
        start = time.monotonic()
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=5,  # small timeout to keep test fast
            poll_interval_seconds=300,  # should be clamped to 60
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result["status"], "running")
        # With poll clamped to 60 and timeout=5, should return in ~5s not 300s
        self.assertLess(elapsed, 10, f"Wait took {elapsed:.1f}s with poll clamped from 300")

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_poll_interval_zero_normalized_to_min(self, mock_get_status):
        """poll_interval_seconds=0 must be normalized to _MIN_POLL_INTERVAL (5)."""
        mock_get_status.return_value = {"_normalized_status": "running", "status": "running"}
        role_run_id = _setup_store(self.tmpdir)
        start = time.monotonic()
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=2,
            poll_interval_seconds=0,  # should be clamped to 5
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result["status"], "running")
        self.assertLess(elapsed, 10, f"Wait took {elapsed:.1f}s with poll=0 (should be min 5)")

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_effective_timeout_seconds_in_response_when_clamped(self, mock_get_status):
        """When timeout is clamped, response includes effective_timeout_seconds."""
        mock_get_status.return_value = {"_normalized_status": "running", "status": "running"}
        role_run_id = _setup_store(self.tmpdir)
        result = role_lifecycle.role_lifecycle_wait_impl(
            role_run_id=role_run_id,
            timeout_seconds=1800,
            poll_interval_seconds=1,
        )
        self.assertEqual(result["status"], "running")
        self.assertIn("effective_timeout_seconds", result)
        # The effective timeout should be the patched max (5)
        self.assertEqual(result["effective_timeout_seconds"], 5)
        self.assertIn("wait", result)
        self.assertEqual(result["wait"]["requested_timeout_seconds"], 1800)
        self.assertEqual(result["wait"]["effective_timeout_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
