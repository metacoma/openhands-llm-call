Your previous response could not be parsed.

Return plain structured text only. Do not return JSON. Do not include Markdown code fences.

Use exactly this format:

ROLE_SUMMARY_BEGIN
STATUS: completed|blocked
ROLE: {{ role }}
PRIMARY_ARTIFACT: {{ primary_artifact_name }}
BLOCKING: yes|no
RISK: LOW|MEDIUM|HIGH|NONE
ACTION: PASS|BLOCKER|NONE
SUMMARY: short one-line summary, max 1000 characters
BLOCKERS:
- none
ROLE_SUMMARY_END

Do not include next_role.
Do not include ready_for_next_role.
