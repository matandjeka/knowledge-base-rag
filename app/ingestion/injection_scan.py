"""Heuristic scan for indirect prompt-injection markers in ingested content.

Inputs: normalized document text.
Outputs: a sorted list of finding labels (empty when nothing matched).
Side effects: none. The scan is advisory — callers flag the source, they do not block it.
"""

import re
from collections.abc import Iterable

from app.models import NormalizedDocument

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier)\b"
        ),
        "instruction-override",
    ),
    (
        re.compile(r"(?i)\byou are now\b|\bnew (system prompt|instructions|role)\b"),
        "role-reassignment",
    ),
    (re.compile(r"(?i)<\s*/?\s*(system|assistant|tool)\s*>"), "role-markup"),
    (re.compile(r"(?im)^\s*(system|assistant)\s*:\s"), "role-prefix"),
    (
        re.compile(
            r"(?i)\b(reveal|print|repeat|exfiltrate|leak|send)\b[^.\n]{0,40}"
            r"\b(system prompt|instructions|api key|secret|credentials?)\b"
        ),
        "exfiltration-request",
    ),
    (
        re.compile(r"(?i)```(json)?\s*\{?\s*\"?(tool_call|function_call|tool_calls)\"?"),
        "tool-call-injection",
    ),
)


def scan_text(text: str) -> list[str]:
    """Return the injection-marker labels found in one piece of text."""
    return sorted({label for pattern, label in _PATTERNS if pattern.search(text)})


def scan_documents(documents: Iterable[NormalizedDocument]) -> list[str]:
    """Return the union of injection-marker labels across a document set."""
    findings: set[str] = set()
    for document in documents:
        findings.update(scan_text(document.content))
    return sorted(findings)
