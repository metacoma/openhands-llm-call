#!/usr/bin/env python3
"""Tests for openhands_llm.server — empty-answer guards."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openhands_llm import server


class TestGetJobStatusEmptyAnswer(unittest.TestCase):
    """Server must return completed_empty_result for terminal job with empty answer."""

    @classmethod
    def setUpClass(cls):
        # Use zero retry total for existing tests to preserve old behavior
        # (one immediate fetch, no retry) and avoid test timeouts.
        os.environ["OPENHANDS_FINAL_ANSWER_RETRY_SECONDS"] = "0"

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("OPENHANDS_FINAL_ANSWER_RETRY_SECONDS", None)

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_completed_empty_result_when_answer_empty(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """_get_job_status returns completed_empty_result for terminal job with empty answer."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = ""

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertEqual(result["answer"], "")

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_completed_when_answer_nonempty(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """_get_job_status returns completed for terminal job with non-empty answer."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        mock_search.return_value = []
        mock_collect.return_value = [{"type": "text", "text": "Some answer"}]
        mock_extract.return_value = "Some answer"

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Some answer")

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_completed_when_answer_whitespace_only(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """_get_job_status returns completed_empty_result for whitespace-only answer."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = "   \n\t  "

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "completed_empty_result")

    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    def test_failed_when_exec_status_failed(
        self, mock_get_conv, mock_search, mock_collect, mock_extract
    ):
        """_get_job_status returns failed when execution_status indicates failure."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "failed",
        }
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = ""

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "failed")


class TestExecuteEmptyAnswer(unittest.TestCase):
    """_execute must return completed_empty_result when final answer is empty."""

    @patch("openhands_llm.openhands_llm_call.start_v1_app_conversation")
    @patch("openhands_llm.openhands_llm_call.run_and_collect_message_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_execute_completed_empty_result(
        self, mock_extract, mock_collect, mock_start
    ):
        """_execute returns completed_empty_result when final answer is empty."""
        mock_start.return_value = {"conversation_id": "conv-123"}
        mock_collect.return_value = []
        mock_extract.return_value = ""

        req = server.CallLMRequest(
            prompt="Test prompt",
            llm_model="openai/qwen3:32b",
            api_key="test-key",
        )

        result = server._execute(req)

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertEqual(result["answer"], "")

    @patch("openhands_llm.openhands_llm_call.start_v1_app_conversation")
    @patch("openhands_llm.openhands_llm_call.run_and_collect_message_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_execute_completed_when_answer_nonempty(
        self, mock_extract, mock_collect, mock_start
    ):
        """_execute returns completed when final answer is non-empty."""
        mock_start.return_value = {"conversation_id": "conv-123"}
        mock_collect.return_value = []
        mock_extract.return_value = "Valid answer"

        req = server.CallLMRequest(
            prompt="Test prompt",
            llm_model="openai/qwen3:32b",
            api_key="test-key",
        )

        result = server._execute(req)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Valid answer")

    @patch("openhands_llm.openhands_llm_call.collect_existing_conversation_answer")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_execute_read_only_empty_result(
        self, mock_extract, mock_collect
    ):
        """_execute returns completed_empty_result for read_only path with empty answer."""
        mock_collect.return_value = []
        mock_extract.return_value = ""

        req = server.CallLMRequest(
            prompt="Test prompt",
            llm_model="openai/qwen3:32b",
            api_key="test-key",
            conversation_id="conv-existing",
            read_only_existing_conversation=True,
        )

        result = server._execute(req)

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertEqual(result["answer"], "")

    @patch("openhands_llm.openhands_llm_call.send_message_to_existing_conversation")
    @patch("openhands_llm.openhands_llm_call.run_and_collect_message_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_execute_existing_conversation_empty_result(
        self, mock_extract, mock_collect, mock_send
    ):
        """_execute returns completed_empty_result for existing-conversation path with empty answer."""
        mock_send.return_value = {"task_id": "task-1", "job_id": "job-1"}
        mock_collect.return_value = []
        mock_extract.return_value = ""

        req = server.CallLMRequest(
            prompt="Test prompt",
            llm_model="openai/qwen3:32b",
            api_key="test-key",
            conversation_id="conv-existing",
        )

        result = server._execute(req)

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertEqual(result["answer"], "")


class TestCallLMResponseContent(unittest.TestCase):
    """POST /v1/call_lm must propagate completed_empty_result status."""

    @patch("openhands_llm.server._execute")
    def test_call_lm_propagates_completed_empty_result(self, mock_execute):
        """call_lm endpoint returns completed_empty_result status from _execute."""
        mock_execute.return_value = {
            "answer": "",
            "conversation_id": "conv-123",
            "status": "completed_empty_result",
        }

        req = server.CallLMRequest(
            prompt="Test prompt",
            llm_model="openai/qwen3:32b",
        )

        response = server.call_lm(req)

        self.assertEqual(response.status_code, 200)
        content = response.body.decode("utf-8")
        data = json.loads(content)
        self.assertEqual(data["status"], "completed_empty_result")


class TestStartV1AppConversationRunFlag(unittest.TestCase):
    """Direct test on start_v1_app_conversation payload structure."""

    @patch("openhands_llm.openhands_llm_call.request_json")
    def test_run_is_true(self, mock_request_json):
        """payload['initial_message']['run'] must be True."""
        from openhands_llm.openhands_llm_call import start_v1_app_conversation

        mock_request_json.return_value = {
            "task_id": "task-1",
            "conversation_id": "conv-1",
        }

        result = start_v1_app_conversation(
            base_url="http://localhost:3000",
            api_key="test-key",
            repo=None,
            branch=None,
            prompt="Test task",
            llm_model="openai/qwen3:32b",
            agent_type="scout",
        )

        self.assertEqual(result["task_id"], "task-1")
        captured_call = mock_request_json.call_args
        payload = captured_call.kwargs["json_body"]
        self.assertTrue(payload["initial_message"]["run"])


class TestGetJobStatusFinalAnswerRetry(unittest.TestCase):
    """Tests for the final-answer retry mechanism in _get_job_status()."""

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_terminal_job_with_immediate_answer_no_retry(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """Terminal job with answer immediately available: no unnecessary retry, returns completed."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        mock_search.return_value = [{"type": "text", "text": "Answer"}]
        mock_collect.return_value = [{"type": "text", "text": "Answer"}]
        mock_extract.return_value = "Answer"

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Answer")
        # search_v1_events should be called exactly once (no retry needed)
        self.assertEqual(mock_search.call_count, 1)

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_terminal_job_empty_then_answer_appears(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """Terminal job with empty answer initially, then answer appears: retries, returns completed."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        # First call returns empty, second call returns answer
        mock_search.side_effect = [
            [],
            [{"type": "text", "text": "Delayed Answer"}],
        ]
        mock_collect.side_effect = [
            [],
            [{"type": "text", "text": "Delayed Answer"}],
        ]
        # extract_final_answer is called only on the second iteration
        # (first iteration has empty answers, so extract is skipped)
        mock_extract.return_value = "Delayed Answer"

        # Use a short retry window and mock time.monotonic to stay
        # within the window for multiple fetches.
        with patch("time.sleep"), patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "10",
            "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "1",
        }):
            # deadline = 0.0 + 10 = 10.0
            # Iteration 1: fetch=[], answer="", now=0.0<10.0, sleep, check=0.0<10.0
            # Iteration 2: fetch=[...], answer="Delayed Answer", return
            monotonic_calls = [0.0, 0.0, 0.0, 0.0]
            with patch("time.monotonic", side_effect=monotonic_calls):
                result = server._get_job_status(
                    "conv-123", "http://localhost:3000", "test-key"
                )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Delayed Answer")
        # search_v1_events called twice (initial + one retry)
        self.assertEqual(mock_search.call_count, 2)

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_terminal_job_always_empty_returns_completed_empty_result(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """Terminal job with answer always empty: retries until timeout, returns completed_empty_result."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        # Always returns empty
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = ""

        with patch("time.sleep"), patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "1",
            "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "1",
        }):
            result = server._get_job_status(
                "conv-123", "http://localhost:3000", "test-key"
            )

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertEqual(result["answer"], "")

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_failed_job_remains_failed(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """Failed terminal job: remains failed, does not become completed."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "failed",
        }
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = ""

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["answer"], "")
        # search_v1_events called once (no retry for failed jobs)
        self.assertEqual(mock_search.call_count, 1)

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_error_job_remains_failed(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """Error terminal job: remains failed, does not become completed."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "error",
        }
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = ""

        result = server._get_job_status(
            "conv-123", "http://localhost:3000", "test-key"
        )

        self.assertEqual(result["status"], "failed")

    def test_invalid_env_values_fallback_to_defaults(self):
        """Invalid env values: fallback to defaults, no crash."""
        # Test with invalid retry_total
        with patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "invalid",
            "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "invalid",
        }):
            total, interval = server._final_answer_retry_config_from_env()
            self.assertEqual(total, 60)
            self.assertEqual(interval, 10)

        # Test with negative retry_total
        with patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "-10",
        }):
            total, interval = server._final_answer_retry_config_from_env()
            self.assertEqual(total, 60)

        # Test with zero retry_interval
        with patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "0",
        }):
            total, interval = server._final_answer_retry_config_from_env()
            self.assertEqual(interval, 10)

        # Test with negative retry_interval
        with patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "-5",
        }):
            total, interval = server._final_answer_retry_config_from_env()
            self.assertEqual(interval, 10)

        # Test with retry_total=0 (one immediate fetch, no retry)
        with patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "0",
        }):
            total, interval = server._final_answer_retry_config_from_env()
            self.assertEqual(total, 0)

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_retry_total_zero_preserves_old_behavior(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """retry_total=0: one immediate fetch, no retry loop."""
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }
        mock_search.return_value = []
        mock_collect.return_value = []
        mock_extract.return_value = ""

        with patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "0",
        }):
            result = server._get_job_status(
                "conv-123", "http://localhost:3000", "test-key"
            )

        self.assertEqual(result["status"], "completed_empty_result")
        # Only one call to search_v1_events (no retry)
        self.assertEqual(mock_search.call_count, 1)

    @patch("openhands_llm.openhands_llm_call.get_v1_conversation")
    @patch("openhands_llm.openhands_llm_call.search_v1_events")
    @patch("openhands_llm.openhands_llm_call.collect_final_text_from_events")
    @patch("openhands_llm.openhands_llm_call.extract_final_answer")
    def test_answer_appears_near_retry_deadline(
        self, mock_extract, mock_collect, mock_search, mock_get_conv
    ):
        """Answer becomes available on the final retry check at/near deadline: returns completed, not completed_empty_result.

        This test verifies Blocker 2 is fixed: the retry loop must perform
        one final fetch after sleeping up to the deadline, so answers that
        appear near the deadline boundary are not missed.
        """
        mock_get_conv.return_value = {
            "id": "conv-123",
            "execution_status": "finished",
        }

        # First fetch (t=0) returns empty; second fetch (at deadline) returns answer
        mock_search.side_effect = [[], [{"type": "text", "text": "Deadline Answer"}]]
        mock_collect.side_effect = [
            [],
            [{"type": "text", "text": "Deadline Answer"}],
        ]
        mock_extract.return_value = "Deadline Answer"

        with patch("time.sleep"), patch.dict(os.environ, {
            "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "10",
            "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "10",
        }):
            # deadline = 0.0 + 10 = 10.0
            # Iteration 1: t=0, fetch=[], empty, now=0<10, sleep(10)
            # After sleep: time.monotonic() returns 10.0 >= 10.0 (deadline)
            #   OLD BUG: break → return empty
            #   FIX: loop continues → Iteration 2: fetch=[...], answer found → return completed
            monotonic_calls = [
                0.0,    # line 90: deadline = 0.0 + 10 = 10.0
                0.0,    # line 112: now = 0.0 (after first fetch)
                10.0,   # after sleep: time.monotonic() >= deadline (10.0 >= 10.0)
                10.0,   # line 112: now = 10.0 (second fetch, at deadline)
            ]
            with patch("time.monotonic", side_effect=monotonic_calls):
                result = server._get_job_status(
                    "conv-123", "http://localhost:3000", "test-key"
                )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Deadline Answer")
        # Two fetches: initial + one final at deadline
        self.assertEqual(mock_search.call_count, 2)

    def test_default_timeout_exceeds_retry_window(self):
        """Default MCP request timeout (90) > default retry window (60)."""
        retry_total, _ = server._final_answer_retry_config_from_env()
        self.assertEqual(retry_total, 60)  # Verify the known default
        # The actual timeout default is verified by inspection at role_lifecycle.py:235.
        # A runtime check requires module reload; document the invariant instead.
        self.assertGreater(90, retry_total, "Timeout must exceed retry window")


if __name__ == "__main__":
    unittest.main()
