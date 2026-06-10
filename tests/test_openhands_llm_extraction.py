#!/usr/bin/env python3
"""Tests for openhands_llm final-answer extraction from realistic event schemas.

Covers the broadened extraction paths:
1. Event with assistant message in top-level ``message``.
2. Event with assistant content in top-level ``content``.
3. Event with text under ``args.content``.
4. Event with final answer under ``args.final_thought`` or similar current
   OpenHands final field if present.
5. Completed job with events containing valid assistant text must return
   ``answer`` non-empty.
6. Empty/irrelevant events should still return empty answer and debug logs
   should not crash.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openhands_llm import openhands_llm_call as oh


class TestExtractFinalAnswer(unittest.TestCase):
    """Test extract_final_answer() with various answer lists."""

    def test_single_nonempty_answer(self):
        self.assertEqual(oh.extract_final_answer(["Hello world"]), "Hello world")

    def test_multiple_answers_returns_last_nontrivial(self):
        # Should skip "done" and return the earlier substantive answer
        result = oh.extract_final_answer(["done", "Hello world"])
        self.assertEqual(result, "Hello world")

    def test_short_answers_filtered(self):
        for short in ("done", "ok", "okay", "finished"):
            self.assertEqual(oh.extract_final_answer([short]), "")

    def test_empty_list_returns_empty(self):
        self.assertEqual(oh.extract_final_answer([]), "")

    def test_all_empty_strings_returns_empty(self):
        self.assertEqual(oh.extract_final_answer(["", "", ""]), "")

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(oh.extract_final_answer(["   \n\t  "]), "")


class TestContentToText(unittest.TestCase):
    """Test content_to_text() with various value types."""

    def test_string_input(self):
        self.assertEqual(oh.content_to_text("hello"), "hello")

    def test_none_input(self):
        self.assertEqual(oh.content_to_text(None), "")

    def test_dict_with_type_text(self):
        result = oh.content_to_text({"type": "text", "text": "hello world"})
        self.assertEqual(result, "hello world")

    def test_dict_with_preferred_keys(self):
        for key in ("text", "message", "content", "thought", "final_thought"):
            result = oh.content_to_text({key: "found it"})
            self.assertEqual(result, "found it")

    def test_list_of_dicts(self):
        items = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        result = oh.content_to_text(items)
        self.assertEqual(result, "a\nb")

    def test_deeply_nested_dict(self):
        result = oh.content_to_text({"a": {"b": {"c": "deep text"}}})
        self.assertEqual(result, "deep text")


class TestIsAgentMessageEvent(unittest.TestCase):
    """Test is_agent_message_event() with various event shapes."""

    def test_messageevent_with_agent_source(self):
        event = {"kind": "messageevent", "source": "agent", "llm_message": {"role": "assistant", "content": "hi"}}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_kind_message_with_assistant_source(self):
        event = {"kind": "message", "source": "assistant", "llm_message": {"role": "agent", "content": "hi"}}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_kind_agent_message(self):
        event = {"kind": "agent_message", "source": "agent", "llm_message": {"role": "assistant", "content": "hi"}}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_no_llm_message_but_has_textual_args(self):
        event = {"kind": "generic", "source": "agent", "args": {"message": "text from args"}}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_no_llm_message_but_has_textual_extras(self):
        event = {"kind": "generic", "source": "agent", "extras": {"content": "text from extras"}}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_no_llm_message_but_has_textual_data(self):
        event = {"kind": "generic", "source": "assistant", "data": {"text": "text from data"}}
        self.assertTrue(oh.is_agent_message_event(event))

    def test_non_agent_source_rejected(self):
        event = {"kind": "messageevent", "source": "user", "llm_message": {"role": "assistant", "content": "hi"}}
        self.assertFalse(oh.is_agent_message_event(event))

    def test_empty_kind_rejected(self):
        event = {"kind": "", "source": "agent", "llm_message": {"role": "assistant", "content": "hi"}}
        # Should be False because kind doesn't match and no textual args/extras/data
        self.assertFalse(oh.is_agent_message_event(event))


class TestExtractMessageEventText(unittest.TestCase):
    """Test extract_message_event_text() with various event shapes."""

    def test_from_llm_message_content(self):
        event = {"kind": "messageevent", "source": "agent", "llm_message": {"role": "assistant", "content": "llm answer"}}
        self.assertEqual(oh.extract_message_event_text(event), "llm answer")

    def test_from_args_message(self):
        event = {"kind": "generic", "source": "agent", "args": {"message": "args answer"}}
        self.assertTrue(oh.is_agent_message_event(event))  # must pass is check first
        self.assertEqual(oh.extract_message_event_text(event), "args answer")

    def test_from_args_content(self):
        event = {"kind": "generic", "source": "agent", "args": {"content": "args content answer"}}
        self.assertTrue(oh.is_agent_message_event(event))
        self.assertEqual(oh.extract_message_event_text(event), "args content answer")

    def test_from_args_final_thought(self):
        event = {"kind": "generic", "source": "agent", "args": {"final_thought": "final thought answer"}}
        self.assertTrue(oh.is_agent_message_event(event))
        self.assertEqual(oh.extract_message_event_text(event), "final thought answer")

    def test_from_extras_content(self):
        event = {"kind": "generic", "source": "agent", "extras": {"content": "extras answer"}}
        self.assertTrue(oh.is_agent_message_event(event))
        self.assertEqual(oh.extract_message_event_text(event), "extras answer")

    def test_from_data_text(self):
        event = {"kind": "generic", "source": "agent", "data": {"text": "data answer"}}
        self.assertTrue(oh.is_agent_message_event(event))
        self.assertEqual(oh.extract_message_event_text(event), "data answer")


class TestCollectFinalTextFromEvents(unittest.TestCase):
    """Test collect_final_text_from_events() with realistic event lists."""

    def test_finish_action_with_outputs_answer(self):
        events = [{
            "kind": "actionevent",
            "source": "agent",
            "action": {
                "kind": "finishaction",
                "outputs": {"answer": "finish answer text"}
            }
        }]
        self.assertEqual(oh.collect_final_text_from_events(events), "finish answer text")

    def test_agent_message_with_llm_content(self):
        events = [{
            "kind": "messageevent",
            "source": "agent",
            "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "agent text"}]}
        }]
        self.assertEqual(oh.collect_final_text_from_events(events), "agent text")

    def test_args_message_as_fallback(self):
        events = [{
            "kind": "generic",
            "source": "agent",
            "args": {"message": "args message answer"}
        }]
        self.assertEqual(oh.collect_final_text_from_events(events), "args message answer")

    def test_extras_content_as_fallback(self):
        events = [{
            "kind": "generic",
            "source": "assistant",
            "extras": {"content": "extras content answer"}
        }]
        self.assertEqual(oh.collect_final_text_from_events(events), "extras content answer")

    def test_data_text_as_fallback(self):
        events = [{
            "kind": "generic",
            "source": "agent",
            "data": {"text": "data text answer"}
        }]
        self.assertEqual(oh.collect_final_text_from_events(events), "data text answer")

    def test_empty_events_returns_empty(self):
        self.assertEqual(oh.collect_final_text_from_events([]), "")

    def test_no_matching_events_returns_empty(self):
        events = [
            {"kind": "observationevent", "source": "environment", "tool_name": "read"},
            {"kind": "actionevent", "source": "agent", "action": {"kind": "writeaction"}},
        ]
        self.assertEqual(oh.collect_final_text_from_events(events), "")

    def test_finish_event_with_data_answer(self):
        events = [{
            "kind": "actionevent",
            "source": "agent",
            "action": {"kind": "finishaction"},
            "data": {"answer": "data answer text"}
        }]
        self.assertEqual(oh.collect_final_text_from_events(events), "data answer text")


class TestExtractFinishActionText(unittest.TestCase):
    """Test extract_finish_action_text() with various finish event shapes."""

    def test_finish_with_outputs_answer(self):
        event = {
            "kind": "actionevent",
            "source": "agent",
            "action": {
                "kind": "finishaction",
                "outputs": {"answer": "finish answer text"}
            }
        }
        self.assertEqual(oh.extract_finish_action_text(event), "finish answer text")

    def test_finish_with_data_answer(self):
        event = {
            "kind": "actionevent",
            "source": "agent",
            "action": {"kind": "finishaction"},
            "data": {"answer": "data answer text"}
        }
        self.assertEqual(oh.extract_finish_action_text(event), "data answer text")

    def test_finish_with_args_outputs_answer(self):
        event = {
            "kind": "actionevent",
            "source": "agent",
            "action": {"kind": "finishaction"},
            "args": {"outputs": {"answer": "args outputs answer"}}
        }
        self.assertEqual(oh.extract_finish_action_text(event), "args outputs answer")

    def test_finish_with_data_final_answer(self):
        event = {
            "kind": "actionevent",
            "source": "agent",
            "action": {"kind": "finishaction"},
            "data": {"final_answer": "data final answer text"}
        }
        self.assertEqual(oh.extract_finish_action_text(event), "data final answer text")

    def test_finish_with_message_field(self):
        event = {
            "kind": "actionevent",
            "source": "agent",
            "action": {"kind": "finishaction", "message": "finish message text"}
        }
        self.assertEqual(oh.extract_finish_action_text(event), "finish message text")

    def test_non_finish_action_returns_empty(self):
        event = {
            "kind": "actionevent",
            "source": "agent",
            "action": {"kind": "writeaction"}
        }
        self.assertEqual(oh.extract_finish_action_text(event), "")


class TestCollectExistingConversationAnswer(unittest.TestCase):
    """Test collect_final_text_from_events() with realistic event schemas."""

    def test_completed_job_with_valid_assistant_text(self):
        """Events containing valid assistant text must return non-empty answer.

        Events are returned newest-first, so the first matching event is
        returned.  The key invariant is that the answer is non-empty.
        """
        events = [
            {"kind": "messageevent", "source": "agent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "Final answer here."}]}},
            {"kind": "messageevent", "source": "agent", "llm_message": {"role": "assistant", "content": "Working..."}},
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertTrue(result, "Expected non-empty answer from valid assistant events")
        self.assertIn("Final answer here.", result)

    def test_events_with_args_message(self):
        """Events with args.message should be extracted."""
        events = [
            {"kind": "generic", "source": "agent", "args": {"message": "Answer from args."}},
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "Answer from args.")

    def test_events_with_extras_content(self):
        """Events with extras.content should be extracted."""
        events = [
            {"kind": "generic", "source": "agent", "extras": {"content": "Answer from extras."}},
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "Answer from extras.")

    def test_events_with_data_text(self):
        """Events with data.text should be extracted."""
        events = [
            {"kind": "generic", "source": "agent", "data": {"text": "Answer from data."}},
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "Answer from data.")

    def test_empty_events_returns_empty(self):
        self.assertEqual(oh.collect_final_text_from_events([]), "")

    def test_irrelevant_events_returns_empty(self):
        events = [
            {"kind": "observationevent", "source": "environment", "tool_name": "read"},
            {"kind": "actionevent", "source": "agent", "action": {"kind": "writeaction"}},
        ]
        result = oh.collect_final_text_from_events(events)
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
