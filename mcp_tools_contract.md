# MCP Role Tools Contract

## role_list

Returns available roles and their high-level capabilities.

Expected response:

```json
{
  "roles": [
    {
      "name": "scout",
      "description": "Read-only repository investigator",
      "readonly": true,
      "requires_artifacts": [],
      "output_artifact": "scout_report",
      "timeout_minutes": 60
    }
  ]
}
```

## role_start

Starts a role-specific OpenHands task.

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
