#!/usr/bin/env python3
"""Tests for final-answer extraction from OpenHands events.

Covers realistic event shapes that the real OpenHands V1 API may return,
ensuring that `collect_final_text_from_events` and `extract_final_answer`
produce non-empty answers when valid assistant text is present.
"""

import json
import os
import sys
import unittest

# Ensure openhands_llm package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openhands_llm import openhands_llm_call as oh


class TestContentToText(unittest.TestCase):
    """Test content_to_text handles various event shapes."""

    def test_dict_with_type_text(self):
        """Dict with type='text' and text key returns text value."""
        result = oh.content_to_text({"type": "text", "text": "hello world"})
        self.assertEqual(result, "hello world")

    def test_dict_preferred_key_message(self):
        """Dict with 'message' key returns its value."""
        result = oh.content_to_text({"message": "hello world"})
        self.assertEqual(result, "hello world")

    def test_dict_preferred_key_content(self):
        """Dict with 'content' key returns its value."""
        result = oh.content_to_text({"content": "hello world"})
        self.assertEqual(result, "hello world")

    def test_dict_preferred_key_final_thought(self):
        """Dict with 'final_thought' key returns its value."""
        result = oh.content_to_text({"final_thought": "hello world"})
        self.assertEqual(result, "hello world")

    def test_dict_args_final_thought(self):
        """Dict with args.final_thought returns its value."""
        result = oh.content_to_text({"args": {"final_thought": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_args_message(self):
        """Dict with args.message returns its value."""
        result = oh.content_to_text({"args": {"message": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_args_content(self):
        """Dict with args.content returns its value."""
        result = oh.content_to_text({"args": {"content": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_args_text(self):
        """Dict with args.text returns its value."""
        result = oh.content_to_text({"args": {"text": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_extras_message(self):
        """Dict with extras.message returns its value."""
        result = oh.content_to_text({"extras": {"message": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_extras_content(self):
        """Dict with extras.content returns its value."""
        result = oh.content_to_text({"extras": {"content": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_extras_text(self):
        """Dict with extras.text returns its value."""
        result = oh.content_to_text({"extras": {"text": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_dict_extras_thought(self):
        """Dict with extras.thought returns its value."""
        result = oh.content_to_text({"extras": {"thought": "hello world"}})
        self.assertEqual(result, "hello world")

    def test_list_of_dicts(self):
        """List of dicts with type='text' blocks is joined."""
        result = oh.content_to_text([
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ])
        self.assertEqual(result, "hello\nworld")

    def test_none_returns_empty(self):
        """None input returns empty string."""
        result = oh.content_to_text(None)
        self.assertEqual(result, "")

    def test_string_stripped(self):
        """String input is stripped."""
        result = oh.content_to_text("  hello world  ")
        self.assertEqual(result, "hello world")

    def test_empty_dict_returns_empty(self):
        """Empty dict returns empty string."""
        result = oh.content_to_text({})
        self.assertEqual(result, "")


class TestExtractMessageEventText(unittest.TestCase):
    """Test extract_message_event_text handles various event shapes."""

    def test_llm_message_content(self):
        """Event with llm_message.content returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello world"}],
            },
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_top_level_message(self):
        """Event with top-level 'message' field returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "message": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_top_level_text(self):
        """Event with top-level 'text' field returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "text": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_top_level_final_thought(self):
        """Event with top-level 'final_thought' field returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "final_thought": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_top_level_thought(self):
        """Event with top-level 'thought' field returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "thought": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_top_level_content(self):
        """Event with top-level 'content' field returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "content": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_top_level_observation(self):
        """Event with top-level 'observation' field returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "observation": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_args_final_thought(self):
        """Event with args.final_thought returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "args": {"final_thought": "hello world"},
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_args_message(self):
        """Event with args.message returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "args": {"message": "hello world"},
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_args_content(self):
        """Event with args.content returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "args": {"content": "hello world"},
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_extras_message(self):
        """Event with extras.message returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "extras": {"message": "hello world"},
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_extras_content(self):
        """Event with extras.content returns text."""
        event = {
            "kind": "MessageEvent",
            "source": "agent",
            "extras": {"content": "hello world"},
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "hello world")

    def test_non_agent_event_returns_empty(self):
        """Non-agent event returns empty string."""
        event = {
            "kind": "MessageEvent",
            "source": "user",
            "message": "hello world",
        }
        result = oh.extract_message_event_text(event)
        self.assertEqual(result, "")


class TestCollectFinalTextFromEvents(unittest.TestCase):
    """Test collect_final_text_from_events returns non-empty answer."""

    def test_finish_action_priority(self):
        """Finish action events are checked before message events."""
        events = [
            {
                "kind": "MessageEvent",
                "source": "agent",
                "message": "message text",
            },
            {
                "kind": "ActionEvent",
                "source": "agent",
                "action": {
                    "kind": "FinishAction",
                    "outputs": {"content": "finish text"},
                },
            },
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "finish text")

    def test_message_event_returns_text(self):
        """Message events return text when no finish action."""
        events = [
            {
                "kind": "MessageEvent",
                "source": "agent",
                "message": "hello world",
            },
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "hello world")

    def test_empty_events_returns_empty(self):
        """Empty event list returns empty string."""
        result = oh.collect_final_text_from_events([])
        self.assertEqual(result, "")

    def test_no_agent_events_returns_empty(self):
        """No agent events returns empty string."""
        events = [
            {
                "kind": "MessageEvent",
                "source": "user",
                "message": "user message",
            },
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "")


class TestExtractFinalAnswer(unittest.TestCase):
    """Test extract_final_answer selects the right answer."""

    def test_returns_last_non_empty_non_short(self):
        """Returns last non-empty, non-short answer (reversed order)."""
        answers = ["done", "ok", "hello world"]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "hello world")

    def test_rejects_done(self):
        """Short answer 'done' is rejected."""
        answers = ["done"]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "")

    def test_rejects_ok(self):
        """Short answer 'ok' is rejected."""
        answers = ["ok"]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "")

    def test_rejects_empty(self):
        """Empty string is rejected."""
        answers = [""]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "")

    def test_rejects_whitespace_only(self):
        """Whitespace-only string is rejected."""
        answers = ["   \n\t  "]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "")

    def test_returns_meaningful_text(self):
        """Meaningful text is returned."""
        answers = ["This is a valid final answer from the agent."]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "This is a valid final answer from the agent.")

    def test_reversed_order_picks_last(self):
        """Reversed order means the last non-empty answer wins."""
        answers = ["first", "second", "third"]
        result = oh.extract_final_answer(answers)
        self.assertEqual(result, "third")


class TestIsAgentMessageEvent(unittest.TestCase):
    """Test is_agent_message_event accepts various kind/source combinations."""

    def test_kind_messageevent_source_agent(self):
        event = {"kind": "MessageEvent", "source": "agent"}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_kind_message_source_assistant(self):
        event = {"kind": "message", "source": "assistant"}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_kind_ends_with_message(self):
        event = {"kind": "some.message", "source": "agent"}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_source_openhands(self):
        event = {"kind": "MessageEvent", "source": "openhands"}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_source_llm(self):
        event = {"kind": "MessageEvent", "source": "llm"}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_source_user_rejected(self):
        event = {"kind": "MessageEvent", "source": "user"}
        self.assertFalse(oh.is_agent_message_event(event))


if __name__ == "__main__":
    unittest.main()
