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


class TestValidateSummaryStructuredText(unittest.TestCase):
    """Test validate_summary with structured text format."""

    def _make_structured(self, overrides=None):
        """Create a structured-text summary with optional overrides."""
        lines = [
            "ROLE_SUMMARY_BEGIN",
            "STATUS: completed",
            "ROLE: scout",
            "PRIMARY_ARTIFACT: scout_report",
            "BLOCKING: no",
            "RISK: LOW",
            "ACTION: NONE",
            "SUMMARY: Scout completed successfully.",
            "BLOCKERS:",
            "- none",
            "ROLE_SUMMARY_END",
        ]
        if overrides:
            new_lines = []
            for line in lines:
                for key, val in overrides.items():
                    prefix = f"{key}:"
                    if line.strip().startswith(prefix):
                        new_lines.append(f"{key}: {val}")
                        break
                else:
                    new_lines.append(line)
            lines = new_lines
        return "\n".join(lines)

    def test_valid_structured_non_reviewer_passes(self):
        """Valid non-reviewer structured summary with ACTION: NONE parses to action is None."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured(),
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["role"], "scout")
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["action"])
        self.assertFalse(result["blocking"])

    def test_valid_reviewer_action_pass(self):
        """Valid reviewer summary with ACTION: PASS."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=self._make_structured({
                "ROLE": "reviewer",
                "PRIMARY_ARTIFACT": "reviewer_report",
                "ACTION": "PASS",
            }),
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["action"], "PASS")

    def test_valid_reviewer_action_blocker_with_blockers(self):
        """Valid reviewer summary with ACTION: BLOCKER and blockers list."""
        from mcp_agent.summary_validator import validate_summary

        text = (
            "ROLE_SUMMARY_BEGIN\n"
            "STATUS: blocked\n"
            "ROLE: reviewer\n"
            "PRIMARY_ARTIFACT: reviewer_report\n"
            "BLOCKING: yes\n"
            "RISK: HIGH\n"
            "ACTION: BLOCKER\n"
            "SUMMARY: Review found blockers.\n"
            "BLOCKERS:\n"
            "- Blocker 1\n"
            "- Blocker 2\n"
            "ROLE_SUMMARY_END"
        )
        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=text,
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["action"], "BLOCKER")
        self.assertTrue(result["blocking"])
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["blocking_summary"], ["Blocker 1", "Blocker 2"])

    def test_risk_none_maps_to_none(self):
        """RISK: NONE maps to risk_level is None."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured({"RISK": "NONE"}),
        )
        self.assertTrue(result.get("valid"))
        self.assertIsNone(result["risk_level"])

    def test_blocking_yes_no_maps_to_bool(self):
        """BLOCKING: yes/no maps to bool."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured({"BLOCKING": "yes"}),
        )
        self.assertTrue(result.get("valid"))
        self.assertTrue(result["blocking"])

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured({"BLOCKING": "no"}),
        )
        self.assertTrue(result.get("valid"))
        self.assertFalse(result["blocking"])

    def test_none_blockers_maps_to_empty_list(self):
        """- none maps to empty blocker list."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured(),
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["blocking_summary"], [])

    def test_wrong_role_returns_role_mismatch(self):
        """Wrong role returns SummaryRoleMismatch."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured({"ROLE": "architect"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryRoleMismatch")

    def test_wrong_primary_artifact_returns_artifact_mismatch(self):
        """Wrong primary artifact returns SummaryArtifactMismatch."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="wrong_artifact",
            json_str=self._make_structured(),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryArtifactMismatch")

    def test_forbidden_next_role_returns_forbidden_fields(self):
        """Forbidden next_role returns SummaryForbiddenFields."""
        from mcp_agent.summary_validator import validate_summary

        text = (
            "ROLE_SUMMARY_BEGIN\n"
            "STATUS: completed\n"
            "ROLE: scout\n"
            "PRIMARY_ARTIFACT: scout_report\n"
            "BLOCKING: no\n"
            "RISK: LOW\n"
            "ACTION: NONE\n"
            "SUMMARY: Test.\n"
            "BLOCKERS:\n"
            "- none\n"
            "next_role: architect\n"
            "ROLE_SUMMARY_END"
        )
        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=text,
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryForbiddenFields")

    def test_forbidden_ready_for_next_role_returns_forbidden_fields(self):
        """Forbidden ready_for_next_role returns SummaryForbiddenFields."""
        from mcp_agent.summary_validator import validate_summary

        text = (
            "ROLE_SUMMARY_BEGIN\n"
            "STATUS: completed\n"
            "ROLE: scout\n"
            "PRIMARY_ARTIFACT: scout_report\n"
            "BLOCKING: no\n"
            "RISK: LOW\n"
            "ACTION: NONE\n"
            "SUMMARY: Test.\n"
            "BLOCKERS:\n"
            "- none\n"
            "ready_for_next_role: true\n"
            "ROLE_SUMMARY_END"
        )
        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=text,
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryForbiddenFields")

    def test_reviewer_action_none_returns_invalid(self):
        """Reviewer with ACTION: NONE returns invalid."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=self._make_structured({
                "ROLE": "reviewer",
                "PRIMARY_ARTIFACT": "reviewer_report",
                "ACTION": "NONE",
            }),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryInvalidField")

    def test_non_reviewer_action_pass_returns_invalid(self):
        """Non-reviewer with ACTION: PASS returns invalid."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=self._make_structured({"ACTION": "PASS"}),
        )
        self.assertFalse(result.get("valid"))
        self.assertEqual(result["error"]["type"], "SummaryInvalidField")

    def test_malformed_missing_fields_returns_invalid(self):
        """Malformed/missing fields returns invalid, not an exception."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str="garbage text",
        )
        self.assertFalse(result.get("valid"))

    def test_missing_markers_partial_fields_returns_invalid(self):
        """Missing markers with insufficient fields returns invalid."""
        from mcp_agent.summary_validator import validate_summary

        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str="STATUS: completed\nROLE: scout",
        )
        self.assertFalse(result.get("valid"))


class TestRepairSummary(unittest.TestCase):
    """Test repair_summary function."""

    def test_repair_prompt_uses_structured_text(self):
        """Repair prompt uses structured text format, not JSON."""
        from mcp_agent.summary_validator import repair_summary

        prompt = repair_summary(
            role="scout",
            summary_artifact_name="scout_report",
        )
        self.assertIn("ROLE_SUMMARY_BEGIN", prompt)
        self.assertIn("ROLE_SUMMARY_END", prompt)
        self.assertIn("STATUS:", prompt)
        self.assertIn("ROLE:", prompt)
        self.assertIn("SUMMARY:", prompt)
        self.assertIn("PRIMARY_ARTIFACT:", prompt)
        self.assertIn("BLOCKING:", prompt)
        self.assertIn("RISK:", prompt)
        self.assertIn("ACTION:", prompt)
        self.assertIn("BLOCKERS:", prompt)

    def test_repair_prompt_includes_role(self):
        """Repair prompt includes role-specific info."""
        from mcp_agent.summary_validator import repair_summary

        prompt = repair_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
        )
        self.assertIn("ROLE_SUMMARY_BEGIN", prompt)


class TestSafeFallbackSummary(unittest.TestCase):
    """Test safe_fallback_summary function."""

    def test_safe_fallback_for_non_reviewer(self):
        """Safe fallback for non-reviewer role."""
        from mcp_agent.summary_validator import safe_fallback_summary

        result = safe_fallback_summary(
            role="scout",
            primary_artifact_name="scout_summary",
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
            primary_artifact_name="reviewer_summary",
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
            primary_artifact_name="reviewer_summary",
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
            primary_artifact_name="reviewer_summary",
            is_reviewer=True,
            main_artifact_content="ACTION: PASS\nSome text",
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action"], "PASS")


class TestValidStructuredSummary(unittest.TestCase):
    """Test parsing a valid multi-line structured text summary."""

    def test_valid_multi_line_block(self):
        """A valid multi-line structured text summary parses correctly."""
        from mcp_agent.summary_validator import validate_summary

        text = (
            "ROLE_SUMMARY_BEGIN\n"
            "STATUS: completed\n"
            "ROLE: scout\n"
            "PRIMARY_ARTIFACT: scout_report\n"
            "BLOCKING: no\n"
            "RISK: LOW\n"
            "ACTION: NONE\n"
            "SUMMARY: Repository scan completed successfully.\n"
            "BLOCKERS:\n"
            "- none\n"
            "ROLE_SUMMARY_END"
        )
        result = validate_summary(
            role="scout",
            summary_artifact_name="scout_report",
            json_str=text,
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["role"], "scout")
        self.assertEqual(result["primary_artifact_name"], "scout_report")
        self.assertFalse(result["blocking"])
        self.assertEqual(result["risk_level"], "LOW")
        self.assertIsNone(result["action"])
        self.assertEqual(result["blocking_summary"], [])


class TestBlockersStructuredSummary(unittest.TestCase):
    """Test parsing a structured text summary with blockers."""

    def test_blocked_with_multiple_blockers(self):
        """A blocked summary with multiple blockers parses correctly."""
        from mcp_agent.summary_validator import validate_summary

        text = (
            "ROLE_SUMMARY_BEGIN\n"
            "STATUS: blocked\n"
            "ROLE: reviewer\n"
            "PRIMARY_ARTIFACT: reviewer_report\n"
            "BLOCKING: yes\n"
            "RISK: HIGH\n"
            "ACTION: BLOCKER\n"
            "SUMMARY: Review found a blocking issue.\n"
            "BLOCKERS:\n"
            "- Generated client does not cover all RPCs.\n"
            "- Tests do not exercise the example client.\n"
            "ROLE_SUMMARY_END"
        )
        result = validate_summary(
            role="reviewer",
            summary_artifact_name="reviewer_report",
            json_str=text,
        )
        self.assertTrue(result.get("valid"))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["blocking"])
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["action"], "BLOCKER")
        self.assertEqual(len(result["blocking_summary"]), 2)
        self.assertIn("Generated client does not cover all RPCs.", result["blocking_summary"])
        self.assertIn("Tests do not exercise the example client.", result["blocking_summary"])


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
