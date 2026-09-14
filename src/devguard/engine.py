"""Evaluate a normalized tool call against every guardrail."""

from pathlib import Path

from devguard import commands, paths, secrets, testguard
from devguard.model import FileChange, Finding, ToolCall, Verdict
from devguard.policy import Policy
from devguard.rules import finding


def evaluate(call: ToolCall, policy: Policy) -> Verdict:
    findings: list[Finding] = []
    if policy.is_local_only(Path(call.cwd)):
        findings.append(finding("DG-SEC-003", f"local-only로 지정된 저장소: {call.cwd}"))
    for path in call.reads:
        if policy.sensitive(path):
            findings.append(finding("DG-SEC-001", f"민감 파일 읽기: {path}"))
    for change in call.changes:
        findings.extend(_change(change, policy))
    if call.command is not None:
        findings.extend(commands.analyze(call.command, policy.sensitive))
    return Verdict.from_findings(tuple(policy.apply(item) for item in findings))


def _change(change: FileChange, policy: Policy) -> list[Finding]:
    findings = []
    if policy.sensitive(change.path):
        findings.append(finding("DG-SEC-001", f"민감 파일 수정: {change.path}"))
    if paths.git_metadata(change.path):
        findings.append(finding("DG-CTL-001", f".git 메타데이터 수정: {change.path}"))
    if paths.guard_config(change.path):
        findings.append(finding("DG-CTL-002", f"가드레일·에이전트 설정 수정: {change.path}"))
    elif paths.instruction_file(change.path):
        findings.append(finding("DG-CTL-003", f"에이전트 지침 파일 수정: {change.path}"))
    kinds = secrets.new_high_confidence(change.added, change.removed)
    if kinds:
        findings.append(finding("DG-SEC-002", f"비밀값 추가({', '.join(kinds)}): {change.path}"))
    elif secrets.new_assignment(change.added, change.removed):
        findings.append(finding("DG-SEC-004", f"비밀값 의심 할당 추가: {change.path}"))
    findings.extend(testguard.check(change))
    return findings
