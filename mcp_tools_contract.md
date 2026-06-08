# MCP Role Tools Contract

## role_list

Returns available roles and their high-level capabilities.

Expected response:

```json
{
  "roles": [
    {
      "name": "scout",
      "readonly": true,
      "requires_artifacts": [],
      "output_artifact_type": "scout_report"
    },
    {
      "name": "architect",
      "readonly": true,
      "requires_artifacts": ["scout_report"],
      "output_artifact_type": "architect_plan"
    }
  ]
}
```

## role_call (Recommended — Primary API)

Calls a specialist role. The MCP server resolves artifact IDs server-side and returns a compact **control summary** inline after a two-step same-conversation lifecycle (main prompt → response → summary prompt → response → validation).

**This is the only public tool Head of IT uses to invoke a worker role.**

**All fields are plain scalar values.** Do NOT pass `metadata` dict. Do NOT pass `input_artifacts` list.

### Required input fields

Every role call must include:

- `role` — the role name
- `user_task` — the user task text (required, non-empty)

Optional flat fields:

- `repository` — repository URL (e.g. `https://github.com/...`)
- `feature` — feature name (e.g. `ruby-grpc-client`)
- `scout_report_artifact_id` — artifact ID of scout report (`art_...`)
- `architect_plan_artifact_id` — artifact ID of architect plan (`art_...`)
- `coder_report_artifact_id` — artifact ID of coder report (`art_...`)
- `reviewer_report_artifact_id` — artifact ID of reviewer report (`art_...`)
- `publisher_instructions_artifact_id` — artifact ID of publisher instructions (`art_...`)
- `idempotency_key` — deduplication key

> **Note:** `api_key`, `llm_model`, and `url` are internal-only. They are read from environment variables (`OPENHANDS_API_KEY`, `OPENHANDS_LLM_MODEL`, `OPENHANDS_URL`) and must **not** be passed by Head of IT.

### Example scout start

```json
{
  "role": "scout",
  "user_task": "Research repository https://github.com/metacoma/freeplane_plugin_grpc before adding Ruby gRPC client.",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "idempotency_key": "ruby-grpc-client-scout"
}
```

### Example architect start (with scout_report_artifact_id)

```json
{
  "role": "architect",
  "user_task": "Design minimal implementation of Ruby gRPC client for repository https://github.com/metacoma/freeplane_plugin_grpc.",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "scout_report_artifact_id": "art_20260608-090234-8e83d5_scout_1_scout_report",
  "idempotency_key": "ruby-grpc-client-architect"
}
```

### Example coder start (with scout_report_artifact_id + architect_plan_artifact_id)

```json
{
  "role": "coder",
  "user_task": "Implement Ruby gRPC client per architect plan.",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "scout_report_artifact_id": "art_..._scout_report",
  "architect_plan_artifact_id": "art_..._architect_plan",
  "idempotency_key": "ruby-grpc-client-coder"
}
```

### Example reviewer start (with scout_report_artifact_id + architect_plan_artifact_id + coder_report_artifact_id)

```json
{
  "role": "reviewer",
  "user_task": "Review Ruby gRPC client implementation.",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "scout_report_artifact_id": "art_..._scout_report",
  "architect_plan_artifact_id": "art_..._architect_plan",
  "coder_report_artifact_id": "art_..._coder_report",
  "idempotency_key": "ruby-grpc-client-reviewer"
}
```

### Example publisher start (with reviewer_report_artifact_id)

```json
{
  "role": "publisher",
  "user_task": "Prepare PR instructions for Ruby gRPC client.",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "reviewer_report_artifact_id": "art_..._reviewer_report",
  "idempotency_key": "ruby-grpc-client-publisher"
}
```

### Example coder_fix start (after reviewer BLOCKER)

```json
{
  "role": "coder_fix",
  "user_task": "Fix only blockers from reviewer_report for Ruby gRPC client.",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "scout_report_artifact_id": "art_..._scout_report",
  "architect_plan_artifact_id": "art_..._architect_plan",
  "coder_report_artifact_id": "art_..._coder_report",
  "reviewer_report_artifact_id": "art_..._reviewer_report",
  "idempotency_key": "ruby-grpc-client-coder-fix-1"
}
```

### Response (success)

```json
{
  "role_run_id": "20260607-abc123-scout-1",
  "run_id": "20260607-abc123",
  "role": "scout",
  "status": "completed",
  "control_summary": {
    "valid": true,
    "status": "completed",
    "role": "scout",
    "summary": "Scout completed. Found 42 relevant files.",
    "blocking": false,
    "risk_level": "LOW",
    "action": null,
    "blocking_summary": []
  },
  "artifacts": {
    "primary": {
      "artifact_id": "art_20260607-abc123_scout_1_scout_report",
      "artifact_type": "scout_report",
      "created_by": "scout"
    },
    "summary": {
      "artifact_id": "art_20260607-abc123_scout_1_control_summary",
      "artifact_type": "control_summary",
      "created_by": "scout"
    }
  }
}
```

### Response (validation failure)

```json
{
  "status": "failed",
  "error": {
    "type": "UnknownRole",
    "message": "unknown role: architect2",
    "retryable": false
  }
}
```

Other validation errors:

- `"user_task is required"` — missing or empty user_task
- `"missing required artifact: scout_report. Provide scout_report_artifact_id."` — required artifact not provided (message includes the flat field name)
- `"scout_report_artifact_id must be an artifact id like art_..., got ..."` — invalid artifact ID format
- `"artifact not found: scout_report"` — artifact reference does not resolve
- `"rendered prompt is empty"` — template rendering produced empty output
- `"role_call now uses flat scalar fields. Do not pass nested input_artifacts or metadata."` — old nested payload rejected

## role_start (Legacy — hidden)

Starts a role-specific OpenHands task. **Deprecated and hidden from public MCP discovery.** Use `role_call` instead.

### Input (recommended — prompt-only)

```json
{
  "role": "scout",
  "prompt": "Analyze GitHub repository https://github.com/metacoma/example on main branch. Clone it if necessary. Do not modify files.",
  "context": {
    "run_id": "optional-existing-run-id",
    "idempotency_key": "optional-key"
  },
  "artifacts": {
    "scout_report": "optional previous artifact text"
  },
  "idempotency_key": "optional-top-level-key"
}
```

### Input (backward-compatible — user_task)

```json
{
  "role": "scout",
  "user_task": "Analyze repository and find where to implement feature X",
  "context": {},
  "artifacts": {},
  "idempotency_key": "optional-top-level-key"
}
```

### Deprecated parameters

The following parameters are **deprecated** and **no longer passed** to OpenHands as
selected-repository metadata. They are accepted for backward compatibility only:

- `repo` — Deprecated. No longer passed to OpenHands. Repository instructions should
  live in the `prompt` text.
- `base_branch` — Deprecated. No longer passed to OpenHands.
- `branch` — Deprecated. No longer passed to OpenHands.

If any of these are provided as nested objects (e.g. `{"url": "..."}`), they are
normalized to strings or discarded.

### Prompt resolution

- `prompt` takes precedence over `user_task` if both are provided.
- At least one of `prompt` or `user_task` is required.

### Uniqueness scope

`run_id:role:idempotency_key`.

- `idempotency_key` may be provided at top-level or in `context`.
  Top-level takes precedence. Empty string is treated as not provided.

### Response (success)

```json
{
  "run_id": "20260605-abc123",
  "role_run_id": "20260605-abc123-scout-1",
  "role": "scout",
  "status": "running",
  "poll_after_seconds": 30,
  "timeout_minutes": 60,
  "idempotent_reuse": false
}
```

### Response (idempotent reuse)

When a duplicate idempotency key is detected:

```json
{
  "run_id": "20260605-abc123",
  "role_run_id": "20260605-abc123-scout-1",
  "role": "scout",
  "status": "running",
  "poll_after_seconds": 30,
  "timeout_minutes": 60,
  "idempotent_reuse": true
}
```

### Response (lock conflict — mutating roles only)

```json
{
  "status": "failed",
  "error": {
    "type": "MutatingRoleLockActive",
    "message": "Mutating role lock is active for repo/branch '...'. Existing role_run_id: ...",
    "retryable": true
  }
}
```

### Response (single-active-role violation)

When another role is already running and the idempotency key does not match:

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

**Note**: If the server could not refresh the active role status from
OpenHands, the `message` field will include the text "The active role
status could not be refreshed from OpenHands; the lock may be stale."
In this case, check the OpenHands backend directly or wait for it to
become available.

### Stale active lock prevention

The server refreshes actual OpenHands task status before treating a
non-terminal persisted record as active. If the refresh succeeds and
the actual status is terminal, the persisted record is updated and
the lock is cleared automatically. If the refresh fails, the server
treats the role as active with a warning in the error message.

If the refresh succeeds but returns a missing, empty, or `"unknown"`
status, the server treats the role as active to preserve single-threaded
safety. The error message includes `refresh_failed: true` and a
`refresh_warning` explaining the issue.

## role_status

Single-shot diagnostic status check. Do not call repeatedly in a tight loop; use `role_wait` for server-side polling.

Expected input:

```json
{
  "role_run_id": "20260605-abc123-scout-1"
}
```

Expected response:

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "running|completed|failed|timeout|cancelled",
  "summary": "Short progress summary if available",
  "has_result": false
}

**Note:** `"unknown"` may appear when OpenHands returns a missing or
unrecognizable status. In that case the server treats the role as active
to preserve single-threaded safety — see the README section on unknown
or missing OpenHands task status.
```

## role_wait

Wait for an existing role run using server-side polling. Use this after `role_start` instead of repeatedly calling `role_status`.

**Pass ONLY the ``role_run_id`` string returned by ``role_start``.**

Correct:

```json
{"role_run_id":"RUN-scout-1","timeout_seconds":1800,"poll_interval_seconds":15,"return_result":true}
```

Incorrect (do not pass the full role_start response object):

```json
{"role_run_id":{"role_run_id":"RUN-scout-1","status":"running"}}
```

If your previous ``role_wait`` call had malformed arguments, retry ``role_wait`` with the same ``role_run_id``.
Do **not** start the role again.

### Input

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 15,
  "return_result": true
}
```

- `role_run_id` — required. The role run ID returned by `role_start`. Accepts both plain strings and dict-wrapped values (e.g. `{"text": "..."}`).
- `timeout_seconds` — optional. Maximum seconds to wait (default 1800, clamped to [1, 7200]). Override with env var `OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS`.
- `poll_interval_seconds` — optional. Seconds between status checks (default 15, clamped to [5, 120]). Override with env var `OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS`.
- `return_result` — optional. If `true` (default) and the role completed, inline the full result. If `false`, return a compact response with `result_available: true`.

### Error (single-active-role violation)

If another role is already running, `role_start` returns:

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

### Response (completed with inline result)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "completed",
  "action": "CONTINUE",
  "risk": null,
  "artifact_name": "scout_report",
  "artifact_path": "runs/20260605-abc123/01-scout.answer.md",
  "result_summary": "Short summary",
  "full_result": "Full markdown report",
  "full_result_omitted": false,
  "duration_seconds": 742
}
```

### Response (completed, compact — return_result=false)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "status": "completed",
  "has_result": true,
  "result_available": true,
  "next_action": "call role_result",
  "duration_seconds": 742
}
```

### Response (terminal failure)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "status": "failed",
  "has_result": false,
  "error": {
    "type": "RoleFailed",
    "message": "...",
    "retryable": true
  },
  "duration_seconds": 1234
}
```

### Response (all terminal statuses)

`role_wait` always returns a terminal status when the underlying role has
reached a terminal state. It must **not** report `running` for terminal
statuses such as `error`, `timed_out`, `canceled`, or `completed_empty_result`.

| Status | Error type | Notes |
|---|---|---|
| `completed` | — | Returns result (or compact response if `return_result=false`) |
| `completed_empty_result` | `EmptyRoleResult` | Has `diagnostics` with `answer_empty: true` |
| `failed` | `RoleFailed` | — |
| `stuck` | `OpenHandsStuckError` | — |
| `error` | `RoleError` | — |
| `cancelled` | `RoleCancelled` | — |
| `canceled` | `RoleCancelled` | Normalized error type; status preserves spelling |
| `timeout` | `RoleTimeout` | — |
| `timed_out` | `RoleTimeout` | Normalized error type; status preserves spelling |

Any terminal status not listed above will return:

```json
{
  "role_run_id": "...",
  "status": "<actual_status>",
  "has_result": false,
  "error": {
    "type": "RoleTerminalStatus",
    "message": "Role ended with terminal status '<actual_status>'.",
    "retryable": true
  },
  "duration_seconds": <N>
}
```

### Response (bounded timeout — role still running)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "status": "running",
  "has_result": false,
  "wait_timed_out": true,
  "message": "Role is still running after bounded wait. Call role_wait again later.",
  "poll_after_seconds": 60,
  "duration_seconds": 1800
}
```

## role_result

### Input

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "include_full_result": true
}
```

- `include_full_result` defaults to `true` for backward compatibility.

### Response (full result)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "completed",
  "action": "CONTINUE",
  "risk": null,
  "artifact_name": "scout_report",
  "artifact_path": "runs/20260605-abc123/01-scout.answer.md",
  "result_summary": "Short summary",
  "full_result": "Full markdown report",
  "full_result_omitted": false
}
```

### Response (compact — include_full_result=false)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "completed",
  "action": "CONTINUE",
  "risk": null,
  "artifact_name": "scout_report",
  "artifact_path": "runs/20260605-abc123/01-scout.answer.md",
  "result_summary": "Short summary",
  "full_result": null,
  "full_result_omitted": true
}
```

### Response for reviewer role

```json
{
  "role": "reviewer",
  "status": "completed",
  "action": "PASS|BLOCKER",
  "risk": "LOW|MEDIUM|HIGH",
  "artifact_name": "reviewer_report",
  "result_summary": "Short review summary",
  "full_result": "Full markdown report"
}
```

## artifact_list

List artifacts for a given run.

**Pass ``role_run_id`` as a plain string.** Do **not** pass the entire ``role_start`` or ``role_wait`` response object.

### Input (by run_id)

```json
{
  "run_id": "20260605-abc123"
}
```

### Input (by role_run_id)

```json
{
  "role_run_id": "20260605-abc123-scout-1"
}
```

When ``role_run_id`` is provided, the ``run_id`` is resolved from the role run record.

### Response

```json
{
  "run_id": "20260605-abc123",
  "artifacts": [
    {
      "artifact_name": "scout_report",
      "role": "scout",
      "role_run_id": "20260605-abc123-scout-1",
      "artifact_path": "runs/20260605-abc123/...",
      "created_at": "..."
    }
  ]
}
```

## artifact_get

Read artifact content produced by a role run. Prefer this tool over reading artifact_path from the sandbox filesystem.

**Pass ``role_run_id`` as a plain string.** Do **not** pass the entire ``role_start`` or ``role_wait`` response object.

### Input (by artifact name)

```json
{
  "run_id": "20260605-abc123",
  "artifact_name": "scout_report"
}
```

### Input (by role_run_id)

```json
{
  "role_run_id": "20260605-abc123-scout-1"
}
```

### Response

```json
{
  "run_id": "20260605-abc123",
  "artifact_name": "scout_report",
  "role": "scout",
  "role_run_id": "20260605-abc123-scout-1",
  "artifact_path": "runs/20260605-abc123/...",
  "content": "Full artifact text ..."
}
```

### Error (not found)

```json
{
  "status": "failed",
  "error": {
    "type": "ArtifactNotFound",
    "message": "Artifact not found for run_id='...'",
    "retryable": false
  }
}
```

## _internal_role_wait (Legacy — hidden)

Wait for a role run. **Deprecated and hidden from public MCP discovery.** Same shape as `role_wait` but operates on internal role runs.

### Input

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 15,
  "return_result": true
}
```

### Response

Same shape as `role_wait` response.

## _internal_role_result (Legacy — hidden)

Get result for a role run. Returns control summary inline and artifact references (not content). **Deprecated and hidden from public MCP discovery.**

### Input

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "include_full_artifacts": false,
  "return_control_summary": true
}
```

### Response (compact — default)

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "completed",
  "control_summary": {
    "valid": true,
    "status": "completed",
    "role": "scout",
    "summary": "Scout completed.",
    "primary_artifact_name": "scout_report",
    "blocking": false,
    "risk_level": "LOW",
    "action": null,
    "blocking_summary": []
  },
  "artifacts": {
    "primary": {
      "artifact_name": "scout_report",
      "artifact_path": "runs/20260605-abc123/01-scout.answer.md"
    },
    "summary": {
      "artifact_name": "scout_summary",
      "artifact_path": "runs/20260605-abc123/..."
    }
  }
}
```

## Control Plane vs Data Plane

The v2 API separates role execution into two planes:

**Data plane** — Full role outputs are stored as artifacts:

| Role | Output artifact | Summary artifact |
|---|---|---|
| scout | scout_report | scout_summary |
| architect | architect_plan | architect_summary |
| coder | coder_report | coder_summary |
| reviewer | reviewer_report | reviewer_summary |
| coder_fix | coder_fix_result | coder_fix_summary |
| publisher | publisher_instructions | publisher_summary |

These artifacts may be long and detailed. They are passed to later roles by ID/path/name only.

**Control plane** — Every role execution also produces a compact control summary. Head of Engineering reads this to decide routing. The summary is short, structured, and safe to return inline.

## Summary JSON Schema

The control summary follows this schema:

```json
{
  "valid": true,
  "status": "completed" | "blocked",
  "role": "<role>",
  "summary": "<short factual summary for Head of Engineering>",
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
- No `next_role` field allowed.
- No `ready_for_next_role` field allowed.
- Keep summary under 1000 characters unless blockers require more detail.

**Reviewer-specific rules:**

- `action` is required and must be exactly `PASS` or `BLOCKER`.
- If the review found blockers, `blocking` must be `true` and `blocking_summary` must list the blockers.
- If the review passed, `blocking` must be `false` and `blocking_summary` must be `[]`.

## Required Artifact Matrix by Role

| Role | Required artifacts | Output artifact | Summary artifact |
|---|---|---|---|
| scout | (none) | scout_report | scout_summary |
| architect | scout_report | architect_plan | architect_summary |
| coder | scout_report, architect_plan | coder_report | coder_summary |
| reviewer | scout_report, architect_plan, coder_report | reviewer_report | reviewer_summary |
| coder_fix | architect_plan, coder_report, reviewer_report | coder_fix_result | coder_fix_summary |
| publisher | coder_report, reviewer_report | publisher_instructions | publisher_summary |

## Migration from Legacy to role_call

### Key differences

| Aspect | Legacy (`role_start`) | role_call (canonical) |
|---|---|---|
| Artifact passing | Full artifact content inline | Artifact ID only |
| Prompt rendering | Orchestrator assembles prompt | MCP server renders server-side via Jinja |
| Summary | None (manual `make_summary`) | In-conversation summary JSON |
| Control summary | Not returned | Returned inline |
| Validation | Minimal | Required artifacts, user_task, role |
| Public API | Multiple tools | Only `role_list` + `role_call` |

### Migration steps

1. Replace `role_start` calls with `role_call`.
2. Replace `input_artifacts` list/dict with dedicated flat artifact ID fields.
3. Replace `metadata` dict with `repository` and `feature` string fields.
4. Read `control_summary` from the response instead of parsing full artifacts.
5. Route based on control summary fields (`status`, `blocking`, `action`) not on `next_role`.
6. Pass only `artifact_id` (not content) between roles via dedicated flat fields.

### Example migration

**Before (legacy):**

```json
{
  "role": "architect",
  "user_task": "Plan implementation",
  "repo": "https://github.com/example/repo",
  "base_branch": "main",
  "artifacts": {
    "scout_report": "<full scout report content>"
  }
}
```

**After (flat role_call):**

```json
{
  "role": "architect",
  "user_task": "Plan implementation",
  "repository": "https://github.com/example/repo",
  "feature": "feature-name",
  "scout_report_artifact_id": "art_20260607_xxx_scout_report",
  "idempotency_key": "feature-architect"
}
```

**Note:** `role_call` is the canonical public API. Legacy `role_start` is hidden from MCP discovery. Head of IT should never read artifact content — only pass artifact_id via dedicated flat fields and let MCP resolve it server-side.
