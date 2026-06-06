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


# Import the functions under test
from mcp_agent.server import (
    normalize_artifact_name,
    normalize_bool,
    normalize_int,
    normalize_role_run_id,
    normalize_string,
    unwrap_text,
)


if __name__ == "__main__":
    unittest.main()
