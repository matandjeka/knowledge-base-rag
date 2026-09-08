"""PII and secret redaction for structured logs.

Inputs: log records passing through the root handler.
Outputs: the same records with e-mail addresses, bearer tokens, key/value secrets, and long
opaque strings masked in the rendered message and in any non-allowlisted string ``extra``.
Side effects: none; the filter never drops a record.
"""

import logging
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_LONG_OPAQUE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie)\b(\s*[=:]\s*)[^\s;,]+"
)
# Identifiers that look opaque but are not secrets: UUIDs and bare hex ids / content hashes.
_ID_SHAPED = re.compile(
    r"(?i)^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32,64})$"
)
_REDACTED = "[redacted]"

# Structured fields that are known-safe to emit verbatim.
_ALLOWED_FIELDS = frozenset(
    {
        "trace_id",
        "workspace_id",
        "org_id",
        "user_id",
        "actor_user_id",
        "action",
        "retrieval_mode",
        "routing_intent",
        "selected_retrievers",
        "routing_confidence",
        "routing_fallback",
        "provider",
        "model_name",
        "usage",
        "status_code",
    }
)
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}


def _mask_opaque(match: "re.Match[str]") -> str:
    token = match.group()
    return token if _ID_SHAPED.match(token) else _REDACTED


def redact(value: str) -> str:
    """Mask bearer tokens, key/value secrets, e-mails, and long opaque strings.

    UUIDs and bare hex identifiers / content hashes are left intact — they are not secrets
    and masking them would blind operational logs.
    """
    value = _BEARER.sub(f"bearer {_REDACTED}", value)
    value = _EMAIL.sub(_REDACTED, value)
    value = _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", value)
    value = _LONG_OPAQUE.sub(_mask_opaque, value)
    return value


class RedactionFilter(logging.Filter):
    """Scrub the rendered message and any unexpected string fields before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        masked = redact(message)
        if masked != message:
            record.msg = masked
            record.args = None
        for key in list(vars(record)):
            if key in _STANDARD_ATTRS or key in _ALLOWED_FIELDS or key.startswith("_"):
                continue
            item = getattr(record, key)
            if isinstance(item, str):
                setattr(record, key, redact(item))
        return True


def install_redaction(handler: logging.Handler) -> None:
    """Attach the redaction filter to a handler when it is not already present."""
    if not any(isinstance(existing, RedactionFilter) for existing in handler.filters):
        handler.addFilter(RedactionFilter())
