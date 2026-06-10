#!/usr/bin/env python3
"""Tests for role tools (mcp_agent.role_tools)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch



class TestRoleWaitTool(unittest.TestCase):
    """Tests for the role_wait MCP tool (new lifecycle-aware implementation)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_wait_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        # Clear env vars so defaults are used
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_completed_with_result(
        self, mock_lifecycle_wait
    ):
        """role_wait returns completed result when role finishes."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE", "summary": "Done"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("control_summary", result)
        self.assertIn("artifacts", result)
        mock_lifecycle_wait.assert_called_once()

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_timeout_status(
        self, mock_lifecycle_wait
    ):
        """role_wait returns timeout status when role is still running."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "running",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "timeout": True,
            "message": "Role is still running. Call role_wait again with the same role_run_id.",
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "running")
        self.assertTrue(result.get("timeout"))

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_terminal_failed(
        self, mock_lifecycle_wait
    ):
        """role_wait returns failed status when role fails."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "error": {"type": "ExecutionError", "message": "Task failed", "retryable": True},
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ExecutionError")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_unknown_role_run_id(
        self, mock_lifecycle_wait
    ):
        """role_wait returns error for unknown role_run_id."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "error": {"type": "RoleRunNotFound", "message": "No role run found", "retryable": False},
        }

        result = role_wait(
            role_run_id="nonexistent-run",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "failed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_cancelled_status(
        self, mock_lifecycle_wait
    ):
        """role_wait returns cancelled status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "cancelled",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "cancelled")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_no_full_result(
        self, mock_lifecycle_wait
    ):
        """role_wait response does not contain full_result."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE", "summary": "Done"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("full_result", result)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_bounded_timeout(
        self, mock_lifecycle_wait
    ):
        """role_wait respects timeout_seconds parameter."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=60,
            poll_interval_seconds=5,
        )

        self.assertEqual(result["status"], "completed")
        mock_lifecycle_wait.assert_called_once()

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_huge_timeout(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps huge timeout to max."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=999999,
            poll_interval_seconds=30,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_huge_poll_interval(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps huge poll interval."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=999999,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_negative_timeout(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps negative timeout to default."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=-100,
            poll_interval_seconds=30,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_zero_poll_interval(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps zero poll interval to minimum."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=0,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_fallback_empty_result_no_name_error(
        self, mock_lifecycle_wait
    ):
        """role_wait handles empty result gracefully."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_unknown_role_run_id_via_store(
        self, mock_lifecycle_wait
    ):
        """role_wait handles unknown role_run_id from store."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "error": {"type": "RoleRunNotFound", "message": "No role run found", "retryable": False},
        }

        result = role_wait(
            role_run_id="unknown-run-123",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "failed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_huge_timeout(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps huge timeout to max."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=999999,
            poll_interval_seconds=30,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_huge_poll_interval(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps huge poll interval."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=999999,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_clamps_negative_timeout(
        self, mock_lifecycle_wait
    ):
        """role_wait clamps negative timeout to default."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=-100,
            poll_interval_seconds=30,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_fallback_empty_result_no_name_error(
        self, mock_lifecycle_wait
    ):
        """role_wait handles empty result gracefully."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_unknown_role_run_id_via_store(
        self, mock_lifecycle_wait
    ):
        """role_wait handles unknown role_run_id from store."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "error": {"type": "RoleRunNotFound", "message": "No role run found", "retryable": False},
        }

        result = role_wait(
            role_run_id="unknown-run-123",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "failed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_response_nonce_present_completed(
        self, mock_lifecycle_wait
    ):
        """role_wait response contains response_nonce for completed status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE", "summary": "Done"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertIn("response_nonce", result)
        self.assertIsInstance(result["response_nonce"], str)
        self.assertTrue(len(result["response_nonce"]) > 0)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_response_nonce_unique(
        self, mock_lifecycle_wait
    ):
        """Two separate role_wait calls produce different response_nonce values."""
        import copy
        import time
        from mcp_agent.server import role_wait

        base_result = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {},
        }

        # Use side_effect to return a fresh copy each call
        mock_lifecycle_wait.side_effect = [copy.deepcopy(base_result) for _ in range(2)]

        result1 = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )
        time.sleep(0.05)  # 50ms gap ensures different timestamp
        mock_lifecycle_wait.side_effect = [copy.deepcopy(base_result) for _ in range(2)]

        result2 = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertNotEqual(result1["response_nonce"], result2["response_nonce"])

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_response_nonce_present_running(
        self, mock_lifecycle_wait
    ):
        """role_wait response contains response_nonce for running status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "running",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "timeout": True,
            "message": "Role is still running.",
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertIn("response_nonce", result)
        self.assertIsInstance(result["response_nonce"], str)
        self.assertTrue(len(result["response_nonce"]) > 0)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_response_nonce_present_failed(
        self, mock_lifecycle_wait
    ):
        """role_wait response contains response_nonce for failed status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "error": {"type": "ExecutionError", "message": "Task failed", "retryable": True},
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertIn("response_nonce", result)
        self.assertIsInstance(result["response_nonce"], str)
        self.assertTrue(len(result["response_nonce"]) > 0)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_existing_fields_preserved_completed(
        self, mock_lifecycle_wait
    ):
        """Existing fields are still returned when role completes."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE", "summary": "Done"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["role_run_id"], "run-001")
        self.assertEqual(result["run_id"], "test-run-001")
        self.assertEqual(result["role"], "scout")
        self.assertIn("control_summary", result)
        self.assertIn("artifacts", result)
        self.assertIn("response_nonce", result)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_response_nonce_format(
        self, mock_lifecycle_wait
    ):
        """response_nonce matches expected format pattern."""
        import re
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {},
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        nonce = result["response_nonce"]
        # Format: YYYYMMDDTHHMMSSffffffZ-xxxxxxxx
        pattern = r"^\d{8}T\d{6}\d{6}Z-[a-f0-9]{8}$"
        self.assertRegex(nonce, pattern)


class TestRoleWaitMissingTerminalStatuses(unittest.TestCase):
    """Tests for role_wait with missing terminal statuses."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_wait_mt_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_all_terminal_statuses_return_terminal_response(
        self, mock_lifecycle_wait
    ):
        """All terminal statuses return appropriate responses."""
        from mcp_agent.server import role_wait

        for status in ["completed", "failed", "cancelled", "timed_out"]:
            mock_lifecycle_wait.return_value = {
                "status": status,
                "role_run_id": "run-001",
                "run_id": "test-run-001",
                "role": "scout",
            }

            result = role_wait(
                role_run_id="run-001",
                timeout_seconds=300,
                poll_interval_seconds=15,
            )

            self.assertEqual(result["status"], status)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_canceled_status(
        self, mock_lifecycle_wait
    ):
        """role_wait returns cancelled status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "cancelled",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "cancelled")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_completed_empty_result_status(
        self, mock_lifecycle_wait
    ):
        """role_wait handles completed with empty result."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_error_status(
        self, mock_lifecycle_wait
    ):
        """role_wait handles error status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "error": {"type": "ExecutionError", "message": "Task failed", "retryable": True},
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "failed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_timed_out_status(
        self, mock_lifecycle_wait
    ):
        """role_wait handles timed_out status."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "timed_out",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "timed_out")


class TestEmptyResultContract(unittest.TestCase):
    """Tests for empty result contract in role_wait."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_empty_result_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_gives_empty_result_after_retry_window(
        self, mock_lifecycle_wait
    ):
        """role_wait gives empty result after retry window."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_retries_final_answer_after_completed(
        self, mock_lifecycle_wait
    ):
        """role_wait retries final answer after completed."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                "summary": {"artifact_id": "art_summary", "artifact_type": "control_summary"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")
class TestPromptTemplatesIncludeFinalMarkers(unittest.TestCase):
    """Tests that all role prompt templates include required final markers."""

    def setUp(self):
        """Resolve the prompts directory path."""
        self.prompts_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts"
        )

    def test_scout_prompt_has_final_answer_contract_and_marker(self):
        """scout.md contains Final Answer Contract section and SCOUT_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "scout.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("SCOUT_STATUS: COMPLETE", content)

    def test_architect_prompt_has_final_answer_contract_and_marker(self):
        """architect.md contains Final Answer Contract section and ARCHITECT_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "architect.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("ARCHITECT_STATUS: COMPLETE", content)

    def test_coder_prompt_has_final_answer_contract_and_marker(self):
        """coder.md contains Final Answer Contract section and CODER_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "coder.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("CODER_STATUS: COMPLETE", content)

    def test_reviewer_prompt_has_final_answer_contract_and_marker(self):
        """reviewer.md contains Final Answer Contract section and REVIEWER_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "reviewer.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("REVIEWER_STATUS", content)

    def test_publisher_prompt_has_final_answer_contract_and_marker(self):
        """publisher.md contains Final Answer Contract section and PUBLISHER_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "publisher.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("PUBLISHER_STATUS: COMPLETE", content)

    def test_all_prompts_require_final_plain_text_answer(self):
        """All role prompts instruct the role to send a final plain-text answer."""
        prompt_files = [
            "scout.md",
            "architect.md",
            "coder.md",
            "reviewer.md",
            "publisher.md",
        ]
        for prompt_file in prompt_files:
            prompt_path = os.path.join(self.prompts_dir, prompt_file)
            content = Path(prompt_path).read_text(encoding="utf-8")
            self.assertIn(
                "final plain-text answer",
                content.lower(),
                f"{prompt_file} should instruct role to send a final plain-text answer",
            )


class TestRequestNonce(unittest.TestCase):
    """Tests for the request_nonce optional argument in role_wait."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_request_nonce_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_accepts_request_nonce(self, mock_lifecycle_wait):
        """role_wait accepts request_nonce argument."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        request_nonce = "2026-06-10T14:55:31Z"
        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            request_nonce=request_nonce,
        )

        self.assertEqual(result["request_nonce"], request_nonce)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_without_request_nonce_still_works(self, mock_lifecycle_wait):
        """Existing callers without request_nonce still work."""
        from mcp_agent.server import role_wait

        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
        )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("request_nonce", result)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_echoed_in_response(self, mock_lifecycle_wait):
        """When request_nonce is provided, the response includes the same exact string."""
        from mcp_agent.server import role_wait

        request_nonce = "2026-06-10T14:55:31Z"
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            request_nonce=request_nonce,
        )

        self.assertEqual(result["request_nonce"], request_nonce)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_response_nonce_generated_independently(self, mock_lifecycle_wait):
        """response_nonce is still generated independently of request_nonce."""
        from mcp_agent.server import role_wait

        request_nonce = "2026-06-10T14:55:31Z"
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            request_nonce=request_nonce,
        )

        self.assertIn("response_nonce", result)
        self.assertNotEqual(result["response_nonce"], request_nonce)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_not_in_artifacts_or_persisted_state(self, mock_lifecycle_wait):
        """request_nonce is not saved into artifacts or persisted run state."""
        from mcp_agent.server import role_wait

        request_nonce = "2026-06-10T14:55:31Z"
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            request_nonce=request_nonce,
        )

        # request_nonce should only appear at the top level of the response
        self.assertEqual(result["request_nonce"], request_nonce)
        # It should not appear nested in artifacts
        self.assertNotIn("request_nonce", result.get("artifacts", {}))
        primary = result.get("artifacts", {}).get("primary", {})
        self.assertNotIn("request_nonce", primary)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_preserved_across_statuses(self, mock_lifecycle_wait):
        """Running/completed/failed responses all preserve request_nonce when applicable."""
        from mcp_agent.server import role_wait

        request_nonce = "2026-06-10T14:55:31Z"

        # Test completed status
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }
        result = role_wait(
            role_run_id="run-001",
            request_nonce=request_nonce,
        )
        self.assertEqual(result.get("request_nonce"), request_nonce)

        # Test running status
        mock_lifecycle_wait.return_value = {
            "status": "running",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "timeout": True,
            "message": "Role is still running.",
        }
        result = role_wait(
            role_run_id="run-001",
            request_nonce=request_nonce,
        )
        self.assertEqual(result.get("request_nonce"), request_nonce)

        # Test failed status
        mock_lifecycle_wait.return_value = {
            "status": "failed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "error": {"type": "ExecutionError", "message": "Task failed", "retryable": True},
        }
        result = role_wait(
            role_run_id="run-001",
            request_nonce=request_nonce,
        )
        self.assertEqual(result.get("request_nonce"), request_nonce)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_different_request_nonces_return_corresponding_values(self, mock_lifecycle_wait):
        """Two calls with different request nonces return their corresponding nonce values."""
        from mcp_agent.server import role_wait

        nonce_a = "nonce-call-A"
        nonce_b = "nonce-call-B"

        def _make_response(status="completed"):
            return {
                "status": status,
                "role_run_id": "run-001",
                "run_id": "test-run-001",
                "role": "scout",
                "control_summary": {"status": "DONE"},
                "artifacts": {
                    "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                },
            }

        mock_lifecycle_wait.side_effect = [_make_response(), _make_response()]

        result_a = role_wait(
            role_run_id="run-001",
            request_nonce=nonce_a,
        )
        result_b = role_wait(
            role_run_id="run-001",
            request_nonce=nonce_b,
        )

        self.assertEqual(result_a["request_nonce"], nonce_a)
        self.assertEqual(result_b["request_nonce"], nonce_b)
        self.assertNotEqual(nonce_a, nonce_b)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_accepts_nested_object(self, mock_lifecycle_wait):
        """role_wait accepts a nested object as request_nonce and normalizes it."""
        import json

        from mcp_agent.server import role_wait

        nonce = {
            "summary": "Poll scout role completion",
            "request_nonce": "2026-06-10T16:45:30Z",
        }
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            request_nonce=nonce,
        )

        self.assertIn("request_nonce", result)
        self.assertIsInstance(result["request_nonce"], str)
        self.assertIn("2026-06-10T16:45:30Z", result["request_nonce"])
        # Verify it is valid JSON
        parsed = json.loads(result["request_nonce"])
        self.assertEqual(parsed["summary"], "Poll scout role completion")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_accepts_list(self, mock_lifecycle_wait):
        """role_wait accepts a list as request_nonce and normalizes it."""
        import json

        from mcp_agent.server import role_wait

        nonce = ["poll", "scout", "2026-06-10T16:45:30Z"]
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            request_nonce=nonce,
        )

        self.assertIn("request_nonce", result)
        self.assertIsInstance(result["request_nonce"], str)
        parsed = json.loads(result["request_nonce"])
        self.assertEqual(parsed, nonce)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_accepts_number(self, mock_lifecycle_wait):
        """role_wait accepts a number as request_nonce and normalizes it."""
        from mcp_agent.server import role_wait

        nonce = 42
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            request_nonce=nonce,
        )

        self.assertIn("request_nonce", result)
        self.assertIsInstance(result["request_nonce"], str)
        self.assertEqual(result["request_nonce"], "42")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_accepts_boolean(self, mock_lifecycle_wait):
        """role_wait accepts a boolean as request_nonce and normalizes it."""
        from mcp_agent.server import role_wait

        nonce = True
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            request_nonce=nonce,
        )

        self.assertIn("request_nonce", result)
        self.assertIsInstance(result["request_nonce"], str)
        self.assertEqual(result["request_nonce"], "true")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_request_nonce_normalized_is_json_serializable(self, mock_lifecycle_wait):
        """Normalized request_nonce is always valid JSON."""
        import json

        from mcp_agent.server import role_wait

        test_values = [
            "plain-string",
            42,
            3.14,
            True,
            False,
            None,
            {"key": "value"},
            ["a", "b", "c"],
        ]

        for nonce in test_values:
            mock_lifecycle_wait.return_value = {
                "status": "completed",
                "role_run_id": "run-001",
                "run_id": "test-run-001",
                "role": "scout",
                "control_summary": {"status": "DONE"},
                "artifacts": {
                    "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
                },
            }

            result = role_wait(
                role_run_id="run-001",
                timeout_seconds=300,
                poll_interval_seconds=15,
                request_nonce=nonce,
            )

            if nonce is not None:
                self.assertIn("request_nonce", result)
                self.assertIsInstance(result["request_nonce"], str)
                # For non-string types, verify the normalized output is valid JSON
                if not isinstance(nonce, str):
                    json.loads(result["request_nonce"])  # raises if not valid JSON
            else:
                # None → omitted from response
                self.assertNotIn("request_nonce", result)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_response_nonce_independent_of_request_nonce_with_object(self, mock_lifecycle_wait):
        """response_nonce is still present and independent when request_nonce is an object."""
        from mcp_agent.server import role_wait

        nonce = {
            "summary": "Poll scout role completion",
            "request_nonce": "2026-06-10T16:45:30Z",
        }
        mock_lifecycle_wait.return_value = {
            "status": "completed",
            "role_run_id": "run-001",
            "run_id": "test-run-001",
            "role": "scout",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {"artifact_id": "art_primary", "artifact_type": "scout_report"},
            },
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            request_nonce=nonce,
        )

        self.assertIn("request_nonce", result)
        self.assertIn("response_nonce", result)
        self.assertNotEqual(result["response_nonce"], result["request_nonce"])


if __name__ == "__main__":
    unittest.main()

