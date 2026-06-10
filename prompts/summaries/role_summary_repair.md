Your previous response could not be parsed.

Return plain structured text only. Do not return JSON. Do not include Markdown code fences.

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
- If there are no blockers, use exactly: `- none`
- Do not include `next_role`.
- Do not include `ready_for_next_role`.
- Put blockers under `BLOCKERS:` as bullet lines (one per line starting with `- `).
