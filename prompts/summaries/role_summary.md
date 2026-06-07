Summarize your previous answer for the orchestrator.

Return compact JSON only.
Do not include Markdown.
Do not include code blocks.
Do not repeat the full answer.
Do not decide the next role.
Do not include routing advice.

Schema:
{
  "status": "completed" | "blocked",
  "role": "<role>",
  "summary": "<short factual summary for Head of Engineering>",
  "primary_artifact_name": "<artifact name>",
  "blocking": true | false,
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | null,
  "action": "PASS" | "BLOCKER" | null,
  "blocking_summary": ["..."]
}

Rules:
- Only reviewer may set action to PASS or BLOCKER.
- Non-reviewer roles must set action to null.
- Do not include next_role.
- Do not include ready_for_next_role.
- Keep summary under 1000 characters unless blockers require more detail.

{% if role == "reviewer" %}
You are summarizing a reviewer result.
The field action is required and must be exactly PASS or BLOCKER.
If the review found blockers, blocking must be true and blocking_summary must list the blockers.
If the review passed, blocking must be false and blocking_summary must be [].
{% else %}
You are not reviewer.
Set action to null.
Do not decide PASS or BLOCKER.
{% endif %}
