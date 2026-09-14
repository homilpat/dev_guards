"""Test integrity: an agent must not make a failing suite pass by disabling or deleting tests."""

import re

from devguard.model import FileChange, Finding
from devguard.rules import finding

_TEST_PATHS = (
    re.compile(r"(^|/)tests?/", re.IGNORECASE),
    re.compile(r"(^|/)test_[^/]*\.py$", re.IGNORECASE),
    re.compile(r"_test\.py$", re.IGNORECASE),
    re.compile(r"(^|/)conftest\.py$", re.IGNORECASE),
    re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$", re.IGNORECASE),
    re.compile(r"(^|/)__tests__/", re.IGNORECASE),
    re.compile(r"(^|/)src/test/", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9_](Test|Tests|IT)\.java$"),
)

# Python (pytest/unittest), JavaScript/TypeScript (Jest/Vitest/Mocha/Jasmine), Java (JUnit).
SKIP_MARKERS = re.compile(
    r"@pytest\.mark\.(?:skip|skipif|xfail)\b"
    r"|\bpytest\.(?:skip|xfail)\("
    r"|@unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b"
    r"|\b(?:it|test|describe|context|suite)\.(?:skip|only)\("
    r"|\bx(?:it|test|describe)\("
    r"|\bf(?:it|describe)\("
    r"|@(?:Disabled|Ignore)\b"
)
TEST_DEFINITIONS = re.compile(
    r"^\s*(?:async\s+)?def\s+test\w*|\b(?:it|test)\s*\(\s*['\"`]|@(?:Test|ParameterizedTest)\b",
    re.MULTILINE,
)


def is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(pattern.search(normalized) for pattern in _TEST_PATHS)


def check(change: FileChange) -> list[Finding]:
    if not is_test_file(change.path):
        return []
    if change.deleted:
        return [finding("DG-TST-002", f"테스트 파일 삭제: {change.path}")]
    findings = []
    if len(SKIP_MARKERS.findall(change.added)) > len(SKIP_MARKERS.findall(change.removed)):
        findings.append(finding("DG-TST-001", f"테스트 비활성화 표시 추가: {change.path}"))
    if len(TEST_DEFINITIONS.findall(change.removed)) > len(TEST_DEFINITIONS.findall(change.added)):
        findings.append(finding("DG-TST-002", f"테스트 함수 제거: {change.path}"))
    return findings
