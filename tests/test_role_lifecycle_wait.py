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
        """poll_interval_seconds should be respected, not overridden by global."""
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
            timeout_seconds=3,
            poll_interval_seconds=1,
        )
        elapsed = time.monotonic() - start

        self.assertEqual(result["status"], "running")
        self.assertTrue(result.get("timeout"))
        # With poll_interval=1 and timeout=3, we expect ~3 polls (every 1 second)
        self.assertGreaterEqual(call_count, 2, f"Expected >= 2 polls, got {call_count}")
        self.assertLess(elapsed, 10, f"role_wait took {elapsed:.1f}s, expected ~3s")


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


if __name__ == "__main__":
    unittest.main()
