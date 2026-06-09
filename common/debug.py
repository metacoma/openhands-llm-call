"""Shared debug helper for openhands_llm and mcp_agent.

Environment variable: OPENHANDS_LLM_CALL_DEBUG
Truthy values (case-insensitive): 1, true, yes, on, debug
"""

import logging
import os

_TRUTHY_VALUES = {"1", "true", "yes", "on", "debug"}

# Fields that must never appear in debug logs
_SENSITIVE_KEYS = frozenset({
    "api_key", "token", "authorization", "password", "secret",
    "apikey", "auth", "credential",
})


def is_debug_enabled() -> bool:
    """Return True if OPENHANDS_LLM_CALL_DEBUG is set to a truthy value."""
    return os.getenv("OPENHANDS_LLM_CALL_DEBUG", "").lower() in _TRUTHY_VALUES


def configure_logging(service_name: str) -> None:
    """Configure logging for *service_name* when debug is enabled.

    Sets the logger level to DEBUG and ensures a StreamHandler exists.
    Does NOT log secrets, tokens, or full environment dumps.
    """
    if not is_debug_enabled():
        return
    logger = logging.getLogger(service_name)
    if logger.handlers:
        for h in logger.handlers:
            h.setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


def debug_log(logger, message: str, **fields) -> None:
    """Log a structured debug message.

    Parameters
    ----------
    logger : logging.Logger
    message : str — short label for the event
    fields : dict — additional key-value pairs (logged as key=value)
    """
    if not is_debug_enabled():
        return
    parts = [message]
    for k, v in fields.items():
        # Never log secrets
        if k.lower() in _SENSITIVE_KEYS:
            parts.append(f"{k}=***REDACTED***")
        else:
            val = str(v)
            if len(val) > 500:
                val = val[:500] + "...[truncated]"
            parts.append(f"{k}={val!r}")
    logger.debug(" ".join(parts))
