"""Agent-independent tool calls and verdicts. Adapters translate to and from these."""

from dataclasses import dataclass
from enum import IntEnum


class Decision(IntEnum):
    """Ordered so that max() yields the most restrictive outcome."""

    ALLOW = 0
    ASK = 1
    DENY = 2


@dataclass(frozen=True)
class FileChange:
    path: str
    # Text replaced or deleted and text introduced; enough to compare before/after counts.
    removed: str = ""
    added: str = ""
    deleted: bool = False


@dataclass(frozen=True)
class ToolCall:
    agent: str
    tool: str
    cwd: str
    reads: tuple[str, ...] = ()
    changes: tuple[FileChange, ...] = ()
    command: str | None = None


@dataclass(frozen=True)
class Finding:
    rule_id: str
    decision: Decision
    message: str
    source: str


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    findings: tuple[Finding, ...] = ()

    @classmethod
    def from_findings(cls, findings: tuple[Finding, ...]) -> "Verdict":
        unique = tuple(dict.fromkeys(findings))
        decision = max((f.decision for f in unique), default=Decision.ALLOW)
        return cls(decision, unique)

    def reason(self) -> str:
        lines = [
            f"[dev-guard] {f.decision.name} {f.rule_id}: {f.message} (근거: {f.source})"
            for f in self.findings
            if f.decision is not Decision.ALLOW
        ]
        if self.decision is Decision.DENY:
            lines.append(
                "가드레일을 우회하지 말고, 필요하면 사용자에게 이유를 설명하고 확인을 받으세요."
            )
        return "\n".join(lines)
