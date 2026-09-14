"""Secret detection on text an agent is about to write. Reports kinds, never the value."""

import re

# High-confidence formats block outright (DG-SEC-002).
HIGH_CONFIDENCE = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "openai-key": re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
}

# Generic assignments are common in test fixtures, so they only ask (DG-SEC-004).
ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
    r"\s*[=:]\s*[\"'][^\"'\r\n]{4,}[\"']"
)


def new_high_confidence(added: str, removed: str) -> list[str]:
    """Kinds that occur more often after the change than before it."""
    return [
        kind
        for kind, pattern in HIGH_CONFIDENCE.items()
        if len(pattern.findall(added)) > len(pattern.findall(removed))
    ]


def new_assignment(added: str, removed: str) -> bool:
    return len(ASSIGNMENT.findall(added)) > len(ASSIGNMENT.findall(removed))
