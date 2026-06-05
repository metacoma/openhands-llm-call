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

## role_status

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
  "status": "running|completed|failed|timeout|cancelled|unknown",
  "summary": "Short progress summary if available",
  "has_result": false
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

### Input

```json
{
  "run_id": "20260605-abc123"
}
```

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

Get an artifact by name or role_run_id.

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
  "run_id": "20260605-abc123",
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
