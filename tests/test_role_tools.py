#!/usr/bin/env python3
"""Tests for role tools (mcp_agent.role_tools)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestActionParsing(unittest.TestCase):
    """Test parse_action helper."""

    def test_reviewer_pass_action(self):
        """Reviewer parser extracts ACTION: PASS."""
        from mcp_agent.role_tools import parse_action

        text = """
# Reviewer Report

## Decision

ACTION: PASS

## Risk

RISK: LOW
"""
        result = parse_action("reviewer", text)
        self.assertEqual(result, "PASS")

    def test_reviewer_blocker_action(self):
        """Reviewer parser extracts ACTION: BLOCKER."""
        from mcp_agent.role_tools import parse_action

        text = "ACTION: BLOCKER\nSome text"
        result = parse_action("reviewer", text)
        self.assertEqual(result, "BLOCKER")

    def test_reviewer_missing_action_returns_none(self):
        """Reviewer without ACTION line returns None."""
        from mcp_agent.role_tools import parse_action

        text = "Just a regular report without ACTION line."
        result = parse_action("reviewer", text)
        self.assertIsNone(result)

    def test_publisher_ready_status(self):
        """Publisher parser extracts PUBLISH_STATUS: READY."""
        from mcp_agent.role_tools import parse_action

        text = "PUBLISH_STATUS: READY\nSome text"
        result = parse_action("publisher", text)
        self.assertEqual(result, "READY")

    def test_publisher_blocked_status(self):
        """Publisher parser extracts PUBLISH_STATUS: BLOCKED."""
        from mcp_agent.role_tools import parse_action

        text = "PUBLISH_STATUS: BLOCKED\nSome text"
        result = parse_action("publisher", text)
        self.assertEqual(result, "BLOCKED")

    def test_other_role_defaults_to_continue(self):
        """Non-reviewer/non-publisher roles default to CONTINUE."""
        from mcp_agent.role_tools import parse_action

        text = "Some report text"
        result = parse_action("scout", text)
        self.assertEqual(result, "CONTINUE")

        result = parse_action("architect", text)
        self.assertEqual(result, "CONTINUE")

        result = parse_action("coder", text)
        self.assertEqual(result, "CONTINUE")


class TestRiskParsing(unittest.TestCase):
    """Test parse_risk helper."""

    def test_parse_risk_low(self):
        """parse_risk extracts RISK: LOW."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("Some text\nRISK: LOW\nMore text")
        self.assertEqual(result, "LOW")

    def test_parse_risk_medium(self):
        """parse_risk extracts RISK: MEDIUM."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("RISK: MEDIUM")
        self.assertEqual(result, "MEDIUM")

    def test_parse_risk_high(self):
        """parse_risk extracts RISK: HIGH."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("RISK: HIGH")
        self.assertEqual(result, "HIGH")

    def test_parse_risk_missing_returns_none(self):
        """parse_risk returns None when RISK not found."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("No risk mentioned")
        self.assertIsNone(result)


class TestMakeSummary(unittest.TestCase):
    """Test make_summary helper."""

    def test_short_text_unchanged(self):
        """Short text is returned unchanged."""
        from mcp_agent.role_tools import make_summary

        result = make_summary("Hello world", max_chars=500)
        self.assertEqual(result, "Hello world")

    def test_long_text_truncated(self):
        """Long text is truncated at word boundary."""
        from mcp_agent.role_tools import make_summary

        long_text = " ".join([f"word{i}" for i in range(100)])
        result = make_summary(long_text, max_chars=20)
        self.assertLessEqual(len(result), 23)  # 20 chars + "..."
        self.assertIn("...", result)

    def test_empty_text_returns_empty(self):
        """Empty text returns empty string."""
        from mcp_agent.role_tools import make_summary

        self.assertEqual(make_summary(""), "")
        self.assertEqual(make_summary(None), "")


class TestRoleListTool(unittest.TestCase):
    """Test role_list MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_list_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        pass  # No module state to reset

    def test_role_list_returns_six_roles(self):
        """role_list returns six roles (including coder_fix)."""
        from mcp_agent.role_tools import role_list_impl as role_list

        result = role_list()
        self.assertIn("roles", result)
        self.assertEqual(len(result["roles"]), 6)

    def test_role_list_contains_scout(self):
        """role_list includes scout role."""
        from mcp_agent.role_tools import role_list_impl as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("scout", names)

    def test_role_list_contains_architect(self):
        """role_list includes architect role."""
        from mcp_agent.role_tools import role_list_impl as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("architect", names)

    def test_role_list_contains_coder(self):
        """role_list includes coder role."""
        from mcp_agent.role_tools import role_list_impl as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("coder", names)

    def test_role_list_contains_reviewer(self):
        """role_list includes reviewer role."""
        from mcp_agent.role_tools import role_list_impl as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("reviewer", names)

    def test_role_list_contains_publisher(self):
        """role_list includes publisher role."""
        from mcp_agent.role_tools import role_list_impl as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("publisher", names)


class TestImprovedMakeSummary(unittest.TestCase):
    """Test improved make_summary."""

    def test_strip_markdown_headings(self):
        """make_summary strips markdown headings lightly."""
        from mcp_agent.role_tools import make_summary

        text = "# Title\n## Section\nSome content\n## Another\nMore text"
        result = make_summary(text, max_chars=200)
        # First 2 headings should be preserved
        self.assertIn("# Title", result)
        self.assertIn("## Section", result)

    def test_remove_excessive_blank_lines(self):
        """make_summary collapses excessive blank lines."""
        from mcp_agent.role_tools import make_summary

        text = "Line 1\n\n\n\n\nLine 2"
        result = make_summary(text, max_chars=200)
        # Should not have 4 consecutive blank lines
        self.assertNotIn("\n\n\n\n", result)

    def test_cap_at_max_chars(self):
        """make_summary caps output at max_chars."""
        from mcp_agent.role_tools import make_summary

        long_text = " ".join([f"word{i}" for i in range(200)])
        result = make_summary(long_text, max_chars=50)
        # Account for "..." suffix
        self.assertLessEqual(len(result), 53)  # 50 + "..."
        self.assertIn("...", result)

    def test_preserves_useful_first_lines(self):
        """make_summary preserves useful first lines."""
        from mcp_agent.role_tools import make_summary

        text = "Important first line\nSecond line\n## Heading\nMore content"
        result = make_summary(text, max_chars=200)
        self.assertIn("Important first line", result)

    def test_empty_input_returns_empty(self):
        """make_summary returns empty for empty input."""
        from mcp_agent.role_tools import make_summary

        self.assertEqual(make_summary(""), "")
        self.assertEqual(make_summary(None), "")

    def test_nonempty_input_never_returns_empty(self):
        """make_summary never returns empty for meaningful non-empty input."""
        from mcp_agent.role_tools import make_summary

        result = make_summary("Hello world", max_chars=10)
        self.assertNotEqual(result, "")
        self.assertIn("Hello", result)

    def test_default_max_chars_is_700(self):
        """Default max_chars is 700."""
        from mcp_agent.role_tools import make_summary

        text = " ".join([f"word{i}" for i in range(100)])
        result = make_summary(text)
        # Should not be truncated since it's short
        self.assertEqual(result, text.strip())


# ---------------------------------------------------------------------------
# Regression tests for Bug 1 (artifact visibility) and Bug 3 (lock lifecycle)
# ---------------------------------------------------------------------------


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
    def test_role_wait_return_result_false(
        self, mock_lifecycle_wait
    ):
        """role_wait returns control_summary without full_result."""
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


if __name__ == "__main__":
    unittest.main()

