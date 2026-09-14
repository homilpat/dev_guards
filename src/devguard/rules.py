"""Rule catalog: every guardrail, its default decision and the document it comes from.

Sources refer to the Knowledge Hub v1.10 freeze set (M06/M07/M08/M11, module requirements) and
the code-agent implementation. Where a rule is relaxed for interactive development, the title
says "확인" (ASK) instead of "차단" (DENY).
"""

from dataclasses import dataclass

from devguard.model import Decision, Finding


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    default: Decision
    source: str


_ALL = (
    Rule(
        "DG-SEC-001",
        "민감 파일 읽기·수정 차단",
        Decision.DENY,
        "M08 §5 Sensitive File Policy; requirements M02-FR-01; code-agent security/ingestion.py",
    ),
    Rule(
        "DG-SEC-002",
        "확실한 비밀값(개인키·클라우드·토큰 키) 추가 차단",
        Decision.DENY,
        "M06 §5 Pre/Post-Apply Guardrail; M08 §5",
    ),
    Rule("DG-SEC-003", "Local-Only 저장소에서 클라우드 에이전트 차단", Decision.DENY, "M08 §2"),
    Rule(
        "DG-SEC-004",
        "비밀값 의심 할당(password=, api_key= 등) 확인",
        Decision.ASK,
        "M06 §5; code-agent security/ingestion.py",
    ),
    Rule(
        "DG-CTL-001", ".git 관리 메타데이터 수정 차단", Decision.DENY, "M06 §5 Pre-Apply Guardrail"
    ),
    Rule(
        "DG-CTL-002",
        "가드레일·에이전트 권한 설정 수정 차단",
        Decision.DENY,
        "M06 §4 권한 정책 self-authorize 금지",
    ),
    Rule(
        "DG-CTL-003",
        "에이전트 지침 파일(AGENTS.md, CLAUDE.md) 수정 확인",
        Decision.ASK,
        "M06 §4 instruction-like text는 권한이 아님",
    ),
    Rule(
        "DG-CMD-001",
        "외부 네트워크 명령 차단",
        Decision.DENY,
        "M06 §4 기본 차단 후보; M08 §3 Internet Egress 기본 차단",
    ),
    Rule("DG-CMD-002", "권한 상승·시스템 변경 명령 차단", Decision.DENY, "M06 §4; M08 §8"),
    Rule("DG-CMD-003", "재귀 강제 삭제 차단", Decision.DENY, "M06 §4 rm -rf; M08 §8"),
    Rule(
        "DG-CMD-004",
        "Docker socket·privileged 컨테이너 차단",
        Decision.DENY,
        "M06 §2 Sandbox Runtime Hardening; M08 §8",
    ),
    Rule(
        "DG-CMD-005",
        "패키지 설치(공개 레지스트리 접근) 확인",
        Decision.ASK,
        "M06 §3 Public registry download 금지",
    ),
    Rule(
        "DG-CMD-006",
        "원격 저장소 전송(push 등) 확인",
        Decision.ASK,
        "M08 §5 remote-publish; M11 Remote operation 별도 승인",
    ),
    Rule(
        "DG-CMD-007", "작업 내용을 버리는 Git 명령 확인", Decision.ASK, "M06 §5 destructive change"
    ),
    Rule(
        "DG-CMD-008",
        "호스트 패키지 관리자 변경 차단",
        Decision.DENY,
        "M06 §4 host package manager mutation",
    ),
    Rule(
        "DG-CMD-009",
        "분석할 수 없는 명령(인코딩·eval·과도한 중첩) 확인",
        Decision.ASK,
        "M06 §4 raw shell·substitution 기본 차단",
    ),
    Rule(
        "DG-TST-001",
        "테스트 skip·disable·only 추가 차단",
        Decision.DENY,
        "IMPLEMENTATION_PROGRESS 2026-09-14 모델 금지 사항; M07 self-exemption 금지",
    ),
    Rule(
        "DG-TST-002",
        "테스트 파일·테스트 함수 삭제 확인",
        Decision.ASK,
        "IMPLEMENTATION_PROGRESS 2026-09-14 Test Selection Integrity",
    ),
)

RULES = {rule.id: rule for rule in _ALL}


def finding(rule_id: str, message: str, decision: Decision | None = None) -> Finding:
    rule = RULES[rule_id]
    return Finding(rule.id, rule.default if decision is None else decision, message, rule.source)
