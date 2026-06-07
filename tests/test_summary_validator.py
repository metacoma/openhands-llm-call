#!/usr/bin/env python3
"""Tests for summary validator (mcp_agent.summary_validator)."""

import json
import os
import shutil
import tempfile
import unittest


class TestValidateSummary(unittest.TestCase):
    """Test validate_summary function."""

    def setUp(self):
        self.valid_summary = {
            "status": "completed",
            "role": "scout",
            "summary": "Scout completed successfully.",
            "primary_artifact_name": "scout_report",
            "blocking": False,
            "risk_level": "LOW",
            "action": None,
            "blocking_summary": [],
        }

    def _make_json(self, overrides=None):
        """Create a JSON string from the base summary with optional overrides."""
        data = dict(self.valid_summary)
        if overrides:
            data.update(overrides)
        return json.dumps(data)

    def test_valid_summary_passes(self):
        """Valid summary JSON passes validation."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json(),
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["role"], "scout")
        self.assertEqual(result["status"], "completed")

    def test_invalid_json_returns_error(self):
        """Invalid JSON returns error dict."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str="{invalid json",
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryParseError")

    def test_missing_required_fields_returns_error(self):
        """Missing required fields returns error dict."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=json.dumps({"status": "completed"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryMissingFields")
        self.assertIn("role", result["error"]["message"])

    def test_role_mismatch_returns_error(self):
        """Role mismatch returns error."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"role": "architect"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryRoleMismatch")

    def test_primary_artifact_mismatch_returns_error(self):
        """Primary artifact name mismatch returns error."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="wrong_artifact",
            json_str=self._make_json(),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(
            result["error"]["type"], "SummaryArtifactMismatch"
        )

    def test_blocking_non_boolean_returns_error(self):
        """blocking non-boolean returns error."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"blocking": "true"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryInvalidField")

    def test_blocking_summary_non_list_returns_error(self):
        """blocking_summary non-list returns error."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"blocking_summary": "not a list"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryInvalidField")

    def test_reviewer_action_pass_validates(self):
        """Reviewer with action=PASS passes."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=self._make_json({
                "role": "reviewer",
                "primary_artifact_name": "reviewer_report",
                "action": "PASS",
            }),
        )
        self.assertTrue(result.get("valid"))

    def test_reviewer_action_blocker_validates(self):
        """Reviewer with action=BLOCKER passes."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=self._make_json({
                "role": "reviewer",
                "primary_artifact_name": "reviewer_report",
                "action": "BLOCKER",
                "blocking": True,
                "blocking_summary": ["Blocker 1"],
            }),
        )
        self.assertTrue(result.get("valid"))

    def test_reviewer_action_null_fails(self):
        """Reviewer with action=null fails."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=self._make_json({
                "role": "reviewer",
                "primary_artifact_name": "reviewer_report",
                "action": None,
            }),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryInvalidField")

    def test_non_reviewer_action_null_validates(self):
        """Non-reviewer with action=null passes."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"action": None}),
        )
        self.assertTrue(result.get("valid"))

    def test_non_reviewer_action_pass_fails(self):
        """Non-reviewer with action=PASS fails."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"action": "PASS"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryInvalidField")

    def test_forbidden_next_role_field_fails(self):
        """Summary with next_role field fails."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"next_role": "architect"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(
            result["error"]["type"], "SummaryForbiddenFields"
        )

    def test_forbidden_ready_for_next_role_field_fails(self):
        """Summary with ready_for_next_role field fails."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_json({"ready_for_next_role": True}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(
            result["error"]["type"], "SummaryForbiddenFields"
        )

    def test_markdown_code_block_wrapping_stripped(self):
        """JSON wrapped in ```json ... ``` is parsed correctly."""
        from mcp_agent.summary_validator import validate_summary

        json_str = '```json\n' + self._make_json() + '\n```'
        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=json_str,
        )
        self.assertTrue(result.get("valid"))


class TestRepairSummary(unittest.TestCase):
    """Test repair_summary function."""

    def test_repair_prompt_contains_json_schema(self):
        """Repair prompt contains JSON schema."""
        from mcp_agent.summary_validator import repair_summary

        prompt = repair_summary(
            role="scout",
            summary_artifact_name="scout_report",
        )
        self.assertIn("JSON", prompt)
        self.assertIn("status", prompt)
        self.assertIn("role", prompt)
        self.assertIn("summary", prompt)
        self.assertIn("primary_artifact_name", prompt)
        self.assertIn("blocking", prompt)
        self.assertIn("risk_level", prompt)
        self.assertIn("action", prompt)
        self.assertIn("blocking_summary", prompt)

    def test_repair_prompt_includes_role(self):
        """Repair prompt includes role-specific info."""
        from mcp_agent.summary_validator import repair_summary

        prompt = repair_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
        )
        self.assertIn("JSON", prompt)


class TestSafeFallbackSummary(unittest.TestCase):
    """Test safe_fallback_summary function."""

    def test_safe_fallback_for_non_reviewer(self):
        """Safe fallback for non-reviewer role."""
        from mcp_agent.summary_validator import safe_fallback_summary

        result = safe_fallback_summary(
            role="scout",
            summary_artifact_name="scout_summary",
            is_reviewer=False,
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["role"], "scout")
        self.assertEqual(result["primary_artifact_name"], "scout_summary")
        self.assertFalse(result["blocking"])
        self.assertIsNone(result["risk_level"])
        self.assertIsNone(result["action"])
        self.assertEqual(result["blocking_summary"], [])

    def test_safe_fallback_for_reviewer_no_derivation(self):
        """Safe fallback for reviewer when action cannot be derived.

        When the reviewer summary cannot be parsed AND ACTION cannot be
        derived from the main artifact, the fallback MUST block the pipeline
        to prevent unsafe PASS/BLOCKER routing by Head of Engineering.
        """
        from mcp_agent.summary_validator import safe_fallback_summary

        result = safe_fallback_summary(
            role="reviewer",
            summary_artifact_name="reviewer_summary",
            is_reviewer=True,
            main_artifact_content="No ACTION line found.",
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["role"], "reviewer")
        self.assertEqual(result["action"], "BLOCKER")
        self.assertTrue(result["blocking"])
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(
            result["blocking_summary"],
            [
                "Reviewer summary parsing failed and ACTION could not be "
                "derived safely.",
            ],
        )

    def test_safe_fallback_for_reviewer_no_derivation_returns_blocker(self):
        """Safe fallback for reviewer when action cannot be derived MUST return BLOCKER.

        This is a critical regression test: reviewer is the only role that
        controls PASS/BLOCKER routing. If fallback returns action=null,
        Head of Engineering cannot safely route to publisher or coder_fix.
        """
        from mcp_agent.summary_validator import safe_fallback_summary

        result = safe_fallback_summary(
            role="reviewer",
            summary_artifact_name="reviewer_summary",
            is_reviewer=True,
            main_artifact_content="No ACTION line found.",
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["action"], "BLOCKER")
        self.assertTrue(result["blocking"])
        self.assertEqual(result["risk_level"], "HIGH")

    def test_safe_fallback_for_reviewer_with_derivation(self):
        """Safe fallback for reviewer when action can be derived."""
        from mcp_agent.summary_validator import safe_fallback_summary

        result = safe_fallback_summary(
            role="reviewer",
            summary_artifact_name="reviewer_summary",
            is_reviewer=True,
            main_artifact_content="ACTION: PASS\nSome text",
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action"], "PASS")


class TestDeriveReviewerAction(unittest.TestCase):
    """Test derive_reviewer_action_from_main_artifact function."""

    def test_derive_pass(self):
        """Derives ACTION: PASS from main artifact."""
        from mcp_agent.summary_validator import (
            derive_reviewer_action_from_main_artifact,
        )

        result = derive_reviewer_action_from_main_artifact(
            "ACTION: PASS\nSome text"
        )
        self.assertEqual(result, "PASS")

    def test_derive_blocker(self):
        """Derives ACTION: BLOCKER from main artifact."""
        from mcp_agent.summary_validator import (
            derive_reviewer_action_from_main_artifact,
        )

        result = derive_reviewer_action_from_main_artifact(
            "ACTION: BLOCKER\nSome text"
        )
        self.assertEqual(result, "BLOCKER")

    def test_derive_case_insensitive(self):
        """Derivation is case-insensitive."""
        from mcp_agent.summary_validator import (
            derive_reviewer_action_from_main_artifact,
        )

        result = derive_reviewer_action_from_main_artifact(
            "action: pass\nSome text"
        )
        self.assertEqual(result, "PASS")

    def test_derive_missing_returns_none(self):
        """Returns None when ACTION not found."""
        from mcp_agent.summary_validator import (
            derive_reviewer_action_from_main_artifact,
        )

        result = derive_reviewer_action_from_main_artifact(
            "No action line here."
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
