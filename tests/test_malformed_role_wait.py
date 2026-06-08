#!/usr/bin/env python3
"""Tests for malformed role_wait inputs and single-active-role behavior."""

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


class TestMalformedRoleWaitInputs(unittest.TestCase):
    """Test that role_wait tolerates malformed LLM argument shapes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_malformed_rw_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_nested_role_run_id_with_nested_args(self, mock_impl):
        """role_wait extracts role_run_id and nested args from a dict."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={
                "role_run_id": "abc-scout-1",
                "timeout_seconds": 600,
                "poll_interval_seconds": 15,
            },
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 600)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 15)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_text_wrappers_for_all_args(self, mock_impl):
        """role_wait handles {\"text\": ...} wrappers for all args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"text": "abc-scout-1"},
            timeout_seconds={"text": "600"},
            poll_interval_seconds={"text": "15"},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 600)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 15)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_plain_string_role_run_id_still_works(self, mock_impl):
        """role_wait with plain string role_run_id still works."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id="abc-scout-1",
            timeout_seconds=300,
            poll_interval_seconds=10,
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 300)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_nested_role_run_id_without_nested_args(self, mock_impl):
        """role_wait extracts role_run_id from nested dict without nested args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={
                "role_run_id": "abc-scout-1",
                "status": "running",
            },
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_mixed_wrappers_and_plain(self, mock_impl):
        """role_wait handles mixed plain and wrapped args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"text": "abc-scout-1"},
            timeout_seconds=300,
            poll_interval_seconds={"text": "10"},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 300)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 10)


class TestSingleActiveRoleBehavior(unittest.TestCase):
    """Test single-active-role protection in role_start."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_sar_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_tools._get_role_store")
    @patch("mcp_agent.role_tools._find_active_role_run")
    def test_no_active_role_allows_start(self, mock_find, mock_store):
        """role_start succeeds when no active role exists."""
        mock_find.return_value = None
        mock_impl = MagicMock()
        mock_impl.return_value = {
            "role_run_id": "test-scout-1",
            "status": "running",
        }

        with patch("mcp_agent.server._role_tools.role_start_impl", mock_impl):
            from mcp_agent.server import role_start

            result = role_start(
                role="scout",
                prompt="Test task",
                context={"run_id": "test-run-1"},
            )

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["role_run_id"], "test-scout-1")
        mock_impl.assert_called_once()

    @patch("mcp_agent.role_tools._get_role_store")
    @patch("mcp_agent.role_tools._find_active_role_run")
    def test_active_role_rejects_new_start(self, mock_find, mock_store):
        """role_start rejects when another role is active."""
        mock_find.return_value = {
            "role_run_id": "active-scout-1",
            "role": "scout",
            "status": "running",
        }

        from mcp_agent.server import role_start

        result = role_start(
            role="architect",
            prompt="Plan implementation",
            context={"run_id": "test-run-1"},
        )

        self.assertEqual(result["error"], "another_role_running")
        self.assertIn("active_role_run_id", result)
        self.assertEqual(result["active_role_run_id"], "active-scout-1")
        self.assertIn("next_action", result)
        self.assertEqual(result["next_action"]["tool"], "role_wait")

    @patch("mcp_agent.role_tools._get_role_store")
    @patch("mcp_agent.role_tools._find_active_role_run")
    def test_idempotent_reuse_same_active_run(self, mock_find, mock_store):
        """Same idempotency_key for same active run returns existing metadata."""
        mock_find.return_value = {
            "role_run_id": "active-scout-1",
            "role": "scout",
            "status": "running",
            "timeout_minutes": 60,
        }

        # Mock the idempotency lookup to return the same active role_run_id
        mock_store_instance = MagicMock()
        mock_store_instance.find_by_idempotency_scope.return_value = "active-scout-1"
        mock_store.return_value = mock_store_instance

        from mcp_agent.server import role_start

        result = role_start(
            role="scout",
            prompt="Test task",
            context={"run_id": "test-run-1", "idempotency_key": "idem-abc"},
            idempotency_key="idem-abc",
        )

        self.assertEqual(result["idempotent_reuse"], True)
        self.assertEqual(result["role_run_id"], "active-scout-1")
        self.assertEqual(result["status"], "running")

    @patch("mcp_agent.role_tools._get_role_store")
    @patch("mcp_agent.role_tools._find_active_role_run")
    def test_terminal_role_allows_new_start(self, mock_find, mock_store):
        """New role can start after previous role reaches terminal status."""
        # Return None because all roles are terminal (no active found)
        mock_find.return_value = None

        mock_impl = MagicMock()
        mock_impl.return_value = {
            "role_run_id": "new-architect-1",
            "status": "running",
        }

        with patch("mcp_agent.server._role_tools.role_start_impl", mock_impl):
            from mcp_agent.server import role_start

            result = role_start(
                role="architect",
                prompt="Plan implementation",
                context={"run_id": "test-run-1"},
            )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()

    @patch("mcp_agent.role_tools._get_role_store")
    @patch("mcp_agent.role_tools._find_active_role_run")
    def test_error_includes_active_role_info(self, mock_find, mock_store):
        """Error response includes active role_run_id, role, and status."""
        mock_find.return_value = {
            "role_run_id": "active-scout-1",
            "role": "scout",
            "status": "running",
        }

        from mcp_agent.server import role_start

        result = role_start(
            role="coder",
            prompt="Implement feature",
            context={"run_id": "test-run-1"},
        )

        self.assertEqual(result["error"], "another_role_running")
        self.assertEqual(result["active_role_run_id"], "active-scout-1")
        self.assertEqual(result["active_role"], "scout")
        self.assertEqual(result["active_status"], "running")
        self.assertIn("role_wait", result["next_action"]["tool"])


if __name__ == "__main__":
    unittest.main()
