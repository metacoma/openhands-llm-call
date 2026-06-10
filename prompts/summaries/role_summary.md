Summarize your previous answer for the orchestrator.

Return plain structured text only. Do not return JSON. Do not include Markdown code fences. Do not repeat the full answer. Do not decide the next role. Do not include routing advice.

## Required format

Return exactly one structured summary block between the markers below.
Each field MUST be on its own line. Do not combine fields on one line.

ROLE_SUMMARY_BEGIN
STATUS: completed|blocked
ROLE: {{ role }}
PRIMARY_ARTIFACT: {{ primary_artifact_name }}
BLOCKING: yes|no
RISK: LOW|MEDIUM|HIGH|NONE
ACTION: PASS|BLOCKER|NONE
SUMMARY: <one-line summary, max 1000 characters>
BLOCKERS:
- none
ROLE_SUMMARY_END

## Rules

- STATUS must be exactly `completed` or `blocked`.
- ROLE must be exactly `{{ role }}`.
- PRIMARY_ARTIFACT must be exactly `{{ primary_artifact_name }}`.
- Only the reviewer role may use ACTION: PASS or ACTION: BLOCKER.
- Non-reviewer roles must use ACTION: NONE.
- If there are no blockers, use exactly: `- none`
- Do not include `next_role`.
- Do not include `ready_for_next_role`.
- Put blockers under `BLOCKERS:` as bullet lines (one per line starting with `- `).

{% if role == "reviewer" %}
You are summarizing a reviewer result. ACTION is required and must be exactly PASS or BLOCKER.
{% else %}
You are not reviewer. Set ACTION: NONE. Do not decide PASS or BLOCKER.
{% endif %}
