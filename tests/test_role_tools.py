#!/usr/bin/env python3
"""Tests for role tools (mcp_agent.role_tools)."""

import json
import os
import shutil
import tempfile
import unittest
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
        # Reset module state
        import mcp_agent.server as server_mod
        server_mod._store = None

    def test_role_list_returns_five_roles(self):
        """role_list returns five roles."""
        from mcp_agent.server import role_list

        result = role_list()
        self.assertIn("roles", result)
        self.assertEqual(len(result["roles"]), 5)

    def test_role_list_contains_scout(self):
        """role_list includes scout role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("scout", names)

    def test_role_list_contains_architect(self):
        """role_list includes architect role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("architect", names)

    def test_role_list_contains_coder(self):
        """role_list includes coder role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("coder", names)

    def test_role_list_contains_reviewer(self):
        """role_list includes reviewer role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("reviewer", names)

    def test_role_list_contains_publisher(self):
        """role_list includes publisher role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("publisher", names)


class TestRoleStartTool(unittest.TestCase):
    """Test role_start MCP tool validation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_start_")
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
    def test_role_start_rejects_unknown_role(self, mock_store):
        """role_start rejects unknown role."""
        from mcp_agent.server import role_start

        result = role_start(
            role="qa",
            user_task="Test task",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRole")
        self.assertIn("qa", result["error"]["message"])
        self.assertIn("scout", result["error"]["message"])

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_start_rejects_missing_required_artifact(self, mock_store):
        """role_start rejects missing required artifact for architect."""
        from mcp_agent.server import role_start

        result = role_start(
            role="architect",
            user_task="Plan implementation",
            artifacts={},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("scout_report", result["error"]["message"])

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_start_accepts_scout_without_artifacts(self, mock_store):
        """role_start accepts scout without artifacts (none required)."""
        from mcp_agent.server import role_start

        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-id",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-mock-123",
            }
            result = role_start(
                role="scout",
                user_task="Investigate repo",
                repo="https://github.com/example/repo",
                base_branch="main",
                context={},
                artifacts={},
            )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertEqual(result["role"], "scout")


class TestRoleStatusTool(unittest.TestCase):
    """Test role_status MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_status_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_role_status_unknown_role_run_id(self):
        """role_status returns error for unknown role_run_id."""
        from mcp_agent.server import role_status

        result = role_status(role_run_id="nonexistent-run-id")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")


class TestRoleResultTool(unittest.TestCase):
    """Test role_result MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_result_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_role_result_unknown_role_run_id(self):
        """role_result returns error for unknown role_run_id."""
        from mcp_agent.server import role_result

        result = role_result(role_run_id="nonexistent-run-id")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")


if __name__ == "__main__":
    unittest.main()
