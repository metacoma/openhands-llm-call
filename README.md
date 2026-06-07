# openhands-llm-call

## Overview

`openhands-llm-call` provides a role-based orchestration layer on top of the
OpenHands LLM Call FastAPI backend.  A Head-of-IT agent drives a pipeline of
specialized worker roles (scout → architect → coder → reviewer → publisher)
through MCP tool calls.

## Architecture

```
User / Head-of-IT
  └── OpenHands chat (prompts/head_of_it.md)
        └── MCP server (mcp_agent/server.py)
              ├── Generic OpenHands tools
              │   openhands_start_task, openhands_get_task_status,
              │   openhands_get_task_result, openhands_get_task_events,
              │   openhands_cancel_task, call_llm, check_health, check_job
              ├── Role tools
              │   role_list, role_start, role_status, role_result
              ├── Artifact tools
              │   artifact_list, artifact_get
              ├── Role registry (config/roles.yaml)
              ├── Prompt renderer (mcp_agent/prompt_renderer.py)
              ├── Role run store (mcp_agent/role_store.py)
              ├── Lock manager (mcp_agent/lock_manager.py)
              └── Artifact store (mcp_agent/artifact_store.py)
```

## Installation

1. Clone this repository.
2. Install MCP agent dependencies:

   ```bash
   pip install -r mcp_agent/requirements.txt
   ```

3. (Optional) Install OpenHands LLM Call backend:

   ```bash
   pip install -r openhands_llm/requirements.txt
   ```

## Configuration

### OpenHands backend

| Environment variable | Default | Description |
|---|---|---|
| `OPENHANDS_URL` | `http://localhost:8000` | Base URL of the OpenHands FastAPI server |
| `OPENHANDS_API_KEY` | *(empty)* | API key forwarded to the backend |
| `OPENHANDS_MAX_RUNTIME_SECONDS` | `7200` | Default max runtime (seconds) for tasks |
| `OPENHANDS_POLL_INTERVAL_SECONDS` | `10` | Polling interval (seconds) |
| `OPENHANDS_STATE_DIR` | `/tmp/openhands-llm-call-state` | Directory for generic task persistence |

### Role configuration

| Environment variable | Default | Description |
|---|---|---|
| `ROLE_CONFIG_PATH` | `config/roles.yaml` | Path to the role YAML config |
| `OPENHANDS_ROLE_STATE_DIR` | `.runs` | Directory for role run state, artifacts, and locks |
| `OPENHANDS_ROLE_LOCK_TTL_MINUTES` | `180` | Stale lock timeout (minutes) |

### MCP server bind host/port

| Environment variable | Default      | Description |
|---|---|---|
| `MCP_HOST` | `127.0.0.1` | Bind address for the MCP server. Use `0.0.0.0` in Docker. |
| `MCP_PORT` | `8000` | Bind port for the MCP server. |

### Role configuration

Worker roles are defined in `config/roles.yaml`.  Each role specifies:

- `model` — LLM model to use
- `prompt_template` — Path to the Jinja2 prompt template
- `readonly` — Whether the role is read-only (no code changes)
- `timeout_minutes` — Maximum runtime for this role
- `requires_artifacts` — List of artifact names this role needs
- `output_artifact` — Logical name for this role's output artifact

### Prompts

Worker prompts live in `prompts/<role>.md`:

```text
prompts/
  scout.md        — read-only repository investigation
  architect.md    — implementation planning
  coder.md        — code implementation (mutating)
  reviewer.md     — review with ACTION/RISK output
  publisher.md    — publish instructions with PUBLISH_STATUS
```

The Head-of-IT orchestration prompt lives at `prompts/head_of_it.md`.  It is
meant to be used as the main OpenHands chat prompt, not as a worker role.

## MCP Tools

### Generic OpenHands tools

| Tool | Purpose |
|---|---|
| `openhands_start_task` | Start a non-blocking OpenHands task |
| `openhands_get_task_status` | Poll task status |
| `openhands_get_task_result` | Fetch final task result |
| `openhands_get_task_events` | Retrieve task event log |
| `openhands_cancel_task` | Cancel a running task |
| `call_llm` | High-level LLM call (backward-compatible) |
| `check_health` | Health-check the backend |
| `check_job` | Check an async LLM job by UID |

### Role tools

| Tool | Purpose |
|---|---|
| `role_list` | List available worker roles |
| `role_start` | Start a named worker role |
| `role_wait` | Wait for a long-running role to finish (server-side polling) |
| `role_status` | Single-shot diagnostic status check (**diagnostic only** — do not poll repeatedly) |
| `role_result` | Get the result of a completed role |

#### `role_start`

**Recommended (prompt-only) usage:**

```json
{
  "role": "scout",
  "prompt": "Analyze GitHub repository https://github.com/metacoma/example on main branch. Clone it if necessary. Do not modify files.",
  "context": {
    "run_id": "20260605-abc123",
    "idempotency_key": "initial-scout"
  },
  "artifacts": {}
}
```

**Backward-compatible usage (user_task):**

> **Note:** `prompt`-only mode is preferred. `user_task` is kept for backward compatibility.
> `repo`, `base_branch`, and `branch` are deprecated — repository URL and branch should be included in the `prompt` text.

```json
{
  "role": "scout",
  "user_task": "Analyze repository and find where to implement feature X",
  "repo": "https://github.com/metacoma/example",
  "base_branch": "main",
  "branch": null,
  "context": {
    "run_id": "20260605-abc123"
  },
  "artifacts": {},
  "idempotency_key": "initial-scout"
}
```

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `role` | Yes | The role name (e.g. `scout`, `architect`, `coder`, `reviewer`, `publisher`) |
| `prompt` | Yes (or `user_task`) | The task/prompt description. Takes precedence over `user_task` |
| `user_task` | Yes (or `prompt`) | Deprecated alias for `prompt`. Kept for backward compatibility |
| `context` | No | Dict with `run_id`, `idempotency_key`, etc. |
| `artifacts` | No | Mapping of artifact names to content |
| `idempotency_key` | No | Top-level idempotency key (takes precedence over `context.idempotency_key`) |
| `repo` | No | Deprecated. If provided as a dict it is normalized to a string or discarded. No longer passed to OpenHands |
| `base_branch` | No | Deprecated. See `repo` |
| `branch` | No | Deprecated. See `repo` |

**Notes:**

- `repo`, `base_branch`, and `branch` are **deprecated**. Repository URL and branch should be included in the `prompt` text. OpenHands creates an empty/default sandbox with no selected repository metadata.
- `idempotency_key` (top-level or in `context`) prevents duplicate tasks on retry.
- `timeout_minutes` is included in the response from the role config.
- `idempotent_reuse: true` indicates the call was deduplicated.

For mutating roles (`readonly: false`, currently only `coder`), a file-based
lock is acquired on `repo|branch`. Concurrent starts for the same repo/branch
fail with a clear error.

#### `role_status`

`role_status` is a **single-shot diagnostic status check**. It is **diagnostic
only** — do not call it repeatedly in a tight loop from an LLM orchestrator.
Use `role_wait` for normal long-running role orchestration.

> **Warning:** Do not implement orchestration by repeatedly calling
> `role_status` in a tight loop. Use `role_wait` for server-side polling.
> Repeated identical `role_status` calls can trigger OpenHands' stuck-loop
> detector in the top-level orchestrator.

#### `role_result`

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "include_full_result": false
}
```

- Default `include_full_result=true` preserves existing behavior.
- When `false`, `full_result` is `null` and `full_result_omitted` is `true`,
  but artifact metadata is still returned.

#### `role_wait` — server-side polling

Wait for a long-running role to finish using server-side polling. Use this after
`role_start` instead of repeatedly calling `role_status`.

**Recommended flow:**

```text
role_start -> role_wait
```

**Example:**

1. Start the role:

```json
{
  "role": "scout",
  "prompt": "Analyze repository https://github.com/metacoma/freeplane_plugin_grpc on main branch. Only inspect, do not modify files. Return scout report.",
  "context": {},
  "artifacts": {}
}
```

2. Wait for completion:

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 15,
  "return_result": true
}
```

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `role_run_id` | Yes | The role run ID returned by `role_start` |
| `timeout_seconds` | No | Maximum seconds to wait (default 1800, clamped to [1, 7200]) |
| `poll_interval_seconds` | No | Seconds between status checks (default 15, clamped to [5, 120]) |
| `return_result` | No | If `true` (default), inline the full result. If `false`, return compact response with `result_available: true` |

**Configuration (environment variables):**

| Variable | Default | Description |
|---|---|---|
| `OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS` | `1800` | Default timeout |
| `OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS` | `15` | Default poll interval |
| `OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS` | `7200` | Maximum allowed timeout |

**Responses:**

- **Completed** (with result)::

  ```json
  {
    "role_run_id": "20260605-abc123-scout-1",
    "status": "completed",
    "has_result": true,
    "result": "...",
    "duration_seconds": 742
  }
  ```

- **Terminal failure**::

  ```json
  {
    "role_run_id": "20260605-abc123-scout-1",
    "status": "failed",
    "has_result": false,
    "error": {
      "type": "RoleFailed",
      "message": "...",
      "retryable": true
    }
  }
  ```

- **Bounded timeout** (role still running)::

  ```json
  {
    "role_run_id": "20260605-abc123-scout-1",
    "status": "running",
    "has_result": false,
    "wait_timed_out": true,
    "message": "Role is still running after bounded wait. Call role_wait again later.",
    "poll_after_seconds": 60
  }
  ```

## Role Result Contract

A role is successful only when it returns a non-empty final LLM answer.

`status=completed` with an empty answer is treated as `completed_empty_result`
or `EmptyRoleResult`.

### role_wait behavior

`role_wait(return_result=true)` waits for completion and validates the final
answer. It includes a post-completion retry window (default 60 seconds,
5-second intervals) to handle races where OpenHands marks the conversation
completed before the final assistant answer is visible through the API.

Example request:

```json
{
  "role_run_id": "...",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 30,
  "return_result": true
}
```

Successful response:

```json
{
  "status": "completed",
  "has_result": true,
  "full_result": "...non-empty answer...",
  "artifact_path_scope": "mcp_agent_state_internal",
  "artifact_access": "Use artifact_get to read this artifact. Do not read artifact_path from an OpenHands terminal."
}
```

Empty response:

```json
{
  "status": "completed_empty_result",
  "has_result": false,
  "error": {
    "type": "EmptyRoleResult",
    "message": "Role completed but did not return a final LLM answer.",
    "retryable": true,
    "suggested_next_action": "Retry this role once with a stricter final-answer prompt."
  },
  "diagnostics": {
    "conversation_id": "...",
    "task_id": "...",
    "answer_empty": true
  }
}
```

### Orchestrator rule

If `role_wait` returns `completed_empty_result`, do **not** continue to the
next role. Retry the same role once with a stricter final-answer prompt, or
stop and report the issue to the user.

### Force refresh

`role_result_impl(role_run_id, force_refresh=True)` bypasses cached empty
answers by re-fetching from the OpenHands FastAPI backend. This is used
internally by `role_wait` during its post-completion retry window.

### Artifact path scope

The `artifact_path` field in role result responses is MCP-internal and must
be read via `artifact_get`, not via terminal commands (e.g. `cat`).

```json
{
  "artifact_path": "state_dir/run_id/filename.artifact",
  "artifact_path_scope": "mcp_agent_state_internal"
}
```

### Empty artifact diagnostics

When `artifact_get` returns an artifact with empty content:

```json
{
  "content": "",
  "content_empty": true,
  "valid_role_report": false,
  "warning": {
    "type": "EmptyArtifactContent",
    "message": "Artifact exists but content is empty."
  }
}
```

### Artifact tools

| Tool | Purpose |
|---|---|
| `artifact_list` | List artifacts for a run |
| `artifact_get` | Get an artifact by name or role_run_id |

#### `artifact_list`

```json
{
  "run_id": "20260605-abc123"
}
```

Returns `{run_id, artifacts: [...]}` where each artifact includes
`artifact_name`, `role`, `role_run_id`, `artifact_path`, `created_at`.

#### `artifact_get`

Prefer `role_run_id` when available:

```json
{
  "role_run_id": "20260605-abc123-scout-1"
}
```

Or by `run_id` and `artifact_name`:

```json
{
  "run_id": "20260605-abc123",
  "artifact_name": "scout_report"
}
```

Returns artifact metadata with `content` field. Path traversal is
prevented — artifacts can only be read from the configured state directory.

> **Note:** If OpenHands wraps these values as `{"default": "..."}`, the server
> will normalize them automatically.

### OpenHands scalar argument wrapping compatibility

Some OpenHands versions may wrap scalar MCP arguments into objects like:

```json
{
  "timeout_seconds": {
    "default": 1800
  }
}
```

or:

```json
{
  "run_id": {
    "default": "20260605-abc123"
  }
}
```

The MCP server normalizes these wrapped values at the tool boundary before
passing clean types to internal implementations.

Recommended user-facing examples still use plain scalar values:

```json
{
  "timeout_seconds": 1800,
  "run_id": "20260605-abc123"
}
```

## Head-of-IT Usage Example

### Recommended orchestration flow

After `role_start`, call `role_wait`. Do not repeatedly call `role_status`.

```text
1. role_list()
   → Returns available roles

2. role_start(
      role="scout",
      prompt="Analyze repository https://github.com/metacoma/example on main branch. Do not modify files.",
      context={"run_id": "20260605-abc123", "idempotency_key": "initial-scout"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", poll_after_seconds: 30, timeout_minutes: 60}

3. role_wait(role_run_id="20260605-abc123-scout-1", timeout_seconds=1800, return_result=true)
   → If completed: continue to the next role
   → If running with wait_timed_out=true: call role_wait again later
   → If failed/stuck/timeout/cancelled: stop and decide whether to retry or report to the user

4. artifact_get(role_run_id="20260605-abc123-scout-1")
   → Returns artifact content

5. role_start(
      role="architect",
      prompt="Plan implementation based on scout report",
      artifacts={"scout_report": "<content from step 4>"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", ...}

6. Continue the pipeline: role_wait → artifact_get → role_start
```

### Legacy example (deprecated — uses `role_status` polling)

> The following example uses `role_status` polling which is **not recommended**
> for LLM orchestrators. Use `role_wait` instead.

```text
1. role_list()
   → Returns available roles

2. role_start(
      role="scout",
      user_task="Analyze repository and find where to implement feature X",
      repo="https://github.com/metacoma/example",
      base_branch="main",
      context={"run_id": "20260605-abc123", "idempotency_key": "initial-scout"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", poll_after_seconds: 30, timeout_minutes: 60}

3. role_status(role_run_id="20260605-abc123-scout-1")
   → Poll until status == "completed"

4. role_result(role_run_id="20260605-abc123-scout-1", include_full_result=false)
   → Returns: {status: "completed", action, risk, artifact_path, result_summary, full_result: null, full_result_omitted: true}

5. artifact_get(run_id="20260605-abc123", artifact_name="scout_report")
   → Returns artifact content

6. role_start(
      role="architect",
      user_task="Plan implementation",
      repo="https://github.com/metacoma/example",
      base_branch="main",
      artifacts={"scout_report": "<content from step 5>"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", ...}

7. Continue the pipeline: role_status → role_result → artifact_get → role_start
```

## v2 API — Artifact-based Role Orchestration

> **Deprecation notice:** `role_start` is legacy. Head-of-Engineering should use
> `shttp_role_start_v2`. Legacy `prompt.text` mode should not be used for
> artifact-based orchestration.

### Control plane vs data plane

The v2 API separates role execution into two planes:

```
control plane = short summaries for Head-of-Engineering
data plane    = full role artifacts for specialist roles
```

**Data plane:** Full role outputs are stored as artifacts:

| Role | Output artifact | Summary artifact |
|---|---|---|
| scout | scout_report | scout_summary |
| architect | architect_plan | architect_summary |
| coder | coder_report | coder_summary |
| reviewer | reviewer_report | reviewer_summary |
| coder_fix | coder_fix_result | coder_fix_summary |
| publisher | publish_instructions | publisher_summary |

These artifacts may be long and detailed. They are passed to later roles by
ID/path/name only — **never** pasted into JSON payloads.

**Control plane:** Every role execution produces a compact control summary.
Head-of-Engineering reads this to decide routing. The summary is short,
structured, and safe to return inline.

### Required artifact matrix

| Role | Required artifacts | Output artifact |
|---|---|---|
| scout | *(none)* | scout_report |
| architect | scout_report | architect_plan |
| coder | scout_report, architect_plan | coder_report |
| reviewer | scout_report, architect_plan, coder_report | reviewer_report |
| coder_fix | architect_plan, coder_report, reviewer_report | coder_fix_result |
| publisher | coder_report, reviewer_report | publish_instructions |

### Summary JSON schema

Every role produces a summary with this schema:

```json
{
  "status": "completed" | "blocked",
  "role": "<role>",
  "summary": "<short factual summary for Head-of-Engineering>",
  "primary_artifact_name": "<artifact name>",
  "blocking": true | false,
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | null,
  "action": "PASS" | "BLOCKER" | null,
  "blocking_summary": ["..."]
}
```

**Rules:**

- Only reviewer may set `action` to `PASS` or `BLOCKER`.
- Non-reviewer roles must set `action` to `null`.
- The summary must **not** include `next_role` or `ready_for_next_role`.
- Keep `summary` under 1000 characters unless blockers require more detail.

### Two-step same-conversation lifecycle

Each role run follows this lifecycle:

```
created
→ main_prompt_rendered
→ main_prompt_sent
→ main_response_received
→ primary_artifact_saved
→ summary_prompt_sent
→ summary_response_received
→ summary_artifact_saved
→ completed
```

If summary parsing fails, one repair attempt is made. If repair also fails,
a safe fallback summary is returned.

### `shttp_role_start_v2`

Start a role using artifact ID references (not raw content). The MCP server
resolves artifacts server-side and returns the control summary inline.

**Example — scout:**

```json
{
  "role": {"text": "scout"},
  "user_task": {"text": "Implement a Ruby gRPC client for freeplane_plugin_grpc."},
  "metadata": {
    "repository": {"text": "https://github.com/metacoma/freeplane_plugin_grpc"}
  }
}
```

**Example — architect (with artifact reference):**

```json
{
  "role": {"text": "architect"},
  "user_task": {"text": "Implement a Ruby gRPC client for freeplane_plugin_grpc."},
  "input_artifacts": {
    "scout_report": {"text": "20260607-010712-647d95/20260607-010712-647d95-scout-1_scout_report.artifact"}
  }
}
```

**Response:**

```json
{
  "role_run_id": "20260607-010712-647d95-architect-1",
  "status": "completed",
  "control_summary": {
    "status": "completed",
    "role": "architect",
    "summary": "Architect plan produced with 5 file changes.",
    "primary_artifact_name": "architect_plan",
    "blocking": false,
    "risk_level": "LOW",
    "action": null,
    "blocking_summary": []
  },
  "artifacts": {
    "primary": {
      "artifact_name": "architect_plan",
      "artifact_path": "..."
    },
    "summary": {
      "artifact_name": "architect_summary",
      "artifact_path": "..."
    }
  }
}
```

### `shttp_role_wait_v2`

Wait for a v2 role run. Same shape as `role_wait` but for v2 runs.

### `shttp_role_result_v2`

Get result for a v2 role run. Returns control summary inline and artifact
paths (not content by default).

```json
{
  "role_run_id": "...",
  "include_full_artifacts": false,
  "return_control_summary": true
}
```

### Head-of-Engineering routing logic

Route based on role order and control summary — **not** on `next_role` from
the role:

```
after scout completed and blocking=false:
    start architect

after architect completed and blocking=false:
    start coder

after coder completed and blocking=false:
    start reviewer

after reviewer action=PASS:
    start publisher

after reviewer action=BLOCKER and fix cycle not used:
    start coder_fix

after reviewer action=BLOCKER and fix cycle already used:
    stop as blocked
```

For non-reviewer roles:

```
if blocking=true:
    stop or ask user
```

### Migration from legacy `role_start`

| Legacy (`role_start`) | v2 (`shttp_role_start_v2`) |
|---|---|
| Pass artifact content inline in `artifacts` | Pass artifact IDs/paths in `input_artifacts` |
| Returns `run_id`, `role_run_id` only | Returns `control_summary` inline |
| Orchestrator reads artifacts to decide routing | Orchestrator reads control summary to decide routing |
| No summary mechanism | In-conversation summary with JSON validation |

**Before (legacy):**

```json
{
  "role": "architect",
  "user_task": "Plan implementation",
  "artifacts": {
    "scout_report": "<full scout report content...>"
  }
}
```

**After (v2):**

```json
{
  "role": {"text": "architect"},
  "user_task": {"text": "Plan implementation"},
  "input_artifacts": {
    "scout_report": {"text": "20260607-010712-647d95/...-scout-1_scout_report.artifact"}
  }
}
```

### Example full role chain (v2)

```text
1. shttp_role_start_v2(scout, user_task="...")
   → control_summary.status = "completed"

2. shttp_role_start_v2(architect, user_task="...", input_artifacts={scout_report: "<path>"})
   → control_summary.status = "completed"

3. shttp_role_start_v2(coder, user_task="...", input_artifacts={scout_report: "<path>", architect_plan: "<path>"})
   → control_summary.status = "completed"

4. shttp_role_start_v2(reviewer, user_task="...", input_artifacts={scout_report: "<path>", architect_plan: "<path>", coder_report: "<path>"})
   → control_summary.action = "PASS" or "BLOCKER"

5. If action = PASS:
     shttp_role_start_v2(publisher, ...)
   If action = BLOCKER:
     shttp_role_start_v2(coder_fix, ...)
```

## LLM-safe MCP usage

### Correct role flow

1. Call ``role_start``.
2. Extract only the string field ``role_run_id``.
3. Call ``role_wait`` with flat JSON arguments.
4. Use ``artifact_get`` to read artifacts.
5. Pass artifact contents to the next role.
6. Start the next role only after the previous role has completed.

**Correct:**

```json
{
  "role_run_id": "RUN-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 15,
  "return_result": true
}
```

**Incorrect:**

```json
{
  "role_run_id": {
    "role_run_id": "RUN-scout-1",
    "status": "running"
  }
}
```

If ``role_wait`` fails because of malformed arguments, do **not** call ``role_start`` again.
Retry ``role_wait`` with the existing ``role_run_id``.

### Single-threaded execution

This server assumes the underlying model may only run one role at a time.

**Do not start multiple roles in parallel.**

**Correct:**

```text
role_start scout
role_wait scout
artifact_get scout
role_start architect
role_wait architect
artifact_get architect
role_start coder
role_wait coder
artifact_get coder
role_start reviewer
role_wait reviewer
artifact_get reviewer
```

**Incorrect:**

```text
role_start scout
role_start architect
role_start coder
```

If you attempt to start a second role while the first is still running, the server returns:

```json
{
  "error": "another_role_running",
  "message": "Another role is already running. This MCP server is configured for single-threaded model execution. Wait for the current role using role_wait before starting the next role.",
  "active_role_run_id": "20260606-215637-1c1074-scout-1",
  "active_role": "scout",
  "active_status": "running",
  "next_action": {
    "tool": "role_wait",
    "arguments": {
      "role_run_id": "20260606-215637-1c1074-scout-1",
      "timeout_seconds": 1800,
      "poll_interval_seconds": 15,
      "return_result": true
    }
  }
}
```

### Stale active lock prevention

The server prevents parallel role execution by scanning persisted role-run
records. To avoid **stale locks** (where a persisted `status: "running"`
persists after the actual OpenHands task has completed), the server
refreshes the actual OpenHands task status before treating a non-terminal
record as active.

If the refresh succeeds and the actual status is terminal, the persisted
record is updated and the lock is cleared automatically.

If the refresh fails (OpenHands unavailable), the server treats the role
as active and includes a warning in the error message.

### Troubleshooting

#### another_role_running

This means a previous role is still active or could not be proven terminal.

Use the `next_action` field and call `role_wait` with the provided
`role_run_id`.

Do not call `role_start` again unless the previous role reached a
terminal state.

If you see the message "The active role status could not be refreshed
from OpenHands; the lock may be stale," it means the server could not
verify whether the previous role has actually finished. In this case:

1. Check the OpenHands backend directly for the task status.
2. If the task has completed, you can manually delete or update the
   role-run JSON file in `OPENHANDS_ROLE_STATE_DIR` to clear the stale lock.
3. Alternatively, wait for the OpenHands backend to become available and
   retry.

#### Unknown or missing OpenHands task status

If the server cannot prove that an existing role has reached a terminal
status (e.g., OpenHands returns `"unknown"`, an empty response, or the
API is unavailable), it preserves single-threaded safety and treats the
role as still active.

In this case, `role_start` may return `another_role_running` with
`refresh_failed: true` and a `refresh_warning` explaining the issue.

**What to do:**

1. Follow the `next_action` field and call `role_wait` for the existing
   `role_run_id`.
2. Do NOT start another role until the previous role is confirmed terminal.
3. If the OpenHands backend is temporarily unavailable, wait and retry.
4. If the task has actually completed (verified externally), manually
   update or delete the role-run JSON file in `OPENHANDS_ROLE_STATE_DIR`
   to clear the stale lock.

## Long-running task behavior

- **Per-role timeouts**: Each role in `config/roles.yaml` specifies
  `timeout_minutes`. The `coder` role defaults to 120 minutes; `publisher`
  to 30 minutes. This is converted to `max_polls` and passed to the
  OpenHands backend.

- **Mutating role locks**: Roles with `readonly: false` acquire a file-based
  lock on `repo|branch` before starting. Concurrent attempts for the same
  repo/branch fail fast with a clear error. Locks expire after
  `OPENHANDS_ROLE_LOCK_TTL_MINUTES` (default 180 min) for crash recovery.

- **Idempotent role_start**: Providing an `idempotency_key` (top-level or in
  `context`) deduplicates retry calls. The uniqueness scope is
  `run_id:role:idempotency_key`. Duplicate calls return the existing
  `role_run_id` with `idempotent_reuse: true`.

## Docker

Two services are defined in `docker-compose.yml`:

- `openhands_llm` — OpenHands LLM Call FastAPI backend
- `mcp_agent` — MCP server that proxies to the backend

```bash
docker-compose up
```

### MCP server access

**Transport:** Streamable HTTP (SHTTP)

**URL from same Docker network:**
```
http://mcp_agent:8000/mcp
```

**URL from host:**
```
http://127.0.0.1:8002/mcp
```

**Key:** empty or dummy

The MCP server binds to `0.0.0.0:8000` inside the container (configurable via `MCP_HOST`/`MCP_PORT` env vars). From the Docker host, use port `8002` (mapped via `docker-compose.yml`).

## Development

```bash
# Install dependencies
pip install -r mcp_agent/requirements.txt
pip install pytest

# Run tests
python -m pytest

# Byte-compile check
python -m compileall mcp_agent
```

## Testing

Tests are in `tests/`:

| File | Coverage |
|---|---|
| `test_role_tools.py` | parse_action, parse_risk, make_summary, role_list, role_start validation, role_status, role_result |
| `test_role_store.py` | RoleRunStore create/get/update/save_artifact/get_artifact/get_attempt_count |
| `test_roles.py` | load_roles, get_role, list_roles, validation |
| `test_task_store.py` | TaskStore CRUD, idempotency, persistence |
| `test_mcp_tools.py` | Mocked OpenHands integration for all generic tools |
| `test_prompt_renderer.py` | Jinja2 prompt rendering |
| `test_lock_manager.py` | Lock acquire/release/conflict/stale handling |
| `test_artifact_store.py` | Artifact save/list/get/path-traversal prevention |

New tests added for this hardening:

- Idempotency: first call creates task, duplicate call returns existing
- Timeout: per-role `timeout_minutes` converted to `max_polls`
- Locks: acquire, conflict, readonly bypass, release on completion, stale expiry
- Artifacts: save, list, get, path traversal rejection
- `include_full_result`: false omits full_result, true preserves old behavior
