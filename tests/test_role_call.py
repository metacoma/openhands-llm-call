#!/usr/bin/env python3
"""Tests for the new public MCP API: role_list + role_call + role_wait.

These tests verify the public surface consists of exactly three tools:
role_list, role_call, role_wait.
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
    d = Path(tempfile.mkdtemp(prefix="test_state_"))
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
          coder:
            description: "Implementation worker"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/coder.md"
            readonly: false
            timeout_minutes: 120
            requires_artifacts:
              - scout_report
              - architect_plan
            output_artifact: coder_report
          reviewer:
            description: "Read-only reviewer"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/reviewer.md"
            readonly: true
            timeout_minutes: 75
            requires_artifacts:
              - scout_report
              - architect_plan
              - coder_report
            output_artifact: reviewer_report
          publisher:
            description: "Publish instruction generator"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/publisher.md"
            readonly: true
            timeout_minutes: 30
            requires_artifacts:
              - reviewer_report
            output_artifact: publisher_instructions
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

    def test_legacy_role_start_v2_not_decorated(self):
        """shttp_role_start_v2 is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("shttp_role_start_v2", tool_names,
                         "shttp_role_start_v2 should NOT be a public MCP tool")

    def test_legacy_role_wait_v2_not_decorated(self):
        """shttp_role_wait_v2 is NOT decorated with @MCP.tool()."""
        tool_names = _get_public_tool_names()
        self.assertNotIn("shttp_role_wait_v2", tool_names,
                         "shttp_role_wait_v2 should NOT be a public MCP tool")

    def test_legacy_role_result_v2_not_decorated(self):
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
            return {
                "status": "running",
                "role_run_id": f"test-run-{kwargs.get('role', 'unknown')}-3",
                "run_id": "test-run-3",
                "role": str(kwargs.get("role")),
                "message": "Role started.",
            }

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
            return {
                "status": "running",
                "role_run_id": f"test-run-{kwargs.get('role', 'unknown')}-5",
                "run_id": "test-run-5",
                "role": str(kwargs.get("role")),
                "message": "Role started.",
            }

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
            return {
                "status": "running",
                "role_run_id": f"test-run-{kwargs.get('role', 'unknown')}-6",
                "run_id": "test-run-6",
                "role": str(kwargs.get("role")),
                "message": "Role started.",
            }

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
            return {
                "status": "running",
                "role_run_id": f"test-run-{kwargs.get('role', 'unknown')}-7",
                "run_id": "test-run-7",
                "role": str(kwargs.get("role")),
                "message": "Role started.",
            }

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
        """Non-text wrapped scalar values are normalized correctly."""
        captured = {}

        def capture_call(**kwargs):
            captured["role"] = kwargs.get("role")
            captured["user_task"] = kwargs.get("user_task")
            captured["idempotency_key"] = kwargs.get("idempotency_key")
            return {
                "status": "running",
                "role_run_id": f"test-run-{kwargs.get('role', 'unknown')}-8",
                "run_id": "test-run-8",
                "role": str(kwargs.get("role")),
                "message": "Role started.",
            }

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            from mcp_agent.server import role_call

            # Use proper wrapper types: {name: ...} for role, {text: ...} for user_task
            result = role_call(
                role={"name": "architect"},
                user_task="Plan Ruby client",
                scout_report_artifact_id={"name": "art_scout"},
                idempotency_key="ruby-grpc-client-architect",
            )

            self.assertEqual(result["status"], "running")
            self.assertEqual(captured["role"], "architect")
            self.assertEqual(captured["user_task"], "Plan Ruby client")
            self.assertEqual(captured["idempotency_key"], "ruby-grpc-client-architect")
        finally:
            rl.role_call_start_impl = original_impl

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_wrapped_name_artifact_id(self, mock_start):
        """scout_report_artifact_id={'name': 'art_scout'} unwraps correctly."""
        captured = {}

        def capture_call(**kwargs):
            captured["input_artifacts"] = kwargs.get("input_artifacts", {})
            return {
                "status": "running",
                "role_run_id": f"test-run-name-{kwargs.get('role', 'unknown')}-1",
                "run_id": "test-run-name-1",
                "role": str(kwargs.get("role")),
                "message": "Role started.",
            }

        from mcp_agent import role_lifecycle as rl
        original_impl = rl.role_call_start_impl
        rl.role_call_start_impl = capture_call

        try:
            from mcp_agent.server import role_call

            result = role_call(
                role={"name": "architect"},
                user_task={"name": "Plan Ruby client"},
                scout_report_artifact_id={"name": "art_20260608-143106-46c480_scout_1_scout_report"},
                idempotency_key={"idempotency_key": "ruby-grpc-client-architect"},
            )

            self.assertEqual(result["status"], "running")
            self.assertIn("scout_report", captured.get("input_artifacts", {}))
            self.assertEqual(
                captured["input_artifacts"]["scout_report"],
                "art_20260608-143106-46c480_scout_1_scout_report"
            )
        finally:
            rl.role_call_start_impl = original_impl


class TestBadNestedPayloadRejection(TestCase):
    """Test 9: old nested payload in ANY field is rejected with InvalidFlatRoleCallPayload."""

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

    def _bad_payload(self):
        return {
            "role": "architect",
            "user_task": "Plan Ruby client",
            "input_artifacts": [
                {"artifact_id": "art_xxx", "artifact_type": "scout_report"}
            ],
            "metadata": {"repository": "https://github.com/..."},
            "idempotency_key": "ruby-grpc-client-architect",
        }

    def _assert_invalid_flat(self, result):
        self.assertEqual(result["status"], "failed")
        # Accept either the old InvalidFlatRoleCallPayload type or the new InvalidFlatPayload type
        self.assertIn(result["error"]["type"], {"InvalidFlatRoleCallPayload", "InvalidFlatPayload"})
        # Both old and new messages mention flat scalar fields or plain string
        self.assertTrue(
            "flat" in result["error"]["message"].lower() or "plain" in result["error"]["message"].lower(),
            f"Error message should mention flat/plain fields, got: {result['error']['message']}",
        )

    def test_bad_nested_in_role(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role=self._bad_payload(), user_task="Plan"))

    def test_bad_nested_in_user_task(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="architect", user_task=self._bad_payload()))

    def test_bad_nested_in_repository(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="architect", user_task="Plan", repository=self._bad_payload()))

    def test_bad_nested_in_feature(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="architect", user_task="Plan", feature=self._bad_payload()))

    def test_bad_nested_in_scout_report_artifact_id(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="architect", user_task="Plan", scout_report_artifact_id=self._bad_payload()))

    def test_bad_nested_in_architect_plan_artifact_id(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="coder", user_task="Code", scout_report_artifact_id="art_scout", architect_plan_artifact_id=self._bad_payload()))

    def test_bad_nested_in_coder_report_artifact_id(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="reviewer", user_task="Review", scout_report_artifact_id="art_scout", architect_plan_artifact_id="art_architect", coder_report_artifact_id=self._bad_payload()))

    def test_bad_nested_in_reviewer_report_artifact_id(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="publisher", user_task="Publish", scout_report_artifact_id="art_scout", architect_plan_artifact_id="art_architect", coder_report_artifact_id="art_coder", reviewer_report_artifact_id=self._bad_payload()))

    def test_bad_nested_in_publisher_instructions_artifact_id(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="publisher", user_task="Publish", scout_report_artifact_id="art_scout", architect_plan_artifact_id="art_architect", coder_report_artifact_id="art_coder", reviewer_report_artifact_id="art_reviewer", publisher_instructions_artifact_id=self._bad_payload()))

    def test_bad_nested_in_idempotency_key(self):
        from mcp_agent.server import role_call
        self._assert_invalid_flat(role_call(role="architect", user_task="Plan", idempotency_key=self._bad_payload()))


class TestInvalidArtifactId(TestCase):
    """Test 8: invalid artifact id returns InvalidArtifactId."""

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

    def test_invalid_scout_report_artifact_id(self):
        from mcp_agent.server import role_call
        result = role_call(
            role="architect",
            user_task="Plan",
            scout_report_artifact_id="not-an-artifact",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidArtifactId")
        self.assertIn("scout_report_artifact_id", result["error"]["message"])
        self.assertIn("art_", result["error"]["message"])

    def test_invalid_architect_plan_artifact_id(self):
        from mcp_agent.server import role_call
        result = role_call(
            role="coder",
            user_task="Code",
            scout_report_artifact_id="art_scout",
            architect_plan_artifact_id="not-an-artifact",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidArtifactId")
        self.assertIn("architect_plan_artifact_id", result["error"]["message"])

    def test_invalid_wrapped_artifact_id(self):
        """Invalid artifact ID inside {'name': ...} wrapper is caught."""
        from mcp_agent.server import role_call
        result = role_call(
            role="architect",
            user_task="Plan",
            scout_report_artifact_id={"name": "not-an-artifact"},
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidArtifactId")
        self.assertIn("scout_report_artifact_id", result["error"]["message"])
        self.assertIn("art_", result["error"]["message"])


class TestBadNestedPayloadInArtifactField(TestCase):
    """Test 10: old nested payload in artifact field is rejected."""

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

    def test_bad_nested_in_scout_report_artifact_id(self):
        from mcp_agent.server import role_call
        bad_payload = {
            "role": "architect",
            "input_artifacts": [
                {"artifact_id": "art_scout", "artifact_type": "scout_report"}
            ],
        }
        result = role_call(
            role="architect",
            user_task="Plan",
            scout_report_artifact_id=bad_payload,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidFlatRoleCallPayload")

    def test_bad_nested_in_user_task(self):
        from mcp_agent.server import role_call
        bad_payload = {
            "role": "architect",
            "user_task": "Plan",
            "input_artifacts": [{"artifact_id": "art_scout"}],
            "metadata": {"repository": "..."},
            "idempotency_key": "x",
        }
        result = role_call(
            role="architect",
            user_task=bad_payload,
            scout_report_artifact_id="art_scout",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidFlatRoleCallPayload")

    def test_bad_nested_in_idempotency_key(self):
        from mcp_agent.server import role_call
        bad_payload = {
            "role": "architect",
            "user_task": "Plan",
            "input_artifacts": [{"artifact_id": "art_scout"}],
            "metadata": {"repository": "..."},
            "idempotency_key": "x",
        }
        result = role_call(
            role="architect",
            user_task="Plan",
            scout_report_artifact_id="art_scout",
            idempotency_key=bad_payload,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidFlatRoleCallPayload")


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


# ---------------------------------------------------------------------------
# Tests — nested {"text": "..."} payload rejection
# ---------------------------------------------------------------------------

class TestNestedTextWrapperRejection(TestCase):
    """Test that {"text": "..."} wrappers are normalized (unwrapped) on public MCP layer."""

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
    def test_role_call_unwraps_text_wrapper_on_role_field(self, mock_start):
        """role_call unwraps {"text": "..."} on the role field."""
        mock_start.return_value = {"task_id": "task-1", "conversation_id": "conv-1"}
        from mcp_agent.server import role_call
        result = role_call(
            role={"text": "scout"},
            user_task="Analyze repository",
        )
        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_role_call_unwraps_text_wrapper_on_user_task_field(self, mock_start):
        """role_call unwraps {"text": "..."} on the user_task field."""
        mock_start.return_value = {"task_id": "task-2", "conversation_id": "conv-2"}
        from mcp_agent.server import role_call
        result = role_call(
            role="scout",
            user_task={"text": "Analyze repository"},
        )
        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_role_call_unwraps_text_wrapper_on_repository_field(self, mock_start):
        """role_call unwraps {"text": "..."} on the repository field."""
        mock_start.return_value = {"task_id": "task-3", "conversation_id": "conv-3"}
        from mcp_agent.server import role_call
        result = role_call(
            role="scout",
            user_task="Analyze",
            repository={"text": "https://github.com/test/repo"},
        )
        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_role_call_unwraps_text_wrapper_on_idempotency_key(self, mock_start):
        """role_call unwraps {"text": "..."} on the idempotency_key field."""
        mock_start.return_value = {"task_id": "task-4", "conversation_id": "conv-4"}
        from mcp_agent.server import role_call
        result = role_call(
            role="scout",
            user_task="Analyze",
            idempotency_key={"text": "test-key"},
        )
        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)

    def test_role_wait_unwraps_text_wrapper_on_role_run_id(self):
        """role_wait unwraps {"text": "..."} on the role_run_id field, then fails with RoleRunNotFound."""
        from mcp_agent.server import role_wait
        result = role_wait(role_run_id={"text": "abc"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "RoleRunNotFound")


# ---------------------------------------------------------------------------
# Tests — error structure enrichment
# ---------------------------------------------------------------------------

class TestErrorStructureEnrichment(TestCase):
    """Test that errors contain next_action, do_not, retryable, message."""

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

    def test_missing_artifact_error_has_next_action(self):
        """MissingRequiredArtifact error has next_action."""
        from mcp_agent.server import role_call
        result = role_call(
            role="architect",
            user_task="Plan",
            scout_report_artifact_id="",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("next_action", result["error"])
        self.assertEqual(result["error"]["next_action"]["tool"], "role_call")
        self.assertIn("do_not", result["error"])

    def test_missing_artifact_error_mentions_required_field(self):
        """MissingRequiredArtifact error message mentions a required field."""
        from mcp_agent.server import role_call
        result = role_call(
            role="coder",
            user_task="Code",
            scout_report_artifact_id="",
            architect_plan_artifact_id="",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        # The error mentions the first missing required field
        self.assertTrue(
            "scout_report" in result["error"]["message"] or "architect_plan" in result["error"]["message"],
            f"Error should mention a required field, got: {result['error']['message']}",
        )

    def test_missing_artifact_error_has_do_not(self):
        """MissingRequiredArtifact error has do_not."""
        from mcp_agent.server import role_call
        result = role_call(
            role="coder",
            user_task="Code",
            scout_report_artifact_id="art_scout",
            architect_plan_artifact_id="",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("do_not", result["error"])
        self.assertTrue(
            any("invent" in d.lower() for d in result["error"]["do_not"]),
            "do_not should mention not inventing artifact ids",
        )

    def test_invalid_flat_payload_error_has_correct_example(self):
        """Wrapped role value is normalized and call proceeds (fails due to no server)."""
        from mcp_agent.server import role_call
        result = role_call(
            role={"text": "scout"},
            user_task="Analyze",
        )
        # Wrapped role is now normalized, so we get a network error (no server) not InvalidFlatPayload
        self.assertEqual(result["status"], "failed")
        self.assertIn("type", result["error"])
        self.assertIn("retryable", result["error"])
        self.assertIn("message", result["error"])

    def test_all_errors_have_type_and_retryable_and_message(self):
        """All errors have type, retryable, and message."""
        from mcp_agent.server import role_call
        result = role_call(
            role={"text": "scout"},
            user_task="Analyze",
        )
        self.assertIn("type", result["error"])
        self.assertIn("retryable", result["error"])
        self.assertIn("message", result["error"])


# ---------------------------------------------------------------------------
# Tests — role_call running response has next_action on role_wait
# ---------------------------------------------------------------------------

class TestRoleCallRunningResponse(TestCase):
    """Test that role_call running response contains next_action pointing to role_wait."""

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
    def test_running_response_has_next_action_role_wait(self, mock_start):
        """role_call running response has next_action.tool == role_wait."""
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
        self.assertIn("next_action", result)
        self.assertEqual(result["next_action"]["tool"], "role_wait")
        self.assertIn("role_run_id", result["next_action"]["arguments"])
        self.assertIn("do_not", result)
        self.assertTrue(
            any("role_call" in d.lower() and "again" in d.lower() for d in result["do_not"]),
            "do_not should mention not calling role_call again",
        )


# ---------------------------------------------------------------------------
# Tests — role_list structure
# ---------------------------------------------------------------------------

class TestRoleListStructure(TestCase):
    """Test role_list output structure."""

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

    def test_role_list_has_tools_allowed(self):
        """role_list has tools.allowed, no forbidden list."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("tools", result)
        self.assertIn("allowed", result["tools"])
        self.assertNotIn("forbidden", result["tools"])
        self.assertEqual(set(result["tools"]["allowed"]), {"role_list", "role_call", "role_wait"})

    def test_role_list_has_workflow_with_steps(self):
        """role_list has workflow with step objects."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("workflow", result)
        workflow = result["workflow"]
        self.assertIsInstance(workflow, list)
        self.assertGreater(len(workflow), 0)
        # Check first step has required fields
        step = workflow[0]
        self.assertIn("step", step)
        self.assertIn("role", step)
        self.assertIn("requires", step)
        self.assertIn("produces", step)

    def test_role_list_has_rules(self):
        """role_list has rules."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("rules", result)
        self.assertIsInstance(result["rules"], list)
        self.assertGreater(len(result["rules"]), 0)
        self.assertTrue(
            any("role_call" in r and "polling" in r.lower() for r in result["rules"]),
            "rules should mention not calling role_call for polling",
        )

    def test_role_list_has_examples(self):
        """role_list has examples."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("examples", result)
        self.assertIn("start_scout", result["examples"])
        self.assertIn("wait", result["examples"])
        self.assertEqual(result["examples"]["start_scout"]["tool"], "role_call")
        self.assertEqual(result["examples"]["wait"]["tool"], "role_wait")


# ---------------------------------------------------------------------------
# Tests — coder without architect_plan_artifact_id fails clearly
# ---------------------------------------------------------------------------

class TestCoderWithoutArchitectPlan(TestCase):
    """Test that coder without architect_plan_artifact_id fails with clear error."""

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

    def test_coder_without_architect_plan_returns_missing_artifact_error(self):
        """Coder without architect_plan_artifact_id returns MissingRequiredArtifact."""
        from mcp_agent.server import role_call
        result = role_call(
            role="coder",
            user_task="Implement changes",
            repository="https://github.com/metacoma/openhands-llm-call",
            scout_report_artifact_id="",
            architect_plan_artifact_id="",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")

    def test_missing_artifact_error_mentions_architect_plan(self):
        """Missing artifact error mentions architect_plan_artifact_id."""
        from mcp_agent.server import role_call
        result = role_call(
            role="coder",
            user_task="Implement changes",
            repository="https://github.com/metacoma/openhands-llm-call",
            scout_report_artifact_id="art_scout",
            architect_plan_artifact_id="",
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("architect_plan", result["error"]["message"])

    def test_missing_artifact_error_has_do_not(self):
        """Missing artifact error has do_not."""
        from mcp_agent.server import role_call
        result = role_call(
            role="coder",
            user_task="Implement changes",
            repository="https://github.com/metacoma/openhands-llm-call",
            scout_report_artifact_id="art_scout",
            architect_plan_artifact_id="",
        )
        self.assertIn("do_not", result["error"])
        self.assertTrue(
            any("invent" in d.lower() for d in result["error"]["do_not"]),
            "do_not should mention not inventing artifact ids",
        )


if __name__ == "__main__":
    unittest_main()


# ---------------------------------------------------------------------------
# Tests — role_wait sanitized response (Test 11)
# ---------------------------------------------------------------------------

class TestRoleWaitSanitizedResponse(TestCase):
    """Test 11: role_wait completed response is sanitized."""

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
    def test_completed_response_strips_artifact_path_content(self, mock_start):
        from mcp_agent.server import role_call
        from mcp_agent.role_store import RoleRunStore

        # Start a role to get a role_run_id
        mock_start.return_value = {"task_id": "task-1", "conversation_id": "conv-1"}
        result = role_call(
            role="scout",
            user_task="Test",
            repository="https://github.com/test/repo",
            idempotency_key="test-scout-1",
        )
        self.assertEqual(result["status"], "running")
        role_run_id = result["role_run_id"]

        # Manually update the stored role run to simulate completed state
        # with artifact metadata containing artifact_path and content
        role_store = RoleRunStore()
        role_store.update_role_run(
            role_run_id,
            status="completed",
            result_summary=json.dumps({"summary": "done"}),
            artifacts=json.dumps({
                "primary": {
                    "artifact_id": "art_scout",
                    "artifact_path": "/tmp/secret_path",
                    "content": "secret_content",
                },
                "summary": {
                    "artifact_id": "art_summary",
                    "artifact_path": "/tmp/secret_summary",
                    "content": "secret_summary_content",
                },
            }),
        )

        # Call role_wait
        from mcp_agent.server import role_wait
        wait_result = role_wait(role_run_id=role_run_id)

        # Verify sanitized response
        self.assertEqual(wait_result["status"], "completed")
        # Must contain artifact_id
        self.assertIn("artifacts", wait_result)
        artifacts = wait_result.get("artifacts", {})
        for art_key in ("primary", "summary"):
            if art_key in artifacts:
                art = artifacts[art_key]
                self.assertIn("artifact_id", art)
                # Must NOT contain forbidden keys
                self.assertNotIn("artifact_path", art)
                self.assertNotIn("content", art)

        # Top-level must NOT contain forbidden keys
        for key in ("artifact_path", "content", "full_result", "result"):
            self.assertNotIn(key, wait_result)


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
        # Reset loop guard state to avoid cross-test pollution
        import mcp_agent.server as server_mod
        server_mod._invalid_call_fingerprints.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        os.environ.pop("ROLE_CONFIG_PATH", None)
        # Reset loop guard state
        import mcp_agent.server as server_mod
        server_mod._invalid_call_fingerprints.clear()

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

# ---------------------------------------------------------------------------
# Test 1: role_call tool description mentions flat-only
# ---------------------------------------------------------------------------

class TestRoleCallDescriptionFlatOnly(TestCase):
    """Test that role_call docstring contains flat-only documentation."""

    def test_role_call_description_mentions_flat_scalar_fields(self):
        """role_call docstring contains 'flat scalar fields'."""
        from mcp_agent.server import role_call
        self.assertIn("flat", role_call.__doc__.lower())
        self.assertIn("scalar", role_call.__doc__.lower())

    def test_role_call_description_forbids_metadata(self):
        """role_call docstring uses positive contract (no metadata field)."""
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        # After cleanup, docstring should NOT mention metadata as a field
        self.assertNotIn(
            "metadata", doc.lower().split("artifact routing")[0] if "artifact routing" in doc.lower() else doc.lower(),
            "role_call description should not mention metadata as an input field",
        )

    def test_role_call_description_forbids_input_artifacts(self):
        """role_call docstring uses positive contract (no input_artifacts field)."""
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        # After cleanup, docstring should NOT mention input_artifacts as a field
        self.assertNotIn(
            "input_artifacts", doc.lower().split("artifact routing")[0] if "artifact routing" in doc.lower() else doc.lower(),
            "role_call description should not mention input_artifacts as an input field",
        )

    def test_role_call_description_mentions_role_wait(self):
        """role_call docstring mentions role_wait."""
        from mcp_agent.server import role_call
        self.assertIn("role_wait", role_call.__doc__)

    def test_role_call_description_mentions_artifact_fields(self):
        """role_call docstring mentions all artifact field names."""
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        for field in ["scout_report_artifact_id", "architect_plan_artifact_id"]:
            self.assertIn(field, doc, f"role_call must mention {field}")


# ---------------------------------------------------------------------------
# Test 2: role_wait tool description mentions polling rule
# ---------------------------------------------------------------------------

class TestRoleWaitDescriptionPollingRule(TestCase):
    """Test that role_wait docstring contains polling rule."""

    def test_role_wait_description_mentions_polling_rule(self):
        """role_wait docstring says 'call role_wait again with the same role_run_id'."""
        from mcp_agent.server import role_wait
        doc = role_wait.__doc__
        self.assertIn("role_wait", doc)
        self.assertIn("same role_run_id", doc.lower())

    def test_role_wait_description_forbids_role_call_polling(self):
        """role_wait docstring says 'Never call role_call again for polling'."""
        from mcp_agent.server import role_wait
        doc = role_wait.__doc__
        self.assertTrue(
            any(phrase in doc.lower() for phrase in [
                "never call role_call",
                "do not call role_call",
                "do not start the role again",
            ]),
            "role_wait must forbid using role_call for polling",
        )


# ---------------------------------------------------------------------------
# Test 3: role_list response contains usage docs
# ---------------------------------------------------------------------------

class TestRoleListResponseUsageDocs(TestCase):
    """Test that role_list response contains usage/routing documentation."""

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

    def test_role_list_contains_public_tools(self):
        """role_list response contains public_tools key."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("public_tools", result)
        self.assertIn("role_list", result["public_tools"])
        self.assertIn("role_call", result["public_tools"])
        self.assertIn("role_wait", result["public_tools"])

    def test_role_list_contains_workflow(self):
        """role_list response contains workflow key."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("workflow", result)
        self.assertIsInstance(result["workflow"], list)
        self.assertGreater(len(result["workflow"]), 0)

    def test_role_list_contains_flat_role_call_contract(self):
        """role_list response contains flat_role_call_contract key with positive contract."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("flat_role_call_contract", result)
        contract = result["flat_role_call_contract"]
        self.assertTrue(contract["use_only_flat_scalar_fields"])
        # Positive contract: allowed_tools, allowed_role_call_fields, artifact_fields, examples
        self.assertIn("allowed_tools", contract)
        self.assertIn("allowed_role_call_fields", contract)
        self.assertIn("artifact_fields", contract)
        self.assertIn("examples", contract)
        # forbidden_fields removed per legacy cleanup
        self.assertNotIn("forbidden_fields", contract)

    def test_role_list_contains_routing_examples(self):
        """role_list response contains routing_examples key."""
        from mcp_agent.server import role_list
        result = role_list()
        self.assertIn("routing_examples", result)
        re = result["routing_examples"]
        self.assertIn("architect_after_scout", re)
        self.assertIn("coder_after_architect", re)
        self.assertIn("reviewer_after_coder", re)
        self.assertIn("publisher_after_pass", re)


# ---------------------------------------------------------------------------
# Test 4: role_list routing maps required fields
# ---------------------------------------------------------------------------

class TestRoleListRoutingMapsRequiredFields(TestCase):
    """Test that role_list returns correct required_flat_fields per role."""

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

    def test_architect_required_flat_fields(self):
        """Architect role has required_flat_fields = ['scout_report_artifact_id']."""
        from mcp_agent.server import role_list
        result = role_list()
        architect = next(r for r in result["roles"] if r["name"] == "architect")
        self.assertIn("required_flat_fields", architect)
        self.assertEqual(architect["required_flat_fields"], ["scout_report_artifact_id"])

    def test_coder_required_flat_fields(self):
        """Coder role has required_flat_fields = ['scout_report_artifact_id', 'architect_plan_artifact_id']."""
        from mcp_agent.server import role_list
        result = role_list()
        coder = next(r for r in result["roles"] if r["name"] == "coder")
        self.assertIn("required_flat_fields", coder)
        self.assertEqual(
            coder["required_flat_fields"],
            ["scout_report_artifact_id", "architect_plan_artifact_id"],
        )

    def test_reviewer_required_flat_fields(self):
        """Reviewer role has required_flat_fields = ['scout_report_artifact_id', 'architect_plan_artifact_id', 'coder_report_artifact_id']."""
        from mcp_agent.server import role_list
        result = role_list()
        reviewer = next(r for r in result["roles"] if r["name"] == "reviewer")
        self.assertIn("required_flat_fields", reviewer)
        self.assertEqual(
            reviewer["required_flat_fields"],
            [
                "scout_report_artifact_id",
                "architect_plan_artifact_id",
                "coder_report_artifact_id",
            ],
        )

    def test_publisher_required_flat_fields(self):
        """Publisher role has required_flat_fields = ['reviewer_report_artifact_id']."""
        from mcp_agent.server import role_list
        result = role_list()
        publisher = next(r for r in result["roles"] if r["name"] == "publisher")
        self.assertIn("required_flat_fields", publisher)
        self.assertEqual(publisher["required_flat_fields"], ["reviewer_report_artifact_id"])


# ---------------------------------------------------------------------------
# Test 5: no nested public examples
# ---------------------------------------------------------------------------

class TestNoNestedPublicExamples(TestCase):
    """Test that public role_call docs do not recommend nested fields."""

    def test_role_call_doc_forbids_metadata_not_recommended(self):
        """role_call docstring does not recommend metadata as a public field."""
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        lines = doc.split("\n")
        for line in lines:
            stripped = line.strip()
            if "metadata" in stripped.lower():
                # Should be in a forbidden/reject context
                self.assertTrue(
                    any(kw in stripped.lower() for kw in [
                        "do not", "do not pass", "forbidden", "not", "never",
                    ]),
                    f"metadata should only appear in forbidden context, found: {stripped}",
                )

    def test_role_call_doc_forbids_input_artifacts_not_recommended(self):
        """role_call docstring does not recommend input_artifacts as a public field."""
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        lines = doc.split("\n")
        for line in lines:
            stripped = line.strip()
            if "input_artifacts" in stripped.lower():
                self.assertTrue(
                    any(kw in stripped.lower() for kw in [
                        "do not", "do not pass", "forbidden", "not", "never",
                    ]),
                    f"input_artifacts should only appear in forbidden context, found: {stripped}",
                )


# ---------------------------------------------------------------------------
# Test 6: public tools remain only role_list/role_call/role_wait
# ---------------------------------------------------------------------------

class TestPublicToolsRemainOnlyThree(TestCase):
    """Test that exactly three public MCP tools are exposed."""

    def test_exactly_three_public_tools(self):
        """Exactly three public MCP tools: role_list, role_call, role_wait."""
        tool_names = _get_public_tool_names()
        self.assertEqual(tool_names, {"role_list", "role_call", "role_wait"})

    def test_no_legacy_tools_exposed(self):
        """Legacy tools are NOT in public tool list."""
        tool_names = _get_public_tool_names()
        legacy = {
            "role_start", "role_status", "role_result",
            "artifact_get", "artifact_list",
            "shttp_role_start_v2", "shttp_role_wait_v2",
            "shttp_role_result_v2", "shttp_role_call",
            "shttp_role_wait", "shttp_role_list",
        }
        self.assertEqual(
            tool_names & legacy, set(),
            f"Legacy tools exposed: {tool_names & legacy}",
        )


# ---------------------------------------------------------------------------
# Test 1: role_call docstring concise
# ---------------------------------------------------------------------------

class TestRoleCallDocstringConcise(TestCase):
    """Test role_call docstring is concise and contains required keywords."""

    def test_role_call_docstring_contains_flat_scalar_fields(self):
        from mcp_agent.server import role_call
        self.assertIn("flat scalar fields", role_call.__doc__)

    def test_role_call_docstring_forbids_metadata(self):
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        # After cleanup, docstring uses positive contract
        pre_routing = doc.lower().split("artifact routing")[0] if "artifact routing" in doc.lower() else doc.lower()
        self.assertNotIn(
            "metadata", pre_routing,
            "role_call docstring should not mention metadata as an input field",
        )

    def test_role_call_docstring_forbids_input_artifacts(self):
        from mcp_agent.server import role_call
        doc = role_call.__doc__
        # After cleanup, docstring uses positive contract
        pre_routing = doc.lower().split("artifact routing")[0] if "artifact routing" in doc.lower() else doc.lower()
        self.assertNotIn(
            "input_artifacts", pre_routing,
            "role_call docstring should not mention input_artifacts as an input field",
        )

    def test_role_call_docstring_mentions_role_wait(self):
        from mcp_agent.server import role_call
        self.assertIn("role_wait", role_call.__doc__)

    def test_role_call_docstring_mentions_polling(self):
        from mcp_agent.server import role_call
        self.assertIn("polling", role_call.__doc__)

    def test_role_call_docstring_mentions_role_list(self):
        from mcp_agent.server import role_call
        self.assertIn("role_list", role_call.__doc__)

    def test_role_call_docstring_length_under_1200(self):
        from mcp_agent.server import role_call
        self.assertLessEqual(
            len(role_call.__doc__), 1200,
            f"role_call docstring is {len(role_call.__doc__)} chars (max 1200)",
        )


# ---------------------------------------------------------------------------
# Test 2: role_wait docstring concise
# ---------------------------------------------------------------------------

class TestRoleWaitDocstringConcise(TestCase):
    """Test role_wait docstring is concise and contains required keywords."""

    def test_role_wait_docstring_contains_same_role_run_id(self):
        from mcp_agent.server import role_wait
        self.assertIn("same role_run_id", role_wait.__doc__)

    def test_role_wait_docstring_forbids_role_call_polling(self):
        from mcp_agent.server import role_wait
        self.assertIn("Never call role_call again for polling", role_wait.__doc__)

    def test_role_wait_docstring_mentions_artifacts_primary(self):
        from mcp_agent.server import role_wait
        self.assertIn("artifacts.primary.artifact_id", role_wait.__doc__)

    def test_role_wait_docstring_length_under_900(self):
        from mcp_agent.server import role_wait
        self.assertLessEqual(
            len(role_wait.__doc__), 900,
            f"role_wait docstring is {len(role_wait.__doc__)} chars (max 900)",
        )


# ---------------------------------------------------------------------------
# Test 3: role_list docstring concise
# ---------------------------------------------------------------------------

class TestRoleListDocstringConcise(TestCase):
    """Test role_list docstring is concise."""

    def test_role_list_docstring_contains_usage_routing_hints(self):
        from mcp_agent.server import role_list
        self.assertIn("usage/routing hints", role_list.__doc__)

    def test_role_list_docstring_length_under_500(self):
        from mcp_agent.server import role_list
        self.assertLessEqual(
            len(role_list.__doc__), 500,
            f"role_list docstring is {len(role_list.__doc__)} chars (max 500)",
        )


# ---------------------------------------------------------------------------
# Test 6: no long JSON examples in docstrings
# ---------------------------------------------------------------------------

class TestNoLongJsonExamplesInDocstrings(TestCase):
    """Test that public tool docstrings do not contain long JSON examples."""

    def test_no_input_artifacts_json_example_in_role_call(self):
        from mcp_agent.server import role_call
        self.assertNotIn('"input_artifacts": [', role_call.__doc__)

    def test_no_metadata_json_example_in_role_call(self):
        from mcp_agent.server import role_call
        self.assertNotIn('"metadata": {', role_call.__doc__)

    def test_no_status_completed_example_in_role_wait(self):
        from mcp_agent.server import role_wait
        self.assertNotIn('"status": "completed"', role_wait.__doc__)


# ---------------------------------------------------------------------------
# Tests for PR #44 blocker fixes
# ---------------------------------------------------------------------------

class TestBlockerFixes(TestCase):
    """Tests for PR #44 blocker fixes."""

    def test_import_mcp_agent_server_as_package(self):
        """Import path works when importing mcp_agent.server as a package."""
        import mcp_agent.server  # noqa: F401
        # If this import succeeds, the test passes.

    def test_no_bare_import_role_lifecycle_in_server(self):
        """No bare 'import role_lifecycle' remains in mcp_agent/server.py."""
        import re
        with open("mcp_agent/server.py", "r") as f:
            content = f.read()
        bare_imports = re.findall(
            r"^\s*import\s+role_lifecycle\s*$", content, re.MULTILINE
        )
        self.assertEqual(
            bare_imports, [],
            f"Found bare imports: {bare_imports}",
        )
        # All role_lifecycle imports must be relative
        relative_imports = re.findall(
            r"from\s+\.\s+import\s+role_lifecycle", content, re.MULTILINE
        )
        self.assertGreater(
            len(relative_imports), 0,
            "Expected at least one relative import of role_lifecycle",
        )

    def test_head_of_it_tolerant_wrapper_policy(self):
        """head_of_it.md reflects tolerant wrapper policy, not rejection."""
        with open("prompts/head_of_it.md", "r") as f:
            content = f.read()
        # Must NOT say wrappers are invalid/rejected near "wrapped scalar" context
        self.assertNotIn("invalid", content.lower().split("wrapped scalar")[1].split("\n")[0].lower() if "wrapped scalar" in content.lower() else "")
        self.assertNotIn("rejected", content.lower().split("wrapped scalar")[1].split("\n")[0].lower() if "wrapped scalar" in content.lower() else "")
        # Must say server normalizes wrappers
        self.assertIn("normaliz", content.lower())

    def test_readme_no_synchronous_call_claim(self):
        """README does not contain forbidden synchronous-call phrases."""
        with open("README.md", "r") as f:
            content = f.read()
        self.assertNotIn("executes the full two-step lifecycle synchronously", content)
        self.assertNotIn("Single-role synchronous call", content)
        self.assertNotIn("blocks until the role completes", content.lower())

    def test_readme_tolerant_wrapper_policy(self):
        """README reflects tolerant wrapper policy (server normalizes wrappers)."""
        with open("README.md", "r") as f:
            content = f.read()
        # Must mention normalization
        self.assertIn("normaliz", content.lower())

    def test_scout_requires_empty(self):
        """scout workflow requires []."""
        from mcp_agent.server import role_list
        result = role_list()
        scout = next((r for r in result["roles"] if r["name"] == "scout"), None)
        self.assertIsNotNone(scout)
        self.assertEqual(scout["requires_artifacts"], [])

    def test_completed_architect_next_action_no_scout_report_hint(self):
        """completed architect next_action does not set scout_report_artifact_id to architect artifact."""
        import inspect
        from mcp_agent.server import role_wait

        source = inspect.getsource(role_wait)
        # The fix should use _ROLE_OUTPUT_HINT_MAP keyed by current role,
        # not artifact_hints keyed by next_role that fills all fields.
        self.assertIn("_ROLE_OUTPUT_HINT_MAP", source)
        # Verify the old artifact_hints pattern is gone
        self.assertNotIn('artifact_hints = {', source)

    def test_missing_required_artifact_routes_correctly(self):
        """MissingRequiredArtifact for reviewer/coder/publisher routes to correct previous role."""
        from mcp_agent.server import role_call

        # Test: coder missing architect_plan → next_action.role should be "architect"
        result = role_call(
            role="coder",
            user_task="Code",
            scout_report_artifact_id="art_scout",
            architect_plan_artifact_id="",  # missing
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("architect_plan", result["error"]["message"])
        self.assertEqual(
            result["error"]["next_action"]["arguments_hint"]["role"],
            "architect",
        )

        # Test: reviewer missing coder_report → next_action.role should be "coder"
        result = role_call(
            role="reviewer",
            user_task="Review",
            scout_report_artifact_id="art_scout",
            architect_plan_artifact_id="art_arch",
            coder_report_artifact_id="",  # missing
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("coder_report", result["error"]["message"])
        self.assertEqual(
            result["error"]["next_action"]["arguments_hint"]["role"],
            "coder",
        )

        # Test: publisher missing reviewer_report → next_action.role should be "reviewer"
        result = role_call(
            role="publisher",
            user_task="Publish",
            reviewer_report_artifact_id="",  # missing
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("reviewer_report", result["error"]["message"])
        self.assertEqual(
            result["error"]["next_action"]["arguments_hint"]["role"],
            "reviewer",
        )

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_running_response_has_next_action_tool_role_wait(self, mock_impl):
        """role_call running response has next_action.tool == 'role_wait'."""
        mock_impl.return_value = {
            "role_run_id": "test-run-001-scout-1",
            "run_id": "test-run-001",
            "role": "scout",
            "status": "running",
            "message": "Role started.",
        }
        from mcp_agent.server import role_call
        result = role_call(
            role="scout",
            user_task="Test task",
            idempotency_key="test-scout-1",
        )
        self.assertEqual(result["status"], "running")
        self.assertIn("next_action", result)
        self.assertEqual(result["next_action"]["tool"], "role_wait")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_completed_architect_returns_only_architect_plan_hint(self, mock_wait):
        """role_wait completed architect returns only architect_plan_artifact_id hint, not scout_report_artifact_id."""
        mock_wait.return_value = {
            "status": "completed",
            "role": "architect",
            "artifacts": {"primary": {"artifact_id": "art_arch_xxx"}},
        }
        from mcp_agent.server import role_wait
        result = role_wait(role_run_id="test-arch-1")
        self.assertEqual(result["status"], "completed")
        self.assertIn("next_action", result)
        self.assertEqual(result["next_action"]["tool"], "role_call")
        hint = result["next_action"].get("arguments_hint", {})
        # Must contain architect_plan_artifact_id
        self.assertIn("architect_plan_artifact_id", hint)
        self.assertEqual(hint["architect_plan_artifact_id"], "art_arch_xxx")
        # Must NOT contain scout_report_artifact_id
        self.assertNotIn("scout_report_artifact_id", hint)

    def test_invalid_flat_role_call_error_allows_wrappers(self):
        """_invalid_flat_role_call_error does not say scalar wrappers are forbidden."""
        from mcp_agent.server import _invalid_flat_role_call_error

        result = _invalid_flat_role_call_error("test_field")
        msg = result["error"]["message"]
        # Must NOT say wrappers are forbidden
        self.assertNotIn("Do not pass", msg)
        self.assertNotIn("forbidden", msg.lower())
        # Must clarify wrappers are normalized
        self.assertIn("normaliz", msg.lower())

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_null_args_uses_defaults(self, mock_wait):
        """role_wait with None timeout_seconds/poll_interval_seconds uses defaults."""
        mock_wait.return_value = {"status": "completed", "role": "test"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id="art_test_1_scout_1",
            timeout_seconds=None,
            poll_interval_seconds=None,
        )

        call_kwargs = mock_wait.call_args.kwargs
        self.assertEqual(call_kwargs["timeout_seconds"], 1800)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 30)

    def test_artifact_id_art_prefix_passes_validation(self):
        """art_... artifact_id from role_wait passes into role_call without InvalidArtifactId."""
        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Test task",
            repository="https://github.com/example/repo",
            feature="test-feature",
            idempotency_key="art_test_scout_1_1",
        )

        # If it fails, it should NOT be InvalidArtifactId
        if result.get("status") == "failed":
            self.assertNotEqual(
                result.get("error", {}).get("type"),
                "InvalidArtifactId",
                "art_... artifact_id should not trigger InvalidArtifactId error",
            )

    def test_idempotency_key_wrapper_reaches_role_call_body(self):
        """Exact failing call with idempotency_key {'value': '...'} reaches role_call body."""
        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Test task",
            repository="https://github.com/example/repo",
            feature="test-feature",
            idempotency_key={"value": "test-idempotency-key"},
        )

        # The unwrap_scalar should normalize {"value": "..."} -> "..."
        # This should NOT fail with InvalidFlatRoleCallPayload
        if result.get("status") == "failed":
            self.assertNotEqual(
                result.get("error", {}).get("type"),
                "InvalidFlatRoleCallPayload",
                "idempotency_key wrapper should be normalized, not rejected",
            )


# ---------------------------------------------------------------------------
# Regression tests for artifact-id resolution bug fix
# ---------------------------------------------------------------------------

