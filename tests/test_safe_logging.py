#!/usr/bin/env python3
"""Tests for mcp_agent.safe_logging helpers."""

import json
import os
import sys
from pathlib import Path
from unittest import TestCase, main as unittest_main
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_agent.safe_logging import (
    safe_preview,
    safe_json_shape,
    correlate_id_from_args,
    format_correlation,
    SENSITIVE_KEYS,
    is_debug_enabled,
    debug_log,
)


class TestSafePreview(TestCase):
    def test_none_returns_none_marker(self):
        self.assertEqual(safe_preview(None), "(none)")

    def test_short_string_unchanged(self):
        self.assertEqual(safe_preview("hello", 300), "hello")

    def test_long_string_truncated(self):
        result = safe_preview("a" * 400, 300)
        self.assertEqual(len(result), 303)  # 300 chars + "..."
        self.assertTrue(result.endswith("..."))

    def test_non_string_converted(self):
        self.assertEqual(safe_preview(123), "123")
        self.assertEqual(safe_preview({"a": 1}), str({"a": 1})[:300])


class TestSafeJsonShape(TestCase):
    def test_redacts_api_key(self):
        result = safe_json_shape({"prompt": "ping", "api_key": "secret"})
        self.assertIsInstance(result, dict)
        self.assertTrue(result["api_key"]["redacted"])

    def test_reports_prompt_type_and_len(self):
        result = safe_json_shape({"prompt": "hello"})
        self.assertEqual(result["prompt"]["type"], "str")
        # For short strings (≤ preview_limit), value is returned directly
        self.assertEqual(result["prompt"].get("value"), "hello")

    def test_handles_none(self):
        result = safe_json_shape(None)
        self.assertEqual(result["type"], "NoneType")

    def test_handles_bool(self):
        result = safe_json_shape(True)
        self.assertEqual(result["type"], "bool")
        self.assertTrue(result["value"])

    def test_handles_int(self):
        result = safe_json_shape(42)
        self.assertEqual(result["type"], "int")
        self.assertEqual(result["value"], 42)

    def test_handles_list(self):
        result = safe_json_shape([1, "two", {"three": 3}])
        self.assertEqual(result["type"], "list")
        self.assertEqual(result["len"], 3)

    def test_handles_nested_sensitive_keys(self):
        result = safe_json_shape({
            "metadata": {"api_key": "secret123", "repository": "owner/repo"},
        })
        self.assertTrue(result["metadata"]["api_key"]["redacted"])
        self.assertEqual(result["metadata"]["repository"]["value"], "owner/repo")

    def test_long_string_has_preview(self):
        result = safe_json_shape({"prompt": "x" * 300})
        self.assertIn("len", result["prompt"])
        self.assertIn("preview", result["prompt"])
        self.assertEqual(result["prompt"]["len"], 300)

    def test_redacts_password_and_token(self):
        result = safe_json_shape({
            "password": "secret",
            "token": "tok123",
            "authorization": "Bearer xxx",
        })
        self.assertTrue(result["password"]["redacted"])
        self.assertTrue(result["token"]["redacted"])
        self.assertTrue(result["authorization"]["redacted"])


class TestCorrelateId(TestCase):
    def test_prefers_role_run_id(self):
        self.assertEqual(correlate_id_from_args(role_run_id="rr-123"), "rr-123")

    def test_falls_back_to_run_id(self):
        self.assertEqual(correlate_id_from_args(run_id="run-456"), "run-456")

    def test_falls_back_to_idempotency_key(self):
        self.assertEqual(
            correlate_id_from_args(idempotency_key="idem-789"), "idem-789"
        )

    def test_prefers_role_run_id_over_run_id(self):
        self.assertEqual(
            correlate_id_from_args(role_run_id="rr-1", run_id="run-2"), "rr-1"
        )

    def test_prefers_run_id_over_idempotency_key(self):
        self.assertEqual(
            correlate_id_from_args(run_id="run-2", idempotency_key="idem-3"), "run-2"
        )

    def test_generates_uuid_when_all_none(self):
        result = correlate_id_from_args()
        self.assertTrue(result.startswith("gen-"))
        self.assertEqual(len(result), 12)  # "gen-" + 8 hex chars

    def test_ignores_empty_role_run_id(self):
        self.assertEqual(
            correlate_id_from_args(role_run_id="", run_id="run-456"), "run-456"
        )

    def test_ignores_whitespace_role_run_id(self):
        self.assertEqual(
            correlate_id_from_args(role_run_id="  ", run_id="run-456"), "run-456"
        )


class TestFormatCorrelation(TestCase):
    def test_minimal(self):
        result = format_correlation("test-id")
        self.assertEqual(result, "correlation_id=test-id")

    def test_with_role(self):
        result = format_correlation("test-id", role="scout")
        self.assertIn("correlation_id=test-id", result)
        self.assertIn("role=scout", result)

    def test_with_idempotency_key(self):
        result = format_correlation("test-id", idempotency_key="idem-1")
        self.assertIn("correlation_id=test-id", result)
        self.assertIn("idempotency_key=idem-1", result)

    def test_all_fields(self):
        result = format_correlation(
            "test-id", role="scout", idempotency_key="idem-1"
        )
        self.assertIn("correlation_id=test-id", result)
        self.assertIn("role=scout", result)
        self.assertIn("idempotency_key=idem-1", result)


# ---------------------------------------------------------------------------
# Test 3: role_call logging does not crash on wrapped args
# ---------------------------------------------------------------------------

class TestHttpErrorIncludesBody(TestCase):
    """Test that HTTP error response includes the body in the exception."""

    def test_error_includes_body(self):
        """Mock /v1/call_lm returning 422 with detail body."""
        from mcp_agent.role_lifecycle import _start_conversation_on_fastapi

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = json.dumps({
            "detail": [
                {"loc": ["body", "prompt"], "msg": "Input should be a valid string"},
            ]
        })
        mock_response.raise_for_status.side_effect = Exception("422 Client Error")

        with patch("requests.post", return_value=mock_response):
            with self.assertRaises(Exception) as ctx:
                _start_conversation_on_fastapi(
                    prompt="ping",
                    api_key="dummy",
                )

            error_msg = str(ctx.exception)
            self.assertIn("HTTP 422", error_msg)
            self.assertIn("Input should be a valid string", error_msg)

    def test_error_includes_status_code(self):
        """Mock /v1/call_lm returning 422 — exception message includes status."""
        from mcp_agent.role_lifecycle import _start_conversation_on_fastapi

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = '{"detail": [{"msg": "bad prompt"}]}'
        mock_response.raise_for_status.side_effect = Exception("422")

        with patch("requests.post", return_value=mock_response):
            with self.assertRaises(Exception) as ctx:
                _start_conversation_on_fastapi(
                    prompt="ping",
                    api_key="dummy",
                )

            self.assertIn("422", str(ctx.exception))


# ---------------------------------------------------------------------------
# Test: OPENHANDS_LLM_CALL_DEBUG env var
# ---------------------------------------------------------------------------

class TestDebugEnvVar(TestCase):
    """Test is_debug_enabled() and debug_log() with OPENHANDS_LLM_CALL_DEBUG."""

    def setUp(self):
        self._orig = os.environ.pop("OPENHANDS_LLM_CALL_DEBUG", None)

    def tearDown(self):
        if self._orig is not None:
            os.environ["OPENHANDS_LLM_CALL_DEBUG"] = self._orig
        else:
            os.environ.pop("OPENHANDS_LLM_CALL_DEBUG", None)

    def test_missing_env_returns_false(self):
        os.environ.pop("OPENHANDS_LLM_CALL_DEBUG", None)
        self.assertFalse(is_debug_enabled())

    def test_truthy_1(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "1"
        self.assertTrue(is_debug_enabled())

    def test_truthy_true(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "true"
        self.assertTrue(is_debug_enabled())

    def test_truthy_TRUE(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "TRUE"
        self.assertTrue(is_debug_enabled())

    def test_truthy_yes(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "yes"
        self.assertTrue(is_debug_enabled())

    def test_truthy_on(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "on"
        self.assertTrue(is_debug_enabled())

    def test_truthy_debug(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "debug"
        self.assertTrue(is_debug_enabled())

    def test_falsy_0(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "0"
        self.assertFalse(is_debug_enabled())

    def test_falsy_empty(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = ""
        self.assertFalse(is_debug_enabled())

    def test_debug_log_no_crash_when_disabled(self):
        os.environ.pop("OPENHANDS_LLM_CALL_DEBUG", None)
        logger = __import__("logging").getLogger("test_debug_log_disabled")
        # Should not raise
        debug_log(logger, "test_msg", foo="bar")

    def test_debug_log_no_crash_when_enabled(self):
        os.environ["OPENHANDS_LLM_CALL_DEBUG"] = "1"
        logger = __import__("logging").getLogger("test_debug_log_enabled")
        logger.setLevel(__import__("logging").DEBUG)
        # Should not raise
        debug_log(logger, "test_msg", foo="bar")


if __name__ == "__main__":
    unittest_main()
