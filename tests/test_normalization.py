#!/usr/bin/env python3
"""Tests for LLM-friendly normalization helpers (mcp_agent.server)."""

import os
import shutil
import sys
import tempfile
import unittest

# Ensure the project root is on sys.path so imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestUnwrapText(unittest.TestCase):
    """Test unwrap_text helper."""

    def test_plain_string(self):
        self.assertEqual(unwrap_text("abc"), "abc")

    def test_plain_int(self):
        self.assertEqual(unwrap_text(123), 123)

    def test_plain_bool(self):
        self.assertTrue(unwrap_text(True))
        self.assertFalse(unwrap_text(False))

    def test_text_wrapper(self):
        self.assertEqual(unwrap_text({"text": "abc"}), "abc")

    def test_value_wrapper_unchanged(self):
        """unwrap_text only handles {\"text\": ...}, not {\"value\": ...}."""
        result = unwrap_text({"value": "abc"})
        self.assertEqual(result, {"value": "abc"})

    def test_default_wrapper_unchanged(self):
        """unwrap_text only handles {\"text\": ...}, not {\"default\": ...}."""
        result = unwrap_text({"default": "abc"})
        self.assertEqual(result, {"default": "abc"})

    def test_multi_key_dict_unchanged(self):
        """Dict with multiple keys is returned as-is."""
        result = unwrap_text({"text": "abc", "extra": "def"})
        self.assertEqual(result, {"text": "abc", "extra": "def"})

    def test_none(self):
        self.assertIsNone(unwrap_text(None))


class TestNormalizeBool(unittest.TestCase):
    """Test normalize_bool helper."""

    def test_true(self):
        self.assertTrue(normalize_bool(True))

    def test_false(self):
        self.assertFalse(normalize_bool(False))

    def test_text_wrapper_true(self):
        self.assertTrue(normalize_bool({"text": True}))

    def test_text_wrapper_string_true(self):
        self.assertTrue(normalize_bool({"text": "true"}))

    def test_text_wrapper_string_false(self):
        self.assertFalse(normalize_bool({"text": "false"}))

    def test_default_wrapper(self):
        self.assertTrue(normalize_bool({"default": True}))
        self.assertFalse(normalize_bool({"default": False}))

    def test_value_wrapper(self):
        self.assertTrue(normalize_bool({"value": True}))

    def test_string_true_variants(self):
        for val in ("true", "1", "yes", "y", "on"):
            self.assertTrue(normalize_bool(val), f"Failed for '{val}'")

    def test_string_false_variants(self):
        for val in ("false", "0", "no", "n", "off"):
            self.assertFalse(normalize_bool(val), f"Failed for '{val}'")

    def test_none_returns_default(self):
        self.assertTrue(normalize_bool(None, default=True))
        self.assertFalse(normalize_bool(None, default=False))

    def test_int_0_is_false(self):
        self.assertFalse(normalize_bool(0))

    def test_int_1_is_true(self):
        self.assertTrue(normalize_bool(1))


class TestNormalizeInt(unittest.TestCase):
    """Test normalize_int helper."""

    def test_plain_int(self):
        self.assertEqual(normalize_int(600), 600)

    def test_string_int(self):
        self.assertEqual(normalize_int("600"), 600)

    def test_text_wrapper(self):
        self.assertEqual(normalize_int({"text": "600"}), 600)

    def test_default_wrapper(self):
        self.assertEqual(normalize_int({"default": 600}), 600)

    def test_value_wrapper(self):
        self.assertEqual(normalize_int({"value": 600}), 600)

    def test_none_returns_default(self):
        self.assertEqual(normalize_int(None, default=1800), 1800)

    def test_float(self):
        self.assertEqual(normalize_int(600.5), 600)

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            normalize_int([1, 2])


class TestNormalizeString(unittest.TestCase):
    """Test normalize_string helper."""

    def test_plain_string(self):
        self.assertEqual(normalize_string("abc", "field"), "abc")

    def test_text_wrapper(self):
        self.assertEqual(normalize_string({"text": "abc"}, "field"), "abc")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_string(123, "field_name")
        self.assertIn("field_name", str(ctx.exception))


class TestNormalizeRoleRunId(unittest.TestCase):
    """Test normalize_role_run_id helper."""

    def test_plain_string(self):
        self.assertEqual(normalize_role_run_id("abc-role-1"), "abc-role-1")

    def test_text_wrapper(self):
        self.assertEqual(
            normalize_role_run_id({"text": "abc-role-1"}), "abc-role-1"
        )

    def test_role_run_id_key(self):
        self.assertEqual(
            normalize_role_run_id({
                "role_run_id": "abc-role-1",
                "status": "running",
            }),
            "abc-role-1",
        )

    def test_nested_text(self):
        self.assertEqual(
            normalize_role_run_id({
                "role_run_id": {"text": "abc-role-1"},
            }),
            "abc-role-1",
        )

    def test_double_nested(self):
        self.assertEqual(
            normalize_role_run_id({
                "role_run_id": {
                    "role_run_id": "abc-role-1",
                    "timeout_seconds": 600,
                },
            }),
            "abc-role-1",
        )

    def test_id_key(self):
        self.assertEqual(
            normalize_role_run_id({"id": "abc-role-1"}), "abc-role-1"
        )

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            normalize_role_run_id([1, 2])


class TestNormalizeArtifactName(unittest.TestCase):
    """Test normalize_artifact_name helper."""

    def test_plain_string(self):
        self.assertEqual(normalize_artifact_name("scout_report"), "scout_report")

    def test_text_wrapper(self):
        self.assertEqual(
            normalize_artifact_name({"text": "scout_report"}), "scout_report"
        )

    def test_name_key(self):
        self.assertEqual(
            normalize_artifact_name({"name": "scout_report"}), "scout_report"
        )

    def test_artifact_name_key(self):
        self.assertEqual(
            normalize_artifact_name({"artifact_name": "scout_report"}),
            "scout_report",
        )

    def test_none(self):
        self.assertIsNone(normalize_artifact_name(None))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            normalize_artifact_name([1, 2])


class TestUnwrapScalar(unittest.TestCase):
    """Tests for unwrap_scalar helper."""

    def test_plain_string(self):
        self.assertEqual(unwrap_scalar("abc"), "abc")

    def test_plain_int(self):
        self.assertEqual(unwrap_scalar(123), 123)

    def test_plain_none(self):
        self.assertIsNone(unwrap_scalar(None))

    def test_text_wrapper(self):
        self.assertEqual(unwrap_scalar({"text": "abc"}), "abc")

    def test_value_wrapper(self):
        self.assertEqual(unwrap_scalar({"value": "abc"}), "abc")

    def test_default_wrapper(self):
        self.assertEqual(unwrap_scalar({"default": "abc"}), "abc")

    def test_id_wrapper(self):
        self.assertEqual(unwrap_scalar({"id": "abc"}), "abc")

    def test_artifact_id_wrapper(self):
        self.assertEqual(unwrap_scalar({"artifact_id": "art_xxx"}), "art_xxx")

    def test_role_wrapper_with_extra_keys(self):
        self.assertEqual(
            unwrap_scalar({"role": "architect"}, extra_keys=["role"]), "architect"
        )

    def test_idempotency_key_wrapper_with_extra_keys(self):
        self.assertEqual(
            unwrap_scalar({"idempotency_key": "key123"}, extra_keys=["idempotency_key"]),
            "key123",
        )

    def test_nested_wrappers(self):
        self.assertEqual(unwrap_scalar({"text": {"value": "abc"}}), "abc")

    def test_multi_key_unknown(self):
        """Multi-key dict with unknown keys returns as-is."""
        result = unwrap_scalar({"foo": "bar", "baz": "qux"})
        self.assertEqual(result, {"foo": "bar", "baz": "qux"})

    def test_multi_key_with_text(self):
        """Multi-key dict with text key unwraps text."""
        self.assertEqual(unwrap_scalar({"text": "abc", "extra": "def"}), "abc")

    def test_multi_key_with_value(self):
        """Multi-key dict with value key unwraps value."""
        self.assertEqual(unwrap_scalar({"value": "abc", "extra": "def"}), "abc")

    def test_multi_key_with_artifact_id(self):
        """Multi-key dict with artifact_id key unwraps artifact_id."""
        self.assertEqual(
            unwrap_scalar({"artifact_id": "art_xxx", "extra": "def"}), "art_xxx"
        )


class TestNormalizeRole(unittest.TestCase):
    """Tests 2-5, 7: normalize_role helper."""

    def test_plain_string(self):
        """Test 2: normalize_role('scout') == 'scout'"""
        self.assertEqual(normalize_role("scout"), "scout")

    def test_text_wrapper(self):
        """Test 3: normalize_role({'text': 'scout'}) == 'scout'"""
        self.assertEqual(normalize_role({"text": "scout"}), "scout")

    def test_name_wrapper(self):
        """Test 4: normalize_role({'name': 'scout'}) == 'scout'"""
        self.assertEqual(normalize_role({"name": "scout"}), "scout")

    def test_role_wrapper(self):
        """normalize_role({'role': 'scout'}) == 'scout'"""
        self.assertEqual(normalize_role({"role": "scout"}), "scout")

    def test_nested_name_text(self):
        """Test 5: normalize_role({'name': {'text': 'scout'}}) == 'scout'"""
        self.assertEqual(
            normalize_role({"name": {"text": "scout"}}), "scout"
        )

    def test_nested_role_text(self):
        """normalize_role({'role': {'text': 'scout'}}) == 'scout'"""
        self.assertEqual(
            normalize_role({"role": {"text": "scout"}}), "scout"
        )

    def test_value_wrapper(self):
        """normalize_role({'value': 'scout'}) == 'scout'"""
        self.assertEqual(normalize_role({"value": "scout"}), "scout")

    def test_id_wrapper(self):
        """normalize_role({'id': 'scout'}) == 'scout'"""
        self.assertEqual(normalize_role({"id": "scout"}), "scout")

    def test_deeply_nested(self):
        """normalize_role({'name': {'role': {'text': 'scout'}}}) == 'scout'"""
        self.assertEqual(
            normalize_role({"name": {"role": {"text": "scout"}}}), "scout"
        )

    def test_whitespace_stripped(self):
        """normalize_role('  scout  ') == 'scout'"""
        self.assertEqual(normalize_role("  scout  "), "scout")


if __name__ == "__main__":
    unittest.main()
