# Internal Legacy API Documentation

**DO NOT EXPOSE TO LLM. DO NOT INCLUDE IN MCP TOOL DISCOVERY. DO NOT INCLUDE IN Head-of-IT prompt.**

This document describes the internal legacy API that is hidden from public MCP discovery.
These tools are available for debugging and backward compatibility only.

## Legacy Tools (not exposed to LLM)

The following functions exist in `mcp_agent/server.py` but are **NOT** decorated with `@MCP.tool()`:

| Function | Purpose |
|---|---|
| `role_start_impl` | Legacy role-start implementation (internal) |
| `role_wait_impl` | Legacy server-side polling (internal) |
| `role_status` | Single-shot diagnostic status check (internal) |
| `role_result` | Get result of a completed role (internal) |
| `artifact_get` | Read artifact content (debug only) |
| `artifact_list_impl` | List artifacts for a given run (internal) |
| `_internal_role_start` | Legacy role start (deprecated, hidden) |
| `_internal_role_wait` | Legacy wait (deprecated, hidden) |
| `_internal_role_result` | Legacy result (deprecated, hidden) |

## Why Hidden

These tools were part of older API versions that used:
- Nested `{"text": "..."}` payload format (confusing for LLM)
- `metadata`/`input_artifacts` dict-based artifact routing (complex)
- Multiple polling tools (`role_start`, `role_status`, `role_result`) instead of unified `role_wait`
- `artifact_get` for reading artifact content (should not be exposed to LLM)

The public API was simplified to exactly three tools: `role_list`, `role_call`, `role_wait`.

## Migration Notes

If you have existing clients using these legacy tools:
1. Migrate to the public API (`role_list`, `role_call`, `role_wait`).
2. Use flat scalar fields instead of nested dicts.
3. Pass artifact IDs, not artifact content.
4. Use `role_wait` for polling instead of `role_status`/`role_result`.

These legacy tools may be removed in a future major version.
