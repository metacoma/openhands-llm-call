# OpenHands Role-Orchestration Prompts

This archive contains prompts for the first-stage architecture:

User -> OpenHands chat with Head of IT prompt -> MCP role tools -> role-specific OpenHands tasks.

## Files

- `prompts/head_of_it.md` — main orchestration prompt for the OpenHands chat.
- `prompts/scout.md` — read-only repository investigation role.
- `prompts/architect.md` — implementation planning role.
- `prompts/coder.md` — implementation role, creates branch and commits changes.
- `prompts/reviewer.md` — read-only review role, emits PASS/BLOCKER and risk.
- `prompts/publisher.md` — publish instructions role, never pushes and never creates PR.
- `roles.example.yaml` — example role registry.
- `mcp_tools_contract.md` — expected MCP tool behavior.

## Intended MCP tools

Minimum required tools:

```text
role_list()
role_start(role, user_task, repo, base_branch, branch, context, artifacts)
role_status(role_run_id)
role_result(role_run_id)
```

Optional later:

```text
pipeline_status(run_id)
role_cancel(role_run_id)
artifact_get(run_id, artifact_name)
```

## Main rule

Head of IT decides workflow.
MCP role server renders role prompts, selects model, starts OpenHands task, stores artifacts, and returns structured results.
Role workers only execute their assigned role.
