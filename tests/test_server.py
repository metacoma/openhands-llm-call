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


if __name__ == "__main__":
    unittest.main()
