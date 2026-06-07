Your previous response was not valid JSON.

Return valid JSON only.
Do not include Markdown.
Do not include code blocks.
Use exactly this schema:
{
  "status": "completed" | "blocked",
  "role": "<role>",
  "summary": "<short factual summary>",
  "primary_artifact_name": "<artifact name>",
  "blocking": true | false,
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | null,
  "action": "PASS" | "BLOCKER" | null,
  "blocking_summary": ["..."]
}

Do not include next_role.
Do not include ready_for_next_role.
