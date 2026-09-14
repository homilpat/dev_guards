# dev-guard 진행 기록

기준일: 2026-09-14 (Asia/Seoul)

## 배경

Knowledge Hub v1.10(로컬 모델 코드 에이전트, `homilpat/Code_Agent`)을 진행하던 중, 기업은 대부분 계약·자사 클라우드 경유·관리형 정책으로 데이터 보호를 해결하고 로컬 에이전트는 폐쇄망 틈새라는 점을 확인했다. 그래서 이미 성능이 좋은 Claude Code와 Codex CLI 위에 **결정적 가드레일과 품질 검사**를 얹는 개발 도구를 별도 레포로 만들기로 했다. v1.10의 규칙과 부품은 이 도구의 기능으로 재사용한다.

## 결정 사항

| 항목 | 결정 |
|---|---|
| 대상 에이전트 | Claude Code, Codex CLI 동시 지원 |
| 대상 언어 | Python, TypeScript/JavaScript, Java |
| 가드레일 출처 | Knowledge Hub v1.10 요구사항 폴더의 문서와 코드 (M06, M08, 요구사항 민감 파일 목록, code-agent `ingestion.py`·`sandbox/policy.py`, 진행 기록의 모델 금지 사항) |
| 강제 방식 | 프롬프트가 아니라 hook·CLI 검사로 판정 |
| 기능 추가 기준 | 켰을 때와 껐을 때 효과를 측정한 것만 남김 |
| 외부 도구 | Serena, Semgrep, claude-mem, Codeflash 등은 코드를 복사하지 않고 연결만 함 (claude-mem은 AGPL, Codeflash는 BSL이며 코드를 외부 LLM으로 보냄) |
| 라이선스 | 사업화 방향이 정해질 때까지 보류 |

## 1단계: 차단·확인 가드레일 (완료)

- 커밋: `df94c72` 기능, `6bb0ce6` 플러그인 주소 수정, `d3d37b2` 원격 초기 커밋 병합. `main` push 완료.
- 규칙 18개(`dev-guard rules`), 각 규칙에 근거 문서 연결.
- Claude Code: PreToolUse(Read, Grep, Write, Edit, MultiEdit, NotebookEdit, Bash, PowerShell)와 UserPromptSubmit hook, 차단 시 행동 지침 skill, 플러그인과 마켓플레이스 파일.
- Codex: `hooks.json` 템플릿(Bash, apply_patch, UserPromptSubmit), 생성형 execpolicy 규칙(`dev-guard codex-rules`), AGENTS.md 문구.
- 정책: 사용자 정책은 `~/.dev-guard/policy.toml`에만 둔다. 저장소 `.dev-guard.toml`은 제한 추가만 가능하다. hook 오류는 차단(fail-closed)이고, 확인·차단 결정은 명령 원문 없이 감사 로그로 남긴다.

### 원본 요구사항에서 조정한 부분

- M08 "외부 LLM API 금지": 도구 자체가 Claude/Codex 위에서 동작하므로, 민감 파일 차단 + `local-only` 저장소에서 클라우드 에이전트 차단으로 바꿨다.
- 형식이 확실한 비밀값만 차단하고, 테스트 픽스처에 흔한 일반 할당은 확인으로 둔다.
- 패키지 설치, `git push`, 작업 폐기 git 명령, 분석할 수 없는 명령은 개발 중 필요할 수 있어 확인으로 둔다.
- Codex hook은 확인 창을 띄울 수 없어 확인 규칙도 차단하고, 명령은 execpolicy `prompt` 규칙으로 승인 창을 띄운다.
- `.env.example`도 원문 규칙(`.env.*`)대로 민감 파일로 본다. 필요하면 사용자 정책 `allow_paths`로 허용한다.

### 검증

- `pytest`: 112 passed. 생성한 Codex 규칙을 실제 `codex execpolicy check`(codex-cli 0.154.0)에 넣어 forbidden·prompt·허용 결과를 확인하는 테스트 포함.
- `ruff check`, `ruff format --check` 통과.
- `claude plugin validate`(Claude Code 2.1.270)로 플러그인과 마켓플레이스 검증 통과.
- **미검증:** 실제 Claude Code·Codex 세션에 설치해 hook이 호출되는 것. 문서마다 hook 입력 필드 이름이 달라(`content`/`file_contents` 등) 양쪽을 모두 받게 했으므로 실제 세션에서 확인이 필요하다.

### 알려진 한계

- 셸 명령은 토큰 분석이라 `python -c "..."` 같은 인터프리터 내부 동작은 보지 못한다. 샌드박스·네트워크 차단을 대체하지 않는다.
- Claude Code `Grep`이 디렉터리 전체를 검색할 때 민감 파일 내용이 섞이는 경우는 hook만으로 막지 못한다. `permissions.deny`를 함께 쓴다.
- 비밀값 탐지는 형식 기반이다.

## 다음 단계 (추천 순서, 사용자 확정 전)

1. **실사용 테스트:** 전역 설치 없이 Code_Agent 프로젝트에만 적용한다. Claude Code는 `.claude/settings.local.json`, Codex는 `.codex/hooks.json`에 등록하고, 이 레포의 `.venv` Python을 절대 경로로 지정한다. 1~2주 사용하며 오탐·누락을 모은다.
2. **Stop 게이트:** 작업 종료 전에 언어별 테스트 명령(pytest, npm test, mvn/gradle test)을 강제하고, 실패하면 결과를 에이전트에 돌려준다. 무한 반복을 방지한다.
3. **측정:** Python tracemalloc 증가율(입력 10배 → peak 배수), 이후 Java JFR, JS V8 heap/CPU profile.
4. **최적화 루프:** 후보 생성 → 기존 테스트로 동작 동일성 확인 → 벤치마크로 실제 개선만 채택(Codeflash 방식을 로컬에서 구현).
5. **문맥 연결:** Serena(LSP), Potpie, 실패 기억.
