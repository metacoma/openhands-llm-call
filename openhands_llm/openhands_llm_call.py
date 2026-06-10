#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


START_TASK_READY_STATES = {"READY", "COMPLETED", "SUCCESS"}
START_TASK_ERROR_STATES = {"ERROR", "FAILED"}

TERMINAL_EXECUTION_STATES = {
    "finished",
    "completed",
    "success",
    "failed",
    "error",
    "stuck",
    "waiting_for_confirmation",
}


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    parts: list[str] = []

    if prompt:
        parts.append(prompt)

    if prompt_file:
        parts.append(Path(prompt_file).read_text(encoding="utf-8"))

    if not parts and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            parts.append(text)

    if not parts:
        fail("Prompt is required: use --prompt, --prompt-file, positional prompt, or pipe stdin")

    return "\n\n".join(parts).strip()


def request_json(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
    allow_errors: bool = False,
) -> Any:
    response = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        params=params,
        timeout=timeout,
    )

    content_type = response.headers.get("content-type", "")

    if "text/html" in content_type:
        if allow_errors:
            return {
                "__ok": False,
                "__status_code": response.status_code,
                "__error": "HTML instead of JSON",
                "__body": response.text[:500],
            }

        fail(
            f"{method} {url} returned HTML instead of JSON.\n"
            "Запрос попал во frontend fallback, а не в API."
        )

    if response.status_code >= 400:
        if allow_errors:
            return {
                "__ok": False,
                "__status_code": response.status_code,
                "__error": response.text,
            }

        fail(f"{method} {url} failed: HTTP {response.status_code}\n{response.text}")

    try:
        return response.json()
    except Exception:
        if allow_errors:
            return {
                "__ok": False,
                "__status_code": response.status_code,
                "__error": "Response is not JSON",
                "__body": response.text[:500],
            }

        fail(f"{method} {url} did not return JSON:\n{response.text}")


def bearer_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def start_v1_app_conversation(
    base_url: str,
    api_key: str,
    repo: str | None,
    branch: str | None,
    prompt: str,
    llm_model: str | None,
    agent_type: str,
) -> dict[str, Any]:
    """Start a new OpenHands V1 conversation.

    Parameters
    ----------
    repo :
        Deprecated.  Kept for backward compatibility but always
        passed as ``None`` so OpenHands creates an empty/default
        sandbox with no selected repository metadata.
    branch :
        Deprecated.  Kept for backward compatibility but always
        passed as ``None``.
    """
    url = f"{base_url.rstrip('/')}/api/v1/app-conversations"

    # NOTE: selected_repository, selected_branch, and git_provider are
    # always None so the sandbox starts empty.  Repository instructions
    # live in the prompt text.
    payload: dict[str, Any] = {
        "sandbox_id": None,
        "conversation_id": None,
        "initial_message": {
            "role": "user",
            "content": [
                {
                    "cache_prompt": False,
                    "type": "text",
                    "text": prompt,
                }
            ],
            "run": False,
        },
        "system_message_suffix": None,
        "processors": None,
        "llm_model": llm_model,
        "selected_repository": None,
        "selected_branch": None,
        "git_provider": None,
        "suggested_task": None,
        "title": None,
        "trigger": None,
        "pr_number": [],
        "parent_conversation_id": None,
        "agent_type": agent_type,
        "public": None,
        "plugins": None,
        "secrets": None,
    }

    return request_json(
        "POST",
        url,
        bearer_headers(api_key),
        json_body=payload,
    )


def send_message_to_existing_conversation(
    base_url: str,
    api_key: str,
    conversation_id: str,
    prompt: str,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    """Send a new user message into an existing OpenHands conversation.

    Returns the response JSON from the API. Does NOT read old answers.

    Parameters
    ----------
    base_url :
        OpenHands base URL (e.g., http://localhost:3000).
    api_key :
        Bearer API key.
    conversation_id :
        Existing conversation ID to continue.
    prompt :
        New user message text.
    timeout :
        Request timeout in seconds.

    Returns
    -------
    dict
        Response JSON (may contain task_id, conversation_id, status, etc.).

    Raises
    ------
    requests.HTTPError
        On HTTP error response.
    """
    url = f"{base_url.rstrip('/')}/api/v1/app-conversations/{conversation_id}/send-message"

    payload: dict[str, Any] = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": prompt,
            }
        ],
        "run": True,  # auto-start agent after message
    }

    response = request_json(
        "POST",
        url,
        bearer_headers(api_key),
        json_body=payload,
        timeout=timeout,
    )

    return response


def poll_v1_start_task(
    base_url: str,
    api_key: str,
    task_id: str,
    interval: int = 5,
    attempts: int = 60,
) -> str:
    url = f"{base_url.rstrip('/')}/api/v1/app-conversations/start-tasks"
    headers = bearer_headers(api_key)

    for attempt in range(1, attempts + 1):
        data = request_json(
            "GET",
            url,
            headers,
            params={"ids": task_id},
            timeout=30,
        )

        if isinstance(data, dict):
            items = data.get("items") or data.get("tasks") or [data]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        if not items:
            print(f"[{attempt}/{attempts}] Start task: no data")
            time.sleep(interval)
            continue

        item = items[0]
        status = item.get("status")
        conversation_id = (
            item.get("app_conversation_id")
            or item.get("conversation_id")
            or item.get("id")
        )

        print(f"[{attempt}/{attempts}] Start task status: {status}")

        if status in START_TASK_READY_STATES and conversation_id:
            return conversation_id

        if status in START_TASK_ERROR_STATES:
            fail(
                "OpenHands start task failed:\n"
                + json.dumps(item, indent=2, ensure_ascii=False)
            )

        time.sleep(interval)

    fail("Timed out waiting for V1 conversation start task")


def get_v1_conversation(
    base_url: str,
    api_key: str,
    conversation_id: str,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v1/app-conversations"

    data = request_json(
        "GET",
        url,
        bearer_headers(api_key),
        params={"ids": conversation_id},
        timeout=30,
        allow_errors=True,
    )

    if isinstance(data, dict) and data.get("__ok") is False:
        return {}

    if isinstance(data, list) and data:
        return data[0]

    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list) and items:
            return items[0]
        return data

    return {}


def response_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        items = data.get("items") or data.get("events") or data.get("results") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    return [item for item in items if isinstance(item, dict)]


def response_next_page_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None

    for key in ("next_page_id", "next_page", "nextPageId"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value

    return None


def event_id(event: dict[str, Any]) -> str:
    for key in ("id", "event_id", "uuid", "timestamp"):
        value = event.get(key)
        if value is not None:
            return str(value)

    return json.dumps(event, sort_keys=True, ensure_ascii=False)[:500]


def search_v1_events(
    base_url: str,
    api_key: str | None,
    conversation_id: str,
    limit: int = 100,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    max_pages = max(1, max_pages)

    url = f"{base_url.rstrip('/')}/api/v1/conversation/{conversation_id}/events/search"
    headers = bearer_headers(api_key or "")

    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page_id: str | None = None

    for _page_num in range(1, max_pages + 1):
        params: dict[str, Any] = {
            "limit": limit,
            "sort_order": "TIMESTAMP_DESC",
        }
        if page_id:
            params["page_id"] = page_id

        data = request_json(
            "GET",
            url,
            headers,
            params=params,
            timeout=30,
            allow_errors=True,
        )

        if isinstance(data, dict) and data.get("__ok") is False:
            warn(
                f"Cannot read events: HTTP={data.get('__status_code')} "
                f"error={data.get('__error')}"
            )
            break

        items = response_items(data)
        if not items:
            break

        for item in items:
            eid = event_id(item)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            events.append(item)

        page_id = response_next_page_id(data)
        if not page_id:
            break

    return events


def content_to_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = content_to_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return value["text"].strip()

        preferred_keys = (
            "text",
            "message",
            "content",
            "thought",
            "final_thought",
            "summary",
            "result",
            "output",
        )

        for key in preferred_keys:
            if key in value:
                text = content_to_text(value[key])
                if text:
                    return text

        parts: list[str] = []
        for nested_value in value.values():
            text = content_to_text(nested_value)
            if text:
                parts.append(text)

        return "\n".join(parts).strip()

    return ""


def event_kind(event: dict[str, Any]) -> str:
    return str(
        event.get("kind")
        or event.get("type")
        or event.get("event_type")
        or ""
    ).lower()


def event_source(event: dict[str, Any]) -> str:
    return str(
        event.get("source")
        or event.get("origin")
        or ""
    ).lower()


def is_agent_event(event: dict[str, Any]) -> bool:
    source = event_source(event)
    return source in {"agent", "assistant", "openhands", "llm"}


def is_agent_message_event(event: dict[str, Any]) -> bool:
    """Return True if *event* looks like an agent/assistant message event.

    Broadens matching beyond the original ``kind == "messageevent"`` check to
    cover additional OpenHands event schemas that may carry textual content
    under ``args.*``, ``extras.*``, or ``data.*`` keys.
    """
    kind = event_kind(event)
    source_ok = is_agent_event(event)

    llm_message = event.get("llm_message")
    role = ""
    if isinstance(llm_message, dict):
        role = str(llm_message.get("role", "")).lower()

    kind_lower = kind.lower()
    kind_ok = (
        "messageevent" in kind_lower
        or kind_lower == "message"
        or kind_lower.endswith(".message")
        or kind_lower == "agent_message"
        or kind_lower == "user_message"
    )

    role_ok = not role or role in {"assistant", "agent"}

    # Accept events with textual args/extras/data even if kind is generic
    has_textual_args = (
        event.get("args") and isinstance(event.get("args"), dict) and
        any(event["args"].get(k) for k in ("message", "content", "text", "final_thought", "observation"))
    )
    has_textual_extras = (
        event.get("extras") and isinstance(event.get("extras"), dict) and
        any(event["extras"].get(k) for k in ("message", "content", "text"))
    )
    has_textual_data = (
        event.get("data") and isinstance(event.get("data"), dict) and
        any(event["data"].get(k) for k in ("message", "content", "text"))
    )

    # Accept if kind matches AND source is agent, OR if source is agent AND has textual content
    return (kind_ok and source_ok and role_ok) or (source_ok and (has_textual_args or has_textual_extras or has_textual_data))


def extract_finish_action_text(event: dict[str, Any]) -> str:
    """Extract text from a finish-action event.

    Checks ``action.outputs.*`` first (existing path), then falls back to
    ``args.outputs.*`` and ``data.*`` paths that may carry the answer in
    newer OpenHands event schemas.
    """
    kind = event_kind(event)
    source = event_source(event)
    tool_name = str(event.get("tool_name") or "").lower()

    action = event.get("action")
    if source == "agent" and isinstance(action, dict):
        action_kind = str(action.get("kind") or "").lower()
        action_name = str(action.get("action") or "").lower()
        is_finish = (
            action_kind == "finishaction"
            or "finish" in action_kind
            or action_name == "finish"
            or tool_name == "finish"
        )

        if is_finish:
            outputs = action.get("outputs")
            outputs_content = None
            outputs_answer = None
            outputs_final_answer = None

            if isinstance(outputs, dict):
                outputs_content = outputs.get("content")
                outputs_answer = outputs.get("answer")
                outputs_final_answer = outputs.get("final_answer")

            for value in (
                action.get("message"),
                outputs_content,
                outputs_answer,
                outputs_final_answer,
                outputs,
                action.get("content"),
                action.get("text"),
            ):
                text = content_to_text(value)
                if text:
                    return text

            # Also check args.outputs if action is nested under args
            event_args = event.get("args")
            if isinstance(event_args, dict):
                nested_outputs = event_args.get("outputs")
                if isinstance(nested_outputs, dict):
                    for value in (
                        nested_outputs.get("answer"),
                        nested_outputs.get("final_answer"),
                        nested_outputs.get("content"),
                        nested_outputs,
                    ):
                        text = content_to_text(value)
                        if text:
                            return text

    if source == "environment" and kind == "observationevent" and tool_name == "finish":
        observation = event.get("observation")
        if isinstance(observation, dict):
            for value in (
                observation.get("content"),
                observation.get("message"),
                observation.get("text"),
                observation.get("result"),
                observation.get("output"),
            ):
                text = content_to_text(value)
                if text:
                    return text

        text = content_to_text(observation)
        if text:
            return text

    # Also check data.* for finish events (newer OpenHands schema)
    event_data = event.get("data")
    if isinstance(event_data, dict):
        for value in (
            event_data.get("answer"),
            event_data.get("final_answer"),
            event_data.get("content"),
            event_data.get("message"),
            event_data,
        ):
            text = content_to_text(value)
            if text:
                return text

    return ""


def extract_message_event_text(event: dict[str, Any]) -> str:
    """Extract textual content from an agent message event.

    Checks ``llm_message.content`` first (existing path), then falls back to
    ``args.*``, ``extras.*``, and ``data.*`` paths that may carry the text
    in newer OpenHands event schemas.
    """
    if not is_agent_message_event(event):
        return ""

    # Try llm_message first (existing path)
    llm_message = event.get("llm_message")
    if isinstance(llm_message, dict):
        text = content_to_text(llm_message.get("content"))
        if text:
            return text

    # Try args.* paths
    args = event.get("args")
    if isinstance(args, dict):
        for key in ("message", "content", "text", "final_thought", "observation"):
            text = content_to_text(args.get(key))
            if text:
                return text

    # Try extras.* paths
    extras = event.get("extras")
    if isinstance(extras, dict):
        for key in ("message", "content", "text"):
            text = content_to_text(extras.get(key))
            if text:
                return text

    # Try data.* paths
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("message", "content", "text"):
            text = content_to_text(data.get(key))
            if text:
                return text

    # Fallback: full event (existing)
    return content_to_text(event)


def collect_final_text_from_events(events: list[dict[str, Any]]) -> str:
    for event in events:
        text = extract_finish_action_text(event)
        if text:
            return text

    for event in events:
        text = extract_message_event_text(event)
        if text:
            return text

    return ""


def print_new_message_events(
    events: list[dict[str, Any]],
    seen_event_ids: set[str],
    collected_answers: list[str],
    verbose_events: bool = False,
) -> None:
    for event in events:
        eid = event_id(event)
        if eid in seen_event_ids:
            continue

        seen_event_ids.add(eid)

        if verbose_events:
            kind = event.get("kind")
            source = event.get("source")
            llm_message = event.get("llm_message")
            role = None
            if isinstance(llm_message, dict):
                role = llm_message.get("role")

            print(f"\n--- RAW EVENT kind={kind!r} source={source!r} role={role!r} ---")
            print(json.dumps(event, indent=2, ensure_ascii=False))

        text = extract_finish_action_text(event)
        label = "FINAL FINISH EVENT"

        if not text:
            text = extract_message_event_text(event)
            label = "AGENT MESSAGE EVENT"

        if not text:
            continue

        collected_answers.append(text)

        print(f"\n--- {label} ---")
        print(text)
        print(f"--- END {label} ---", flush=True)


def conversation_is_done(conversation: dict[str, Any]) -> bool:
    if not conversation:
        return False

    execution_status = conversation.get("execution_status")
    if execution_status is not None:
        normalized = str(execution_status).lower()
        if normalized in TERMINAL_EXECUTION_STATES:
            return True

    status = conversation.get("status")
    if status is not None:
        normalized = str(status).lower()
        if normalized in {"finished", "completed", "stopped", "failed", "error"}:
            return True

    runtime_status = conversation.get("runtime_status")
    if runtime_status is not None:
        normalized = str(runtime_status).lower()
        if normalized in {"stopped", "finished", "completed", "failed", "error"}:
            return True

    return False


def run_and_collect_message_events(
    base_url: str,
    api_key: str,
    conversation_id: str,
    poll_interval: int,
    max_polls: int,
    events_limit: int,
    events_max_pages: int,
    final_fetch_delay: int,
    verbose_events: bool,
    ignore_done_status: bool,
    stop_after_first_message: bool,
) -> list[str]:
    seen_event_ids: set[str] = set()
    collected_answers: list[str] = []

    for attempt in range(1, max_polls + 1):
        events = search_v1_events(
            base_url=base_url,
            api_key=api_key,
            conversation_id=conversation_id,
            limit=events_limit,
            max_pages=events_max_pages,
        )

        print_new_message_events(
            events=events,
            seen_event_ids=seen_event_ids,
            collected_answers=collected_answers,
            verbose_events=verbose_events,
        )

        if stop_after_first_message and collected_answers:
            print("Stopping after first extracted final/agent message event.")
            return collected_answers

        conversation = get_v1_conversation(
            base_url=base_url,
            api_key=api_key,
            conversation_id=conversation_id,
        )

        execution_status = conversation.get("execution_status")
        status = conversation.get("status")
        sandbox_status = conversation.get("sandbox_status")
        runtime_status = conversation.get("runtime_status")

        print(
            f"[{attempt}/{max_polls}] "
            f"status={status} execution={execution_status} "
            f"runtime={runtime_status} sandbox={sandbox_status} "
            f"messages={len(collected_answers)}"
        )

        if not ignore_done_status and conversation_is_done(conversation):
            print(
                "Conversation reached terminal state. "
                "Fetching final events before stopping..."
            )

            if final_fetch_delay > 0:
                print(
                    f"Waiting {final_fetch_delay}s before final event fetch "
                    "to let OpenHands flush answer events..."
                )
                time.sleep(final_fetch_delay)

            for retry in range(1, 7):
                if retry > 1:
                    print(f"Final events retry {retry}/6...")
                    time.sleep(5)

                events = search_v1_events(
                    base_url=base_url,
                    api_key=api_key,
                    conversation_id=conversation_id,
                    limit=events_limit,
                    max_pages=events_max_pages,
                )

                before = len(collected_answers)

                print_new_message_events(
                    events=events,
                    seen_event_ids=seen_event_ids,
                    collected_answers=collected_answers,
                    verbose_events=verbose_events,
                )

                if stop_after_first_message and collected_answers:
                    return collected_answers

                if len(collected_answers) > before:
                    return collected_answers

                final_text = collect_final_text_from_events(events)
                if final_text:
                    collected_answers.append(final_text)
                    print("\n--- FINAL AGENT EVENT ---")
                    print(final_text)
                    print("--- END FINAL AGENT EVENT ---", flush=True)
                    return collected_answers

            return collected_answers

        time.sleep(poll_interval)

    warn("Reached max polling attempts. Printing the last extracted MessageEvent.")
    return collected_answers


def extract_final_answer(answers: list[str]) -> str:
    for answer in reversed(answers):
        answer = answer.strip()
        if not answer:
            continue

        lowered = answer.lower()
        if lowered in {"done", "ok", "okay", "finished"}:
            continue

        return answer

    return ""


def collect_existing_conversation_answer(
    base_url: str,
    api_key: str | None,
    conversation_id: str,
    events_limit: int,
    events_max_pages: int,
    final_fetch_delay: int,
    verbose_events: bool,
) -> list[str]:
    if final_fetch_delay > 0:
        print(
            f"Waiting {final_fetch_delay}s before reading events "
            "to let OpenHands flush answer events..."
        )
        time.sleep(final_fetch_delay)

    events = search_v1_events(
        base_url=base_url,
        api_key=api_key,
        conversation_id=conversation_id,
        limit=events_limit,
        max_pages=events_max_pages,
    )

    if verbose_events:
        for event in events:
            kind = event.get("kind")
            source = event.get("source")
            llm_message = event.get("llm_message")
            role = None
            if isinstance(llm_message, dict):
                role = llm_message.get("role")

            print(f"\n--- RAW EVENT kind={kind!r} source={source!r} role={role!r} ---")
            print(json.dumps(event, indent=2, ensure_ascii=False))

    final_text = collect_final_text_from_events(events)
    if final_text:
        print("\n--- FINAL OPENHANDS EVENT ---")
        print(final_text)
        print("--- END FINAL OPENHANDS EVENT ---", flush=True)
        return [final_text]

    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Start OpenHands V1 task and print the final OpenHands answer. "
            "Can run with repo or as plain chat without repo."
        )
    )

    parser.add_argument(
        "positional_llm_model",
        nargs="?",
        help=(
            "Optional positional model. Example: openai/qwen3.6:27b. "
            "Used when --llm-model is not passed."
        ),
    )
    parser.add_argument(
        "positional_prompt",
        nargs="?",
        help=(
            "Optional positional prompt. Used when --prompt and --prompt-file are not passed."
        ),
    )

    parser.add_argument(
        "--url",
        default=os.getenv("OPENHANDS_URL", "http://localhost:3000"),
        help="OpenHands URL, example: http://192.168.10.137:3000",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPENHANDS_API_KEY"),
        help="Bearer API key for V1 API",
    )
    parser.add_argument(
        "--conversation-id",
        default=None,
        help=(
            "Do not start a new task. Extract the final answer from this "
            "existing OpenHands conversation id."
        ),
    )
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "GitHub repo full name, example: owner/repo. "
            "Optional. If omitted, starts a plain chat not bound to a repository."
        ),
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Branch to use, example: main. Used only with --repo.",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("LLM_MODEL"),
        help='LLM model for this conversation, example: "openai/qwen3:32b"',
    )
    parser.add_argument(
        "--agent-type",
        default="default",
        help='OpenHands agent type, default: "default"',
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Task prompt",
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="Read task prompt from file",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--max-polls",
        type=int,
        default=180,
        help="Maximum polling attempts",
    )
    parser.add_argument(
        "--events-limit",
        type=int,
        default=100,
        help="How many events to fetch per page, max 100",
    )
    parser.add_argument(
        "--events-max-pages",
        type=int,
        default=50,
        help="How many event pages to fetch per poll",
    )
    parser.add_argument(
        "--final-fetch-delay",
        type=int,
        default=30,
        help=(
            "Seconds to wait before the final events/search fetch after "
            "OpenHands reports a terminal state. This avoids a race where "
            "execution=finished is visible before the final answer event is flushed."
        ),
    )
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="Print raw events for debugging event schema",
    )
    parser.add_argument(
        "--ignore-done-status",
        action="store_true",
        help=(
            "Do not stop based on conversation status. "
            "Useful when OpenHands reports sandbox/error status while agent still works."
        ),
    )
    parser.add_argument(
        "--stop-after-first-message",
        action="store_true",
        help="Stop polling once at least one final/agent answer event was extracted",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Only start conversation and print URL",
    )
    parser.add_argument(
        "--answer-file",
        default=None,
        help="Write final extracted answer to this file",
    )

    args = parser.parse_args()

    if not args.llm_model and args.positional_llm_model:
        args.llm_model = args.positional_llm_model

    if not args.prompt and not args.prompt_file and args.positional_prompt:
        args.prompt = args.positional_prompt

    base_url = args.url.rstrip("/")

    if args.conversation_id:
        conversation_id = args.conversation_id
        print(f"OpenHands conversation: {base_url}/conversations/{conversation_id}")

        answers = collect_existing_conversation_answer(
            base_url=base_url,
            api_key=args.api_key,
            conversation_id=conversation_id,
            events_limit=args.events_limit,
            events_max_pages=args.events_max_pages,
            final_fetch_delay=args.final_fetch_delay,
            verbose_events=args.verbose_events,
        )
    else:
        if not args.api_key:
            fail("Set OPENHANDS_API_KEY or pass --api-key")

        if not args.llm_model:
            fail("LLM model is required: pass --llm-model, positional model, or set LLM_MODEL")

        user_prompt = read_prompt(args.prompt, args.prompt_file)

        prompt = f"""
{user_prompt}

Important:
- When you are done, send a final plain-text answer to the user.
- Do not leave the answer only inside a tool call, command output, file content, or code block.
- The final answer should summarize what you found or changed.
""".strip()

        if args.repo:
            print(f"Starting OpenHands repo conversation: repo={args.repo} branch={args.branch}")
        else:
            print("Starting OpenHands plain conversation without repository")

        result = start_v1_app_conversation(
            base_url=base_url,
            api_key=args.api_key,
            repo=args.repo,
            branch=args.branch,
            prompt=prompt,
            llm_model=args.llm_model,
            agent_type=args.agent_type,
        )

        print("Start response:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        task_id = (
            result.get("id")
            or result.get("task_id")
            or result.get("start_task_id")
        )

        conversation_id = (
            result.get("app_conversation_id")
            or result.get("conversation_id")
        )

        if not conversation_id:
            if not task_id:
                fail("V1 response has neither conversation_id nor task id")

            conversation_id = poll_v1_start_task(
                base_url=base_url,
                api_key=args.api_key,
                task_id=task_id,
            )

        print(f"\nOpenHands conversation: {base_url}/conversations/{conversation_id}")

        if args.no_wait:
            return 0

        answers = run_and_collect_message_events(
            base_url=base_url,
            api_key=args.api_key,
            conversation_id=conversation_id,
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
            events_limit=args.events_limit,
            events_max_pages=args.events_max_pages,
            final_fetch_delay=args.final_fetch_delay,
            verbose_events=args.verbose_events,
            ignore_done_status=args.ignore_done_status,
            stop_after_first_message=args.stop_after_first_message,
        )

    final_answer = extract_final_answer(answers)

    if args.answer_file:
        answer_path = Path(args.answer_file)
        answer_path.parent.mkdir(parents=True, exist_ok=True)
        answer_path.write_text(final_answer + "\n", encoding="utf-8")
        print(f"Saved final answer to: {answer_path}")

    print("\n================ FINAL OPENHANDS ANSWER ================")
    if final_answer:
        print(final_answer)
    else:
        print("No final OpenHands answer was extracted from events.")
        print("Run again with --verbose-events to inspect event schema.")
    print("========================================================")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
