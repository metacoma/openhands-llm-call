#!/usr/bin/env python3
"""Tests for the new public MCP API: shttp_role_list + shttp_role_call.

These tests verify the migration from legacy+v2 tools to the minimal
public surface consisting of exactly two tools.
"""

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase, main as unittest_main
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_agent.artifact_store import ArtifactStore, _generate_artifact_id
from mcp_agent import role_lifecycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmp_state_dir() -> Path:
    """Create a temporary state directory for tests."""
    d = Path(tempfile.mkdtemp(prefix="test_shttp_"))
    return d


def _write_role_config(state_dir: Path) -> Path:
    """Write a minimal roles.yaml into *state_dir* and return its path."""
    cfg = state_dir / "roles.yaml"
    cfg.write_text(
        textwrap.dedent("""\
        roles:
          scout:
            description: "Read-only investigator"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/scout.md"
            readonly: true
            timeout_minutes: 60
            requires_artifacts: []
            output_artifact: scout_report
          architect:
            description: "Implementation planner"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/architect.md"
            readonly: true
            timeout_minutes: 90
            requires_artifacts:
              - scout_report
            output_artifact: architect_plan
        """),
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# Tests — artifact_id generation & store
# ---------------------------------------------------------------------------

class TestArtifactIdGeneration(TestCase):
    """Test _generate_artifact_id and ArtifactStore.get_content_by_id."""

    def test_generate_artifact_id_format(self):
        aid = _generate_artifact_id("run-abc", "scout", 1, "scout_report")
        self.assertTrue(aid.startswith("art_"))
        self.assertIn("run-abc", aid)
        self.assertIn("scout", aid)
        self.assertIn("scout_report", aid)

    def test_generate_artifact_id_unique(self):
        aid1 = _generate_artifact_id("run-abc", "scout", 1, "scout_report")
        aid2 = _generate_artifact_id("run-abc", "scout", 2, "scout_report")
        self.assertNotEqual(aid1, aid2)

    def test_save_returns_artifact_id(self):
        state_dir = _make_tmp_state_dir()
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(state_dir)
        store = ArtifactStore()
        meta = store.save(
            run_id="test-run-001",
            role_run_id="test-run-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="Scout report content",
        )
        self.assertIn("artifact_id", meta)
        self.assertIn("artifact_type", meta)
        self.assertEqual(meta["artifact_type"], "scout_report")
        self.assertEqual(meta["role"], "scout")
        self.assertEqual(meta["run_id"], "test-run-001")
        self.assertIn("size_bytes", meta)
        self.assertTrue(meta["size_bytes"] > 0)

    def test_get_content_by_id(self):
        state_dir = _make_tmp_state_dir()
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(state_dir)
        store = ArtifactStore()
        meta = store.save(
            run_id="test-run-002",
            role_run_id="test-run-002-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="Scout report content for get_content_by_id",
        )
        aid = meta["artifact_id"]
        content = store.get_content_by_id(aid)
        self.assertEqual(content, "Scout report content for get_content_by_id")

    def test_get_content_by_id_not_found(self):
        state_dir = _make_tmp_state_dir()
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(state_dir)
        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get_content_by_id("art_nonexistent")


# ---------------------------------------------------------------------------
# Tests — resolve_input_artifacts helper
# ---------------------------------------------------------------------------

class TestResolveInputArtifacts(TestCase):
    """Test the resolve_input_artifacts normalization helper."""

    def test_list_format(self):
        result = role_lifecycle.resolve_input_artifacts([
            {"artifact_id": "art_xxx", "artifact_type": "scout_report"},
            {"artifact_id": "art_yyy", "artifact_type": "architect_plan"},
        ])
        self.assertEqual(result["scout_report"], "art_xxx")
        self.assertEqual(result["architect_plan"], "art_yyy")

    def test_dict_format(self):
        result = role_lifecycle.resolve_input_artifacts({
            "scout_report": "art_xxx",
            "architect_plan": "art_yyy",
        })
        self.assertEqual(result["scout_report"], "art_xxx")
        self.assertEqual(result["architect_plan"], "art_yyy")

    def test_none_input(self):
        result = role_lifecycle.resolve_input_artifacts(None)
        self.assertEqual(result, {})

    def test_empty_list(self):
        result = role_lifecycle.resolve_input_artifacts([])
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Tests — role_call_impl response schema (mocked)
# ---------------------------------------------------------------------------

class TestRoleCallImplResponse(TestCase):
    """Test that role_call_impl returns the correct response schema."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._poll_task_status")
    def test_returns_control_summary(self, mock_poll, mock_start):
        """shttp_role_call returns control_summary."""
        mock_start.return_value = {"task_id": "task-1", "conversation_id": "conv-1"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "scout",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-003"},
        )

        self.assertIn("control_summary", result)
        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._poll_task_status")
    def test_returns_artifact_id(self, mock_poll, mock_start):
        """shttp_role_call returns artifacts.primary.artifact_id."""
        mock_start.return_value = {"task_id": "task-2", "conversation_id": "conv-2"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "scout",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-004"},
        )

        self.assertIn("artifacts", result)
        self.assertIn("primary", result["artifacts"])
        self.assertIn("artifact_id", result["artifacts"]["primary"])
        self.assertIn("artifact_type", result["artifacts"]["primary"])
        self.assertIn("created_by", result["artifacts"]["primary"])

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._poll_task_status")
    def test_no_full_result_in_response(self, mock_poll, mock_start):
        """Response does NOT contain full_result."""
        mock_start.return_value = {"task_id": "task-3", "conversation_id": "conv-3"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "scout",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-005"},
        )

        self.assertNotIn("full_result", result)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._poll_task_status")
    def test_no_artifact_content_in_response(self, mock_poll, mock_start):
        """Response does NOT contain artifact content."""
        mock_start.return_value = {"task_id": "task-4", "conversation_id": "conv-4"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "scout",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-006"},
        )

        # Check that no key contains artifact content
        artifacts = result.get("artifacts", {})
        for key, val in artifacts.items():
            if isinstance(val, dict):
                self.assertNotIn("content", val)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._poll_task_status")
    def test_no_artifact_path_in_public_response(self, mock_poll, mock_start):
        """Response does NOT contain artifact_path in public mode."""
        mock_start.return_value = {"task_id": "task-5", "conversation_id": "conv-5"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "scout",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-007"},
        )

        artifacts = result.get("artifacts", {})
        for key, val in artifacts.items():
            if isinstance(val, dict):
                self.assertNotIn("artifact_path", val)


# ---------------------------------------------------------------------------
# Tests — shttp_role_call MCP tool (mocked)
# ---------------------------------------------------------------------------

class TestShttpRoleCallTool(TestCase):
    """Test the shttp_role_call MCP tool wrapper."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)

    @patch("mcp_agent.role_lifecycle.role_call_impl")
    def test_call_delegates_to_role_call_impl(self, mock_impl):
        """shttp_role_call delegates to role_call_impl."""
        mock_impl.return_value = {
            "role_run_id": "test-run-008-scout-1",
            "run_id": "test-run-008",
            "role": "scout",
            "status": "completed",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {
                    "artifact_id": "art_xxx",
                    "artifact_type": "scout_report",
                    "created_by": "scout",
                },
                "summary": {
                    "artifact_id": "art_yyy",
                    "artifact_type": "control_summary",
                    "created_by": "scout",
                },
            },
        }

        # Import the tool function
        from mcp_agent.server import shttp_role_call

        result = shttp_role_call(
            role="scout",
            user_task="Test task",
            input_artifacts=[],
            metadata={"repository": "https://github.com/test/repo"},
        )

        mock_impl.assert_called_once()
        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle.role_call_impl")
    def test_call_with_list_input_artifacts(self, mock_impl):
        """shttp_role_call accepts input_artifacts as list of objects."""
        mock_impl.return_value = {
            "role_run_id": "test-run-009-architect-1",
            "run_id": "test-run-009",
            "role": "architect",
            "status": "completed",
            "control_summary": {"status": "DONE"},
            "artifacts": {
                "primary": {
                    "artifact_id": "art_zzz",
                    "artifact_type": "architect_plan",
                    "created_by": "architect",
                },
                "summary": {
                    "artifact_id": "art_www",
                    "artifact_type": "control_summary",
                    "created_by": "architect",
                },
            },
        }

        from mcp_agent.server import shttp_role_call

        result = shttp_role_call(
            role="architect",
            user_task="Plan implementation",
            input_artifacts=[
                {"artifact_id": "art_scout_xxx", "artifact_type": "scout_report"},
            ],
            metadata={"repository": "https://github.com/test/repo"},
        )

        # Verify the normalized dict was passed to role_call_impl
        call_kwargs = mock_impl.call_args
        input_arts = call_kwargs[1]["input_artifacts"]
        self.assertEqual(input_arts["scout_report"], "art_scout_xxx")
        self.assertEqual(result["status"], "completed")


# ---------------------------------------------------------------------------
# Tests — shttp_role_list MCP tool
# ---------------------------------------------------------------------------

class TestShttpRoleListTool(TestCase):
    """Test the shttp_role_list MCP tool."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_returns_roles_list(self):
        """shttp_role_list returns a roles list."""
        from mcp_agent.server import shttp_role_list

        result = shttp_role_list()
        self.assertIn("roles", result)
        self.assertIsInstance(result["roles"], list)
        self.assertGreater(len(result["roles"]), 0)

    def test_role_has_required_fields(self):
        """Each role has name, readonly, requires_artifacts, output_artifact_type."""
        from mcp_agent.server import shttp_role_list

        result = shttp_role_list()
        for role in result["roles"]:
            self.assertIn("name", role)
            self.assertIn("readonly", role)
            self.assertIn("requires_artifacts", role)
            self.assertIn("output_artifact_type", role)

    def test_scout_has_no_required_artifacts(self):
        """Scout role has no required artifacts."""
        from mcp_agent.server import shttp_role_list

        result = shttp_role_list()
        scout = next((r for r in result["roles"] if r["name"] == "scout"), None)
        self.assertIsNotNone(scout)
        self.assertEqual(scout["requires_artifacts"], [])

    def test_architect_requires_scout_report(self):
        """Architect role requires scout_report."""
        from mcp_agent.server import shttp_role_list

        result = shttp_role_list()
        architect = next((r for r in result["roles"] if r["name"] == "architect"), None)
        self.assertIsNotNone(architect)
        self.assertIn("scout_report", architect["requires_artifacts"])


# ---------------------------------------------------------------------------
# Tests — Legacy tools NOT exposed
# ---------------------------------------------------------------------------

def _get_public_tool_names():
    """Return a set of public MCP tool names from the server module."""
    from mcp_agent.server import MCP
    tools = MCP._tool_manager.list_tools()
    return {t.name for t in tools}


class TestLegacyToolsHidden(TestCase):
    """Test that legacy tools are NOT decorated with @MCP.tool()."""

    def test_role_start_not_decorated(self):
        """role_start is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("role_start", tool_names,
                         "role_start should NOT be a public MCP tool")

    def test_role_wait_not_decorated(self):
        """role_wait is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("role_wait", tool_names,
                         "role_wait should NOT be a public MCP tool")

    def test_role_status_not_decorated(self):
        """role_status is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("role_status", tool_names,
                         "role_status should NOT be a public MCP tool")

    def test_role_result_not_decorated(self):
        """role_result is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("role_result", tool_names,
                         "role_result should NOT be a public MCP tool")

    def test_artifact_get_not_decorated(self):
        """artifact_get is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("artifact_get", tool_names,
                         "artifact_get should NOT be a public MCP tool")

    def test_shttp_role_start_v2_not_decorated(self):
        """shttp_role_start_v2 is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("shttp_role_start_v2", tool_names,
                         "shttp_role_start_v2 should NOT be a public MCP tool")

    def test_shttp_role_wait_v2_not_decorated(self):
        """shttp_role_wait_v2 is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("shttp_role_wait_v2", tool_names,
                         "shttp_role_wait_v2 should NOT be a public MCP tool")

    def test_shttp_role_result_v2_not_decorated(self):
        """shttp_role_result_v2 is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("shttp_role_result_v2", tool_names,
                         "shttp_role_result_v2 should NOT be a public MCP tool")


# ---------------------------------------------------------------------------
# Tests — Public tools ARE exposed
# ---------------------------------------------------------------------------

class TestPublicToolsExposed(TestCase):
    """Test that new public tools ARE decorated with @MCP.tool()."""

    def test_shttp_role_call_is_exposed(self):
        """shttp_role_call IS a public MCP tool."""
        tool_names = _get_public_tool_names()
        self.assertIn("shttp_role_call", tool_names,
                      "shttp_role_call SHOULD be a public MCP tool")

    def test_shttp_role_list_is_exposed(self):
        """shttp_role_list IS a public MCP tool."""
        tool_names = _get_public_tool_names()
        self.assertIn("shttp_role_list", tool_names,
                      "shttp_role_list SHOULD be a public MCP tool")


# ---------------------------------------------------------------------------
# Tests — Backward-compatible alias
# ---------------------------------------------------------------------------

class TestBackwardCompat(TestCase):
    """Test backward compatibility."""

    def test_start_role_v2_impl_alias_exists(self):
        """start_role_call_v2_impl alias exists for backward compat."""
        self.assertTrue(hasattr(role_lifecycle, 'start_role_v2_impl'))
        self.assertEqual(role_lifecycle.start_role_v2_impl, role_lifecycle.role_call_impl)


if __name__ == "__main__":
    unittest_main()
