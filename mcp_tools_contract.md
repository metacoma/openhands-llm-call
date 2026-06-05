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
      "output_artifact": "scout_report"
    }
  ]
}
```

## role_start

Starts a role-specific OpenHands task.

Expected input:

```json
{
  "role": "scout",
  "user_task": "Analyze repository and find where to implement feature X",
  "repo": "https://github.com/metacoma/example",
  "base_branch": "main",
  "branch": null,
  "context": {
    "run_id": "optional-existing-run-id"
  },
  "artifacts": {
    "scout_report": "optional previous artifact text"
  }
}
```

Expected response:

```json
{
  "run_id": "20260605-abc123",
  "role_run_id": "20260605-abc123-scout-1",
  "role": "scout",
  "status": "running",
  "poll_after_seconds": 30
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
  "role": "scout",
  "status": "running|completed|failed|timeout|cancelled",
  "summary": "Short progress summary if available",
  "has_result": false
}
```

## role_result

Expected response for successful role:

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
  "full_result": "Full markdown report"
}
```

Expected response for reviewer:

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
