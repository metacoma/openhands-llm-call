#!/usr/bin/env python3
"""Regression tests for LLM-friendly tolerant wrapper acceptance.

Tests that public MCP tools (role_call, role_wait) accept and normalize
LLM-generated argument wrappers like {"value": "..."}, {"text": "..."},
case-insensitive roles, string ints/bools, etc.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure the project root is on sys.path so imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_agent.server import (
    _normalize_mcp_string,
    _normalize_mcp_int,
    _normalize_mcp_bool,
    _normalize_mcp_role,
    _looks_like_artifact_content,
    _artifact_content_error,
    _compute_payload_fingerprint,
    _check_loop_guard,
    _invalid_call_fingerprints,
)


class TestNormalizeMcpString(unittest.TestCase):
    """Test _normalize_mcp_string helper."""

    def test_plain_string(self):
        self.assertEqual(_normalize_mcp_string("test"), "test")

    def test_value_wrapper(self):
        self.assertEqual(_normalize_mcp_string({"value": "test"}), "test")

    def test_text_wrapper(self):
        self.assertEqual(_normalize_mcp_string({"text": "test"}), "test")

    def test_default_wrapper(self):
        self.assertEqual(_normalize_mcp_string({"default": "test"}), "test")

    def test_name_wrapper(self):
        self.assertEqual(_normalize_mcp_string({"name": "test"}), "test")

    def test_none_returns_empty(self):
        self.assertEqual(_normalize_mcp_string(None), "")

    def test_empty_string(self):
        self.assertEqual(_normalize_mcp_string(""), "")

    def test_number_converted_to_string(self):
        self.assertEqual(_normalize_mcp_string(123), "123")

    def test_nested_wrappers(self):
        self.assertEqual(_normalize_mcp_string({"value": {"text": "test"}}), "test")


class TestNormalizeMcpInt(unittest.TestCase):
    """Test _normalize_mcp_int helper."""

    def test_plain_int(self):
        self.assertEqual(_normalize_mcp_int(1800), 1800)

    def test_value_wrapper(self):
        self.assertEqual(_normalize_mcp_int({"value": 1800}), 1800)

    def test_text_wrapper_string(self):
        self.assertEqual(_normalize_mcp_int({"text": "1800"}), 1800)

    def test_string_int(self):
        self.assertEqual(_normalize_mcp_int("1800"), 1800)

    def test_none_returns_none(self):
        """_normalize_mcp_int(None) returns None so callers can use normalize_int(value, default=...)."""
        self.assertIsNone(_normalize_mcp_int(None))

    def test_float(self):
        self.assertEqual(_normalize_mcp_int(1800.5), 1800)


class TestNormalizeMcpBool(unittest.TestCase):
    """Test _normalize_mcp_bool helper."""

    def test_plain_true(self):
        self.assertTrue(_normalize_mcp_bool(True))

    def test_plain_false(self):
        self.assertFalse(_normalize_mcp_bool(False))

    def test_value_wrapper_true(self):
        self.assertTrue(_normalize_mcp_bool({"value": True}))

    def test_text_wrapper_string_true(self):
        self.assertTrue(_normalize_mcp_bool({"text": "true"}))

    def test_text_wrapper_string_false(self):
        self.assertFalse(_normalize_mcp_bool({"text": "false"}))

    def test_string_yes(self):
        self.assertTrue(_normalize_mcp_bool("yes"))

    def test_string_no(self):
        self.assertFalse(_normalize_mcp_bool("no"))

    def test_string_1(self):
        self.assertTrue(_normalize_mcp_bool("1"))

    def test_string_0(self):
        self.assertFalse(_normalize_mcp_bool("0"))

    def test_none_returns_false(self):
        self.assertFalse(_normalize_mcp_bool(None))


class TestNormalizeMcpRole(unittest.TestCase):
    """Test _normalize_mcp_role helper."""

    def test_plain_lowercase(self):
        self.assertEqual(_normalize_mcp_role("scout"), "scout")

    def test_case_insensitive_upper(self):
        self.assertEqual(_normalize_mcp_role("SCOUT"), "scout")

    def test_case_insensitive_mixed(self):
        self.assertEqual(_normalize_mcp_role("Scout"), "scout")

    def test_whitespace_stripped(self):
        self.assertEqual(_normalize_mcp_role(" scout "), "scout")

    def test_value_wrapper(self):
        self.assertEqual(_normalize_mcp_role({"value": "Scout"}), "scout")

    def test_text_wrapper(self):
        self.assertEqual(_normalize_mcp_role({"text": "ARCHITECT"}), "architect")

    def test_all_roles_normalized(self):
        for role in ("scout", "architect", "coder", "reviewer", "publisher", "coder_fix"):
            self.assertEqual(_normalize_mcp_role(role.upper()), role)


class TestLooksLikeArtifactContent(unittest.TestCase):
    """Test _looks_like_artifact_content helper."""

    def test_plain_string_false(self):
        self.assertFalse(_looks_like_artifact_content("art_123"))

    def test_short_content_false(self):
        """Short content (<=20 chars) should not be flagged."""
        self.assertFalse(_looks_like_artifact_content({"content": "short"}))

    def test_long_content_true(self):
        self.assertTrue(_looks_like_artifact_content({"content": "this is a very long content string"}))

    def test_artifact_content_key_true(self):
        self.assertTrue(_looks_like_artifact_content({"artifact_content": "some content"}))

    def test_full_result_true(self):
        self.assertTrue(_looks_like_artifact_content({"full_result": "data"}))

    def test_result_true(self):
        self.assertTrue(_looks_like_artifact_content({"result": "data"}))

    def test_messages_true(self):
        self.assertTrue(_looks_like_artifact_content({"messages": [{"role": "user", "content": "hi"}]}))

    def test_tool_calls_true(self):
        self.assertTrue(_looks_like_artifact_content({"tool_calls": [{"name": "role_call"}]}))

    def test_dict_with_artifact_id_false(self):
        """Single-key dict with artifact_id is not flagged."""
        self.assertFalse(_looks_like_artifact_content({"artifact_id": "art_123"}))


class TestArtifactContentError(unittest.TestCase):
    """Test _artifact_content_error helper."""

    def test_returns_structured_error(self):
        result = _artifact_content_error("scout_report_artifact_id")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ArtifactContentAsId")
        self.assertTrue(result["error"]["retryable"])
        self.assertIn("artifact content", result["error"]["message"].lower())
        self.assertIn("art_abc123", result["error"]["correct_example"]["scout_report_artifact_id"])


class TestComputePayloadFingerprint(unittest.TestCase):
    """Test _compute_payload_fingerprint helper."""

    def test_deterministic(self):
        payload = {"role": "scout", "user_task": "test"}
        fp1 = _compute_payload_fingerprint(payload)
        fp2 = _compute_payload_fingerprint(payload)
        self.assertEqual(fp1, fp2)

    def test_different_payloads_different_fingerprints(self):
        fp1 = _compute_payload_fingerprint({"role": "scout"})
        fp2 = _compute_payload_fingerprint({"role": "architect"})
        self.assertNotEqual(fp1, fp2)

    def test_returns_32_chars(self):
        fp = _compute_payload_fingerprint({"role": "scout"})
        self.assertEqual(len(fp), 32)


class TestCheckLoopGuard(unittest.TestCase):
    """Test _check_loop_guard helper."""

    def setUp(self):
        # Reset module-level state
        import mcp_agent.server as server_mod
        server_mod._invalid_call_fingerprints.clear()

    def tearDown(self):
        import mcp_agent.server as server_mod
        server_mod._invalid_call_fingerprints.clear()

    def test_first_call_no_error(self):
        result = _check_loop_guard("role_call", {"role": "scout"})
        self.assertIsNone(result)

    def test_second_call_no_error(self):
        _check_loop_guard("role_call", {"role": "scout"})
        result = _check_loop_guard("role_call", {"role": "scout"})
        self.assertIsNone(result)

    def test_third_call_returns_error(self):
        _check_loop_guard("role_call", {"role": "scout"})
        _check_loop_guard("role_call", {"role": "scout"})
        result = _check_loop_guard("role_call", {"role": "scout"})
        self.assertIsNotNone(result)
        self.assertEqual(result["error"]["type"], "RepeatedInvalidToolCall")
        self.assertFalse(result["error"]["retryable"])

    def test_different_payload_no_loop(self):
        _check_loop_guard("role_call", {"role": "scout"})
        _check_loop_guard("role_call", {"role": "architect"})
        result = _check_loop_guard("role_call", {"role": "scout"})
        self.assertIsNone(result)


class TestTolerantWrapperIntegration(unittest.TestCase):
    """Integration tests for LLM-friendly wrapper acceptance via role_call/role_wait."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_tolerant_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_accepts_idempotency_key_value_wrapper(self, mock_impl):
        """idempotency_key: {'value': '...'} normalizes to plain string."""
        mock_impl.return_value = {"status": "running", "role_run_id": "run-123"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Analyze repository",
            repository="https://github.com/example/repo",
            feature="ruby-grpc-client",
            idempotency_key={"value": "freeplane-plugin-grpc-ruby-client-scout"},
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(
            call_kwargs["idempotency_key"],
            "freeplane-plugin-grpc-ruby-client-scout",
        )

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_accepts_wrapped_role(self, mock_impl):
        """role: {'value': 'scout'} normalizes to 'scout'."""
        mock_impl.return_value = {"status": "running", "role_run_id": "run-123"}

        from mcp_agent.server import role_call

        result = role_call(
            role={"value": "scout"},
            user_task="Analyze repository",
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role"], "scout")

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_case_insensitive_role(self, mock_impl):
        """role: ' Scout ' normalizes to 'scout'."""
        mock_impl.return_value = {"status": "running", "role_run_id": "run-123"}

        from mcp_agent.server import role_call

        result = role_call(
            role=" Scout ",
            user_task="Analyze repository",
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role"], "scout")

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_accepts_wrapped_user_task(self, mock_impl):
        """user_task: {'text': '...'} normalizes to plain string."""
        mock_impl.return_value = {"status": "running", "role_run_id": "run-123"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task={"text": "Analyze repository"},
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["user_task"], "Analyze repository")

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_accepts_wrapped_repository(self, mock_impl):
        """repository: {'value': '...'} normalizes to plain string."""
        mock_impl.return_value = {"status": "running", "role_run_id": "run-123"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Analyze repository",
            repository={"value": "https://github.com/example/repo"},
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_accepts_wrapped_feature(self, mock_impl):
        """feature: {'text': '...'} normalizes to plain string."""
        mock_impl.return_value = {"status": "running", "role_run_id": "run-123"}

        from mcp_agent.server import role_call

        result = role_call(
            role="scout",
            user_task="Analyze repository",
            feature={"text": "ruby-grpc-client"},
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_accepts_wrapped_values(self, mock_impl):
        """role_wait accepts all wrapped argument types."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"value": "run-123"},
            timeout_seconds={"text": "1800"},
            poll_interval_seconds={"value": 30},
            return_result={"text": "true"},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "run-123")
        self.assertEqual(call_kwargs["timeout_seconds"], 1800)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 30)
        self.assertTrue(call_kwargs["return_result"])

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_role_wait_accepts_string_ints_and_bools(self, mock_impl):
        """role_wait accepts string ints and string bools."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id="run-123",
            timeout_seconds="1800",
            poll_interval_seconds="30",
            return_result="false",
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["timeout_seconds"], 1800)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 30)
        self.assertFalse(call_kwargs["return_result"])

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_rejects_nested_metadata(self, mock_impl):
        """Nested metadata in a valid field is rejected with structured error.

        The MCP framework passes raw dicts to Pydantic for validation.
        We test this by passing a nested payload in a valid parameter
        (scout_report_artifact_id) that contains metadata-like keys.
        """
        from mcp_agent.server import role_call

        # Pass a dict with metadata-like keys as scout_report_artifact_id
        # This triggers the nested payload detection in _raw_fields loop
        result = role_call(
            role="scout",
            user_task="Analyze repository",
            scout_report_artifact_id={"metadata": {"foo": "bar"}},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "InvalidFlatRoleCallPayload")
        # The error message mentions nested payloads generically
        self.assertIn("nested", result["error"]["message"].lower())

    @patch("mcp_agent.role_lifecycle.role_call_start_impl")
    def test_role_call_rejects_artifact_content_as_id(self, mock_impl):
        """Full artifact content passed as artifact_id is rejected."""
        from mcp_agent.server import role_call

        result = role_call(
            role="architect",
            user_task="Plan implementation",
            scout_report_artifact_id={"content": "full scout report text here"},
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn(result["error"]["type"], ["ArtifactContentAsId", "InvalidFlatRoleCallPayload"])

    def test_public_tool_discovery_exactly_three(self):
        """Public MCP discovery exposes exactly role_list, role_call, role_wait."""
        from mcp_agent.server import role_list

        result = role_list()
        allowed = result["tools"]["allowed"]
        forbidden = result["tools"]["forbidden"]

        self.assertIn("role_list", allowed)
        self.assertIn("role_call", allowed)
        self.assertIn("role_wait", allowed)
        self.assertEqual(len(allowed), 3, "Exactly 3 allowed tools")

        # Verify legacy tools are in forbidden list
        for legacy in ["shttp_role_call", "role_start", "role_status", "role_result", "artifact_get"]:
            self.assertIn(legacy, forbidden, f"{legacy} should be forbidden")


if __name__ == "__main__":
    unittest.main()
