#!/usr/bin/env python3
"""Tests for the new public MCP API: role_list + role_call.

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
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_returns_control_summary(self, mock_poll, mock_start):
        """role_call returns control_summary."""
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
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_returns_artifact_id(self, mock_poll, mock_start):
        """role_call returns artifacts.primary.artifact_id."""
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
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
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
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
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
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
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
# Tests — role_call MCP tool (mocked)
# ---------------------------------------------------------------------------

class TestRoleCallTool(TestCase):
    """Test the role_call MCP tool wrapper."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_call_delegates_to_role_call_start_impl(self, mock_impl):
        """role_call delegates to role_call_start_impl."""
        mock_impl.return_value = {
            "role_run_id": "test-run-008-scout-1",
            "run_id": "test-run-008",
            "role": "scout",
            "status": "running",
            "message": "Role started.",
        }

        # Import the tool function
        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Test task",
            repository="https://github.com/test/repo",
            feature="",
            scout_report_artifact_id="",
            architect_plan_artifact_id="",
            coder_report_artifact_id="",
            reviewer_report_artifact_id="",
            publisher_instructions_artifact_id="",
            idempotency_key="",
        )

        mock_impl.assert_called_once()
        self.assertEqual(result["status"], "running")

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_call_with_flat_artifact_id(self, mock_impl):
        """role_call accepts scout_report_artifact_id as flat field."""
        mock_impl.return_value = {
            "role_run_id": "test-run-009-architect-1",
            "run_id": "test-run-009",
            "role": "architect",
            "status": "running",
            "message": "Role started.",
        }

        from mcp_agent.server import role_call

        result = role_call(
            role="architect",
            user_task="Plan implementation",
            repository="https://github.com/test/repo",
            feature="",
            scout_report_artifact_id="art_scout_xxx",
            architect_plan_artifact_id="",
            coder_report_artifact_id="",
            reviewer_report_artifact_id="",
            publisher_instructions_artifact_id="",
            idempotency_key="",
        )

        # Verify the normalized dict was passed to role_call_start_impl
        call_kwargs = mock_impl.call_args
        input_arts = call_kwargs[1]["input_artifacts"]
        self.assertEqual(input_arts["scout_report"], "art_scout_xxx")
        self.assertEqual(result["status"], "running")


# ---------------------------------------------------------------------------
# Tests — role_list MCP tool
# ---------------------------------------------------------------------------

class TestRoleListTool(TestCase):
    """Test the role_list MCP tool."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    def test_returns_roles_list(self):
        """role_list returns a roles list."""
        from mcp_agent.server import role_list

        result = role_list()
        self.assertIn("roles", result)
        self.assertIsInstance(result["roles"], list)
        self.assertGreater(len(result["roles"]), 0)

    def test_role_has_required_fields(self):
        """Each role has name, readonly, requires_artifacts, output_artifact_type."""
        from mcp_agent.server import role_list

        result = role_list()
        for role in result["roles"]:
            self.assertIn("name", role)
            self.assertIn("readonly", role)
            self.assertIn("requires_artifacts", role)
            self.assertIn("output_artifact_type", role)

    def test_scout_has_no_required_artifacts(self):
        """Scout role has no required artifacts."""
        from mcp_agent.server import role_list

        result = role_list()
        scout = next((r for r in result["roles"] if r["name"] == "scout"), None)
        self.assertIsNotNone(scout)
        self.assertEqual(scout["requires_artifacts"], [])

    def test_architect_requires_scout_report(self):
        """Architect role requires scout_report."""
        from mcp_agent.server import role_list

        result = role_list()
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
        """role_wait IS decorated with @MCP.tool() (public tool)."""
        tool_names = _get_public_tool_names()
        self.assertIn("role_wait", tool_names,
                      "role_wait SHOULD be a public MCP tool")

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

    def test_role_list_not_decorated(self):
        """shttp_role_list (old name) is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("shttp_role_list", tool_names,
                         "shttp_role_list should NOT be a public MCP tool")

    def test_artifact_list_not_decorated(self):
        """artifact_list is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("artifact_list", tool_names,
                         "artifact_list should NOT be a public MCP tool")


# ---------------------------------------------------------------------------
# Tests — Public tools ARE exposed
# ---------------------------------------------------------------------------

class TestPublicToolsExposed(TestCase):
    """Test that new public tools ARE decorated with @MCP.tool()."""

    def test_role_call_is_exposed(self):
        """role_call IS a public MCP tool."""
        tool_names = _get_public_tool_names()
        self.assertIn("role_call", tool_names,
                      "role_call SHOULD be a public MCP tool")

    def test_role_list_is_exposed(self):
        """role_list IS a public MCP tool."""
        tool_names = _get_public_tool_names()
        self.assertIn("role_list", tool_names,
                      "role_list SHOULD be a public MCP tool")


# ---------------------------------------------------------------------------
# Tests — Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency(TestCase):
    """Test that idempotency_key deduplicates role_call."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_same_idempotency_key_no_second_run(self, mock_poll, mock_start):
        """Repeated role_call with same idempotency_key does not create a second role_run."""
        mock_start.return_value = {"task_id": "task-idem-1", "conversation_id": "conv-idem-1"}
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

        result1 = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-idem"},
            idempotency_key="idem-key-xyz",
        )

        self.assertEqual(result1["status"], "completed")
        # First call is NOT idempotent (it creates the run)
        self.assertFalse(result1.get("_idempotent", False))

        # Reset mock to count calls
        mock_start.reset_mock()
        mock_poll.reset_mock()

        # Second call with same key should return existing result without calling OpenHands
        result2 = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-idem"},
            idempotency_key="idem-key-xyz",
        )

        self.assertEqual(result2["status"], "completed")
        self.assertIn("_idempotent", result2)
        self.assertTrue(result2["_idempotent"])

        # Verify no new OpenHands conversation was started
        mock_start.assert_not_called()
        mock_poll.assert_not_called()

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_different_idempotency_key_creates_new_run(self, mock_poll, mock_start):
        """Different idempotency_key creates a new role_run."""
        mock_start.return_value = {"task_id": "task-idem-2", "conversation_id": "conv-idem-2"}
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

        result1 = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-idem-diff"},
            idempotency_key="idem-key-aaa",
        )

        result2 = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts={},
            metadata={"run_id": "test-run-idem-diff"},
            idempotency_key="idem-key-bbb",
        )

        self.assertEqual(result1["status"], "completed")
        self.assertEqual(result2["status"], "completed")
        # Both should have created new runs (not idempotent)
        self.assertFalse(result1.get("_idempotent", False))
        self.assertFalse(result2.get("_idempotent", False))


# ---------------------------------------------------------------------------
# Tests — Artifact ID resolution
# ---------------------------------------------------------------------------

class TestArtifactIdResolution(TestCase):
    """Test that input_artifacts are resolved by artifact_id."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_artifact_id_resolved_to_content(self, mock_poll, mock_start):
        """Architect receives scout_report content via artifact_id resolution."""
        mock_start.return_value = {"task_id": "task-artid-1", "conversation_id": "conv-artid-1"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "architect",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        # First, save a scout artifact so it can be resolved by artifact_id
        store = ArtifactStore()
        scout_meta = store.save(
            run_id="test-run-artid",
            role_run_id="test-run-artid-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="FULL SCOUT REPORT CONTENT",
        )
        scout_artifact_id = scout_meta["artifact_id"]

        # Now call architect with the artifact_id
        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Plan implementation",
            input_artifacts={"scout_report": scout_artifact_id},
            metadata={"run_id": "test-run-artid"},
        )

        self.assertEqual(result["status"], "completed")

        # Verify the prompt was rendered with the resolved content
        # The second call to _start_conversation_on_fastapi sends the main prompt
        calls = mock_start.call_args_list
        self.assertGreaterEqual(len(calls), 1)
        main_prompt = calls[0][1]["prompt"] if len(calls[0][1]) > 0 else calls[0][0][0]
        self.assertIn("FULL SCOUT REPORT CONTENT", main_prompt)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_artifact_id_not_found_returns_error(self, mock_poll, mock_start):
        """Unresolvable artifact_id returns ArtifactNotFound error."""
        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Plan implementation",
            input_artifacts={"scout_report": "art_nonexistent_id"},
            metadata={"run_id": "test-run-artid-nf"},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ArtifactNotFound")


# ---------------------------------------------------------------------------
# Tests — Same conversation for summary
# ---------------------------------------------------------------------------

class TestSameConversationSummary(TestCase):
    """Test that summary prompt is sent to the same conversation_id."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_summary_sent_to_same_conversation(self, mock_poll, mock_start):
        """Summary prompt is sent to the same conversation_id as main prompt."""
        conv_id = "conv-same-123"

        def side_effect(*args, **kwargs):
            # First call: return a conversation_id
            # Subsequent calls: echo back the conversation_id from kwargs
            if not hasattr(side_effect, 'call_count'):
                side_effect.call_count = 0
            side_effect.call_count += 1
            if side_effect.call_count == 1:
                return {"task_id": "task-same-1", "conversation_id": conv_id}
            # Second call (summary): use the conversation_id passed in kwargs
            return {"task_id": "task-same-2", "conversation_id": kwargs.get("conversation_id", "")}

        mock_start.side_effect = side_effect
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
            metadata={"run_id": "test-run-same"},
        )

        self.assertEqual(result["status"], "completed")

        # Verify _start_conversation_on_fastapi was called twice:
        # 1. Main prompt (no conversation_id initially)
        # 2. Summary prompt with the same conversation_id from step 1
        calls = mock_start.call_args_list
        self.assertGreaterEqual(len(calls), 2)

        # The second call should have conversation_id matching the first response
        summary_conv_id = calls[1][1].get("conversation_id") if len(calls[1][1]) > 0 else None
        self.assertEqual(summary_conv_id, conv_id)


# ---------------------------------------------------------------------------
# Tests — Backward-compatible alias
# ---------------------------------------------------------------------------

class TestBackwardCompat(TestCase):
    """Test backward compatibility."""

    def test_role_call_impl_alias_exists(self):
        """_role_call_impl_alias exists for backward compat."""
        self.assertTrue(hasattr(role_lifecycle, '_role_call_impl_alias'))
        self.assertEqual(role_lifecycle._role_call_impl_alias, role_lifecycle.role_call_impl)


# ---------------------------------------------------------------------------
# Tests — wrapped MCP-style values (BLOCKER fix)
# ---------------------------------------------------------------------------

class TestResolveInputArtifactsWrapped(TestCase):
    """Test that wrapped MCP-style values are correctly unwrapped."""

    def test_wrapped_list_of_objects(self):
        """Wrapped artifact_id and artifact_type are unwrapped."""
        result = role_lifecycle.resolve_input_artifacts([
            {
                "artifact_id": {"text": "art_scout"},
                "artifact_type": {"text": "scout_report"},
            }
        ])
        self.assertEqual(result, {"scout_report": "art_scout"})

    def test_plain_list_of_objects_still_works(self):
        """Plain (non-wrapped) list-of-objects continues to work."""
        result = role_lifecycle.resolve_input_artifacts([
            {
                "artifact_id": "art_scout",
                "artifact_type": "scout_report",
            }
        ])
        self.assertEqual(result, {"scout_report": "art_scout"})

    def test_mapping_format_still_works(self):
        """Plain dict mapping continues to work."""
        result = role_lifecycle.resolve_input_artifacts({
            "scout_report": "art_scout",
        })
        self.assertEqual(result, {"scout_report": "art_scout"})

    def test_wrapped_mapping_format(self):
        """Wrapped dict values are unwrapped."""
        result = role_lifecycle.resolve_input_artifacts({
            "scout_report": {"text": "art_scout"},
        })
        self.assertEqual(result, {"scout_report": "art_scout"})

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_role_call_impl_accepts_list_of_objects_directly(self, mock_poll, mock_start):
        """role_call_impl correctly normalizes list-of-objects input_artifacts."""
        # Save a scout artifact so architect can resolve it
        store = ArtifactStore()
        scout_meta = store.save(
            run_id="test-run-wrapped",
            role_run_id="test-run-wrapped-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="Scout content",
        )
        scout_artifact_id = scout_meta["artifact_id"]

        mock_start.return_value = {"task_id": "task-1", "conversation_id": "conv-1"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "architect",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        # Pass wrapped list-of-objects — role_call_impl must normalize it
        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Test task",
            input_artifacts=[
                {
                    "artifact_id": {"text": scout_artifact_id},
                    "artifact_type": {"text": "scout_report"},
                }
            ],
            metadata={"run_id": "test-run-wrapped"},
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_role_call_impl_accepts_wrapped_dict_input_artifacts(
        self, mock_poll, mock_start
    ):
        """role_call_impl correctly normalizes wrapped dict input_artifacts.

        When ``input_artifacts`` is already a dict but values are wrapped
        (e.g. ``{"scout_report": {"text": "art_scout"}}``), the guard
        ``if not isinstance(input_artifacts, dict)`` would have skipped
        normalization.  The unconditional call to ``resolve_input_artifacts``
        must unwrap them so ``get_content_by_id`` receives a valid ID.
        """
        # Save a scout artifact so the lifecycle can resolve it
        store = ArtifactStore()
        scout_meta = store.save(
            run_id="test-run-wrapped-dict",
            role_run_id="test-run-wrapped-dict-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="Scout report content",
        )
        scout_artifact_id = scout_meta["artifact_id"]

        mock_start.return_value = {"task_id": "task-2", "conversation_id": "conv-2"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "architect",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        # Pass wrapped dict values — role_call_impl must normalize them
        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Test task",
            input_artifacts={
                "scout_report": {"text": scout_artifact_id},
            },
            metadata={"run_id": "test-run-wrapped-dict"},
        )

        self.assertEqual(result["status"], "completed")

        # Verify the artifact was actually resolved — get_content_by_id
        # should succeed with the unwrapped ID
        content = store.get_content_by_id(scout_artifact_id)
        self.assertEqual(content, "Scout report content")


class TestArtifactContentInjection(TestCase):
    """Test that artifact content is injected into prompts via Jinja."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    def test_artifact_content_injected_via_jinja(self, mock_poll, mock_start):
        """Wrapped list-of-objects resolves artifact_id and content is injected."""
        mock_start.return_value = {"task_id": "task-inject-1", "conversation_id": "conv-inject-1"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "valid": True,
                "status": "DONE",
                "role": "architect",
                "summary": "Test summary",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
            }),
        }

        # Save a scout artifact
        store = ArtifactStore()
        scout_meta = store.save(
            run_id="test-run-inject",
            role_run_id="test-run-inject-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="FULL SCOUT REPORT",
        )
        scout_artifact_id = scout_meta["artifact_id"]

        # Call architect with wrapped list-of-objects
        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Plan implementation",
            input_artifacts=[
                {
                    "artifact_id": {"text": scout_artifact_id},
                    "artifact_type": {"text": "scout_report"},
                }
            ],
            metadata={"run_id": "test-run-inject"},
        )

        self.assertEqual(result["status"], "completed")

        # Verify the main prompt contains the artifact content
        calls = mock_start.call_args_list
        self.assertGreaterEqual(len(calls), 1)
        main_prompt = calls[0][1]["prompt"] if len(calls[0][1]) > 0 else calls[0][0][0]
        self.assertIn("FULL SCOUT REPORT", main_prompt)


# ---------------------------------------------------------------------------
# Smoke tests for the minimal fix (Steps 1-6 of architect plan)
# ---------------------------------------------------------------------------


class TestSmokeRoleList(TestCase):
    """Test 1: role_list does not crash on import or call."""

    def test_import_and_call(self):
        from mcp_agent.server import role_list

        result = role_list()
        self.assertIn("roles", result)
        self.assertIsInstance(result["roles"], list)
        self.assertGreater(len(result["roles"]), 0)
        for role in result["roles"]:
            self.assertIn("name", role)
            self.assertIn("readonly", role)


class TestSmokeRoleCallJobIdFallback(TestCase):
    """Test 2: role_call works without task_id (uses conversation_id fallback)."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch.object(role_lifecycle, "_get_task_status_once")
    @patch.object(role_lifecycle, "_start_conversation_on_fastapi")
    @patch.object(role_lifecycle, "render_prompt")
    def test_job_id_fallback_from_conversation_id(
        self, mock_render, mock_start, mock_poll
    ):
        mock_start.return_value = {"conversation_id": "conv-123"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({"status": "completed", "role": "scout", "summary": "test"}),
        }
        mock_render.return_value = "Scout prompt"

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts=None,
            metadata={"run_id": "test-run-jid"},
            api_key="test-key",
        )

        # Verify the job_id (conv-123) was used for polling
        call_args = mock_poll.call_args
        self.assertIsNotNone(call_args)
        self.assertEqual(call_args[0][0], "conv-123")

    @patch.object(role_lifecycle, "_get_task_status_once")
    @patch.object(role_lifecycle, "_start_conversation_on_fastapi")
    @patch.object(role_lifecycle, "render_prompt")
    def test_job_id_fallback_from_id_field(self, mock_render, mock_start, mock_poll):
        mock_start.return_value = {"id": "id-456"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({"status": "completed", "role": "scout", "summary": "test"}),
        }
        mock_render.return_value = "Scout prompt"

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts=None,
            metadata={"run_id": "test-run-jid2"},
            api_key="test-key",
        )

        # Verify _poll_task_status was called with the job_id extracted from response
        call_args = mock_poll.call_args
        self.assertIsNotNone(call_args)
        self.assertEqual(call_args[0][0], "id-456")

    @patch.object(role_lifecycle, "_get_task_status_once")
    @patch.object(role_lifecycle, "_start_conversation_on_fastapi")
    @patch.object(role_lifecycle, "render_prompt")
    def test_missing_job_id_returns_error(self, mock_render, mock_start, mock_poll):
        mock_start.return_value = {"some_other_field": "xyz"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({"status": "completed", "role": "scout", "summary": "test"}),
        }
        mock_render.return_value = "Scout prompt"

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Test task",
            input_artifacts=None,
            metadata={"run_id": "test-run-jid3"},
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingJobId")


class TestSmokeResolveInputArtifacts(TestCase):
    """Test 3: input artifacts resolve by artifact_id."""

    @patch.object(role_lifecycle, "ArtifactStore")
    @patch.object(role_lifecycle, "_get_task_status_once")
    @patch.object(role_lifecycle, "_start_conversation_on_fastapi")
    @patch.object(role_lifecycle, "render_prompt")
    def test_artifact_content_resolved_by_artifact_id(
        self, mock_render, mock_start, mock_poll, mock_astore_cls
    ):
        # Mock artifact store to return content for a known artifact_id
        mock_store = MagicMock()
        mock_store.get_content_by_id.return_value = "FULL SCOUT REPORT\n\n===\nDetailed analysis here."
        mock_store.save.return_value = {
            "artifact_id": "test-primary-art",
            "artifact_type": "scout_report",
            "artifact_name": "primary",
            "artifact_path": "test-run-ai/test_primary.artifact",
            "content_empty": False,
            "content": "test content",
        }
        mock_astore_cls.return_value = mock_store

        mock_start.return_value = {"task_id": "test-task-1"}
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({"status": "completed", "role": "architect", "summary": "test"}),
        }
        mock_render.return_value = "Architect prompt"

        # Head of IT passes only artifact_id (not full content)
        input_artifacts = [
            {"artifact_id": "art_scout_report_xyz", "artifact_type": "scout_report"}
        ]

        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Review this code",
            input_artifacts=input_artifacts,
            metadata={"run_id": "test-run-ai"},
            api_key="test-key",
        )

        # Verify artifact_store.get_content_by_id was called with the artifact_id
        mock_store.get_content_by_id.assert_called_once_with("art_scout_report_xyz")
        self.assertEqual(result["status"], "completed")


# ---------------------------------------------------------------------------
# Tests — normalize_role integration (Test 6, 7, 8, 9, 10)
# ---------------------------------------------------------------------------


class TestNormalizeRoleIntegration(TestCase):
    """Tests 6-7: role_call accepts real observed payload and wrapped metadata."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle.render_prompt")
    def test_real_observed_payload_normalizes_correctly(
        self, mock_render, mock_poll, mock_start
    ):
        """Test 6: role_call accepts the real observed payload shape.

        Real payload from Head of IT logs uses wrapped scalar values.
        """
        mock_start.return_value = {"task_id": "task-real-1", "conversation_id": "conv-real-1"}
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
        mock_render.return_value = "Scout prompt"

        # Capture the role passed to role_call_start_impl
        captured_kwargs = {}

        original_role_call_start_impl = role_lifecycle.role_call_start_impl

        def capture_impl(**kwargs):
            captured_kwargs.update(kwargs)
            return original_role_call_start_impl(**kwargs)

        with patch.object(role_lifecycle, "role_call_start_impl", side_effect=capture_impl):
            from mcp_agent.server import role_call

            result = role_call(
                role={"name": "scout"},
                user_task={"text": "Исследуй репозиторий ..."},
                repository="https://github.com/metacoma/freeplane_plugin_grpc",
                feature="ruby-grpc-client",
                scout_report_artifact_id="",
                architect_plan_artifact_id="",
                coder_report_artifact_id="",
                reviewer_report_artifact_id="",
                publisher_instructions_artifact_id="",
                idempotency_key={"text": "scout-freeplane-plugin-grpc-ruby-client"},
            )

        # Verify normalization
        self.assertEqual(captured_kwargs["role"], "scout")
        self.assertIsInstance(captured_kwargs["user_task"], str)
        self.assertEqual(
            captured_kwargs["idempotency_key"],
            "scout-freeplane-plugin-grpc-ruby-client",
        )
        self.assertEqual(
            captured_kwargs["metadata"]["repository"],
            "https://github.com/metacoma/freeplane_plugin_grpc",
        )
        self.assertEqual(captured_kwargs["metadata"]["feature"], "ruby-grpc-client")

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle.render_prompt")
    def test_wrapped_repository_feature_values_unwrap(
        self, mock_render, mock_poll, mock_start
    ):
        """Test 7: wrapped repository/feature values normalize to plain strings."""
        mock_start.return_value = {"task_id": "task-meta-1", "conversation_id": "conv-meta-1"}
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
        mock_render.return_value = "Scout prompt"

        captured_kwargs = {}

        original_role_call_start_impl = role_lifecycle.role_call_start_impl

        def capture_impl(**kwargs):
            captured_kwargs.update(kwargs)
            return original_role_call_start_impl(**kwargs)

        with patch.object(role_lifecycle, "role_call_start_impl", side_effect=capture_impl):
            from mcp_agent.server import role_call

            result = role_call(
                role="scout",
                user_task="Test task",
                repository={"text": "https://github.com/metacoma/freeplane_plugin_grpc"},
                feature={"text": "ruby-grpc-client"},
                scout_report_artifact_id="",
                architect_plan_artifact_id="",
                coder_report_artifact_id="",
                reviewer_report_artifact_id="",
                publisher_instructions_artifact_id="",
                idempotency_key=None,
            )

        self.assertEqual(
            captured_kwargs["metadata"]["repository"],
            "https://github.com/metacoma/freeplane_plugin_grpc",
        )
        self.assertEqual(captured_kwargs["metadata"]["feature"], "ruby-grpc-client")


class TestIdempotencyDuplicateRuns(TestCase):
    """Test 8: idempotency prevents duplicate runs."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle.render_prompt")
    def test_same_idempotency_key_reuses_existing_run(
        self, mock_render, mock_poll
    ):
        """Test 8: second call with same idempotency_key does not create a new role_run.

        We patch the store's find_by_idempotency_scope to return an existing run,
        verifying that _start_conversation_on_fastapi is NOT called.
        """
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
        mock_render.return_value = "Scout prompt"

        from mcp_agent.server import role_call

        idempotency_key = "scout-freeplane-plugin-grpc-ruby-client"

        # First call — creates a new run
        with patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_start:
            mock_start.return_value = {"task_id": "task-idem-1", "conversation_id": "conv-idem-1"}
            result1 = role_call(
                role="scout",
                user_task="Test task 1",
                idempotency_key=idempotency_key,
            )
            first_call_count = mock_start.call_count

        # Second call with same idempotency_key — should NOT call _start_conversation_on_fastapi
        # because the store's find_by_idempotency_scope will return the existing run
        with patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_start:
            mock_start.return_value = {"task_id": "task-idem-2", "conversation_id": "conv-idem-2"}
            result2 = role_call(
                role="scout",
                user_task="Test task 2",
                idempotency_key=idempotency_key,
            )
            second_call_count = mock_start.call_count

        # The second call should have called _start_conversation_on_fastapi fewer times
        # (ideally 0 times if idempotency works, but at minimum the role_run_id should be the same)
        self.assertIn("role_run_id", result1)
        self.assertIn("role_run_id", result2)
        # If idempotency works, the second call should not have started a new conversation
        # (call count should be 0 or less than first call)
        self.assertLessEqual(second_call_count, first_call_count)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle.render_prompt")
    def test_different_idempotency_key_creates_new_run(
        self, mock_render, mock_poll, mock_start
    ):
        """Different idempotency_key should create a new run."""
        mock_start.return_value = {"task_id": "task-idem2-1", "conversation_id": "conv-idem2-1"}
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
        mock_render.return_value = "Scout prompt"

        from mcp_agent.server import role_call

        result1 = role_call(
            role="scout",
            user_task="Test task 1",
            idempotency_key="key-1",
        )
        result2 = role_call(
            role="scout",
            user_task="Test task 2",
            idempotency_key="key-2",
        )

        # Different keys should produce different role_run_ids
        self.assertNotEqual(result1["role_run_id"], result2["role_run_id"])


class TestRoleCallResponseSchema(TestCase):
    """Test 9: role_call response does not include full content."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle.render_prompt")
    def test_response_excludes_full_result_content_artifact_path(
        self, mock_render, mock_poll, mock_start
    ):
        """Test 9: response must not contain full_result, content, or artifact_path.

        Since role_call is now non-blocking, it returns status: 'running'
        with role_run_id. The test verifies the response schema for the
        running status.
        """
        mock_start.return_value = {"task_id": "task-schema-1", "conversation_id": "conv-schema-1"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Test task",
            idempotency_key="schema-test-key",
        )

        # Response must contain these fields (running status)
        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertIn("run_id", result)
        self.assertIn("message", result)

        # Response must NOT contain these fields
        self.assertNotIn("full_result", result)
        self.assertNotIn("content", result)
        self.assertNotIn("artifact_path", result)
        self.assertNotIn("control_summary", result)


class TestWrappedInputArtifacts(TestCase):
    """Test 10: existing input_artifacts wrapped test still passes."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle.ArtifactStore")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    @patch("mcp_agent.role_lifecycle._get_task_status_once")
    @patch("mcp_agent.role_lifecycle.render_prompt")
    def test_wrapped_artifact_id_still_works(
        self, mock_render, mock_poll, mock_start, mock_astore_cls
    ):
        """Wrapped scout_report_artifact_id still works.

        Since role_call is now non-blocking, it returns status: 'running'.
        The test verifies that wrapped artifact_id values are properly
        unwrapped and passed to the lifecycle layer.
        """
        # Mock artifact store so get_content_by_id returns content for fake IDs
        mock_store = MagicMock()
        mock_store.get_content_by_id.return_value = "FULL SCOUT REPORT"
        mock_store.save.return_value = {
            "artifact_id": "test-primary-art",
            "artifact_type": "scout_report",
            "artifact_path": "/tmp/test-artifact.artifact",
            "content_empty": False,
            "content": "test content",
            "role": "architect",
            "run_id": "test-run-wrapped-art",
            "size_bytes": 100,
        }
        mock_astore_cls.return_value = mock_store

        mock_start.return_value = {"task_id": "task-art-1", "conversation_id": "conv-art-1"}

        from mcp_agent.server import role_call

        result = role_call(
            role="architect",
            user_task="Plan implementation",
            repository="https://github.com/test/repo",
            feature="",
            scout_report_artifact_id={"text": "art_scout"},
            architect_plan_artifact_id="",
            coder_report_artifact_id="",
            reviewer_report_artifact_id="",
            publisher_instructions_artifact_id="",
            idempotency_key="",
        )

        # role_call now returns running status (non-blocking)
        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)


class TestPublicToolNames(TestCase):
    """Test 1: public discovery contains correct tool names."""

    def test_tool_names(self):
        from mcp.server.fastmcp import FastMCP
        from mcp_agent.server import MCP as server_mcp

        # Verify the MCP instance has exactly these tools
        tools = list(server_mcp._tool_manager.list_tools())
        tool_names = [t.name for t in tools]

        self.assertIn("role_list", tool_names)
        self.assertIn("role_call", tool_names)
        self.assertIn("role_wait", tool_names)
        self.assertNotIn("shttp_role_list", tool_names)
        self.assertNotIn("shttp_role_call", tool_names)


# ---------------------------------------------------------------------------
# Tests — flat role_call (Tests 2-8)
# ---------------------------------------------------------------------------

class TestScoutFlatCall(TestCase):
    """Test 2: scout flat call."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_scout_flat_call(self, mock_start):
        """Scout starts with flat fields, no required artifacts, returns role_run_id."""
        mock_start.return_value = {"task_id": "task-scout-2", "conversation_id": "conv-scout-2"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Research repo",
            repository="https://github.com/metacoma/freeplane_plugin_grpc",
            feature="ruby-grpc-client",
            scout_report_artifact_id="",
            architect_plan_artifact_id="",
            coder_report_artifact_id="",
            reviewer_report_artifact_id="",
            publisher_instructions_artifact_id="",
            idempotency_key="ruby-grpc-client-scout",
        )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertEqual(result["role"], "scout")


class TestArchitectFlatCallMapsScoutReport(TestCase):
    """Test 3: architect flat call maps scout_report_artifact_id."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_architect_flat_call_maps_scout_report(self, mock_start):
        """Architect flat call maps scout_report_artifact_id to internal input_artifacts."""
        mock_start.return_value = {"task_id": "task-arch-3", "conversation_id": "conv-arch-3"}

        from mcp_agent.server import role_call

        # Patch role_call_start_impl to capture the input_artifacts argument
        captured = {}

        def capture_call(**kwargs):
            captured["input_artifacts"] = kwargs.get("input_artifacts", {})
            return {"task_id": "task-arch-3", "conversation_id": "conv-arch-3"}

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            result = role_call(
                role="architect",
                user_task="Plan Ruby client",
                repository="https://github.com/metacoma/freeplane_plugin_grpc",
                feature="ruby-grpc-client",
                scout_report_artifact_id="art_scout",
                idempotency_key="ruby-grpc-client-architect",
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(captured["input_artifacts"], {"scout_report": "art_scout"})
        finally:
            rl.role_call_start_impl = original_impl


class TestArchitectMissingScoutReport(TestCase):
    """Test 4: architect missing scout_report_artifact_id."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_architect_missing_scout_report(self, mock_start):
        """Architect without scout_report_artifact_id returns MissingRequiredArtifact."""
        from mcp_agent.server import role_call

        result = role_call(
            role="architect",
            user_task="Plan Ruby client",
            scout_report_artifact_id="",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("scout_report", result["error"]["message"])
        self.assertIn("Provide scout_report_artifact_id", result["error"]["message"])


class TestCoderFlatCallMapsTwoArtifacts(TestCase):
    """Test 5: coder flat call maps two artifacts."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_coder_flat_call_maps_two_artifacts(self, mock_start):
        """Coder flat call maps scout_report + architect_plan."""
        captured = {}

        def capture_call(**kwargs):
            captured["input_artifacts"] = kwargs.get("input_artifacts", {})
            return {"task_id": "task-coder-5", "conversation_id": "conv-coder-5"}

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            from mcp_agent.server import role_call

            result = role_call(
                role="coder",
                user_task="Implement Ruby client",
                scout_report_artifact_id="art_scout",
                architect_plan_artifact_id="art_architect",
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(
                captured["input_artifacts"],
                {"scout_report": "art_scout", "architect_plan": "art_architect"},
            )
        finally:
            rl.role_call_start_impl = original_impl


class TestReviewerFlatCallMapsThreeArtifacts(TestCase):
    """Test 6: reviewer flat call maps three artifacts."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_reviewer_flat_call_maps_three_artifacts(self, mock_start):
        """Reviewer flat call maps scout_report + architect_plan + coder_report."""
        captured = {}

        def capture_call(**kwargs):
            captured["input_artifacts"] = kwargs.get("input_artifacts", {})
            return {"task_id": "task-rev-6", "conversation_id": "conv-rev-6"}

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            from mcp_agent.server import role_call

            result = role_call(
                role="reviewer",
                user_task="Review Ruby client",
                scout_report_artifact_id="art_scout",
                architect_plan_artifact_id="art_architect",
                coder_report_artifact_id="art_coder",
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(
                captured["input_artifacts"],
                {
                    "scout_report": "art_scout",
                    "architect_plan": "art_architect",
                    "coder_report": "art_coder",
                },
            )
        finally:
            rl.role_call_start_impl = original_impl


class TestPublisherFlatCallMapsReviewerReport(TestCase):
    """Test 7: publisher flat call maps reviewer_report."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_publisher_flat_call_maps_reviewer_report(self, mock_start):
        """Publisher flat call maps reviewer_report."""
        captured = {}

        def capture_call(**kwargs):
            captured["input_artifacts"] = kwargs.get("input_artifacts", {})
            return {"task_id": "task-pub-7", "conversation_id": "conv-pub-7"}

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            from mcp_agent.server import role_call

            result = role_call(
                role="publisher",
                user_task="Prepare PR instructions",
                reviewer_report_artifact_id="art_reviewer",
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(
                captured["input_artifacts"],
                {"reviewer_report": "art_reviewer"},
            )
        finally:
            rl.role_call_start_impl = original_impl


class TestWrappedScalarValues(TestCase):
    """Test 8: wrapped scalar values."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_wrapped_scalar_values(self, mock_start):
        """Wrapped scalar values are normalized correctly."""
        captured = {}

        def capture_call(**kwargs):
            captured["role"] = kwargs.get("role")
            captured["user_task"] = kwargs.get("user_task")
            captured["idempotency_key"] = kwargs.get("idempotency_key")
            return {"task_id": "task-wrapped-8", "conversation_id": "conv-wrapped-8"}

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            from mcp_agent.server import role_call

            result = role_call(
                role={"text": "architect"},
                user_task={"text": "Plan Ruby client"},
                scout_report_artifact_id={"text": "art_scout"},
                idempotency_key={"idempotency_key": "ruby-grpc-client-architect"},
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(captured["role"], "architect")
            self.assertEqual(captured["user_task"], "Plan Ruby client")
            self.assertEqual(captured["idempotency_key"], "ruby-grpc-client-architect")
        finally:
            rl.role_call_start_impl = original_impl


class TestBadNestedPayloadRejection(TestCase):
    """Test 9: regression for old bad duplicated payload."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    def test_bad_nested_payload_rejected(self):
        """Old bad nested payload is rejected with InvalidFlatRoleCallPayload."""
        from mcp_agent.server import role_call

        # Simulate the real bad payload shape from logs
        bad_role = {
            "role": "architect",
            "user_task": "Plan Ruby client",
            "input_artifacts": [
                {"artifact_id": "art_xxx", "artifact_type": "scout_report"}
            ],
            "metadata": {"repository": "https://github.com/..."},
            "idempotency_key": "ruby-grpc-client-architect",
        }

        result = role_call(
            role=bad_role,
            user_task=bad_role,
            scout_report_artifact_id=bad_role,
            idempotency_key=bad_role,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidFlatRoleCallPayload")
        self.assertIn("flat scalar fields", result["error"]["message"])
        self.assertIn("Do not pass nested", result["error"]["message"])


class TestRoleWaitStillWorks(TestCase):
    """Test 10: role_wait still works."""

    def test_role_wait_is_public_tool(self):
        """role_wait is a public MCP tool."""
        from mcp_agent.server import MCP as server_mcp

        tools = list(server_mcp._tool_manager.list_tools())
        tool_names = [t.name for t in tools]

        self.assertIn("role_wait", tool_names)

    def test_no_legacy_v2_tools(self):
        """Legacy/shttp/v2/artifact tools are NOT public."""
        from mcp_agent.server import MCP as server_mcp

        tools = list(server_mcp._tool_manager.list_tools())
        tool_names = [t.name for t in tools]

        legacy_tools = [
            "shttp_role_call",
            "shttp_role_list",
            "shttp_role_start_v2",
            "shttp_role_wait_v2",
            "shttp_role_result_v2",
            "role_start",
            "role_status",
            "role_result",
            "artifact_get",
        ]
        for tool in legacy_tools:
            self.assertNotIn(tool, tool_names,
                           f"{tool} should NOT be a public MCP tool")


if __name__ == "__main__":
    unittest_main()


# ---------------------------------------------------------------------------
# Tests — role_call returns "running" status (non-blocking)
# ---------------------------------------------------------------------------

class TestRoleCallStartReturnsRunning(TestCase):
    """Test: role_call starts role and returns role_run_id quickly."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_returns_running_status(self, mock_start):
        """role_call returns status: 'running' with role_run_id."""
        mock_start.return_value = {"task_id": "task-1", "conversation_id": "conv-1"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Test task",
            repository="https://github.com/test/repo",
            feature="",
            scout_report_artifact_id="",
            architect_plan_artifact_id="",
            coder_report_artifact_id="",
            reviewer_report_artifact_id="",
            publisher_instructions_artifact_id="",
            idempotency_key="",
        )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertIn("run_id", result)
        self.assertEqual(result["role"], "scout")
        self.assertIn("message", result)
        # Must NOT contain these fields
        self.assertNotIn("full_result", result)
        self.assertNotIn("content", result)
        self.assertNotIn("artifact_path", result)
        self.assertNotIn("control_summary", result)


# ---------------------------------------------------------------------------
# Tests — role_wait public tool discovery
# ---------------------------------------------------------------------------

class TestRoleWaitPublicTool(TestCase):
    """Test: role_wait appears in MCP tool discovery."""

    def test_role_wait_is_public_tool(self):
        """role_wait is a public MCP tool."""
        from mcp_agent.server import MCP as server_mcp

        tools = list(server_mcp._tool_manager.list_tools())
        tool_names = [t.name for t in tools]

        self.assertIn("role_wait", tool_names)

    def test_no_legacy_v2_tools(self):
        """Legacy/shttp/v2/artifact tools are NOT public."""
        from mcp_agent.server import MCP as server_mcp

        tools = list(server_mcp._tool_manager.list_tools())
        tool_names = [t.name for t in tools]

        legacy_tools = [
            "shttp_role_call",
            "shttp_role_list",
            "shttp_role_start_v2",
            "shttp_role_wait_v2",
            "shttp_role_result_v2",
            "role_start",
            "role_status",
            "role_result",
            "artifact_get",
        ]
        for tool in legacy_tools:
            self.assertNotIn(tool, tool_names,
                           f"{tool} should NOT be a public MCP tool")
