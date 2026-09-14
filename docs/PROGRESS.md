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

## 1.5단계: Code_Agent 프로젝트 파일럿 연결 (완료, 실사용 확인 전)

- 커밋: `32758b8`(2단계 코드와 함께). Code_Agent 쪽 `.gitignore`는 `homilpat/Code_Agent` `f61b038`.
- 기존 `.venv`는 이 PC에 없는 Python 3.14를 가리켜 실행 불가. conda Python 3.11로 `.venv-runtime`을 만들었다. 이후 Codex 세션에서 `psutil pytest ruff jedi typer PyYAML`을 이 환경에 설치했다.
- `src/devguard/launcher.py`: 절대 경로 Python + `-I`로 실행해 프로젝트의 동명 모듈이나 `PYTHONPATH`가 검사 코드를 바꾸지 못하게 했다.
- CLI 서브커맨드와 `check --command` 이름 충돌 수정. Windows PowerShell이 hook의 exit 2를 1로 바꾸는 문제는 `commandWindows`에서 `exit $LASTEXITCODE`로 보존.
- Code_Agent에만 `.claude/settings.local.json`, `.codex/hooks.json`, `.codex/config.toml` 설치(PC별 경로라 git 제외). 전역 설정 변경 없음. 등록된 hook은 PreToolUse, UserPromptSubmit뿐이다.
- 검증: 당시 119 passed, 실제 셸로 설정 명령을 모의 입력 호출한 10개 사례 통과.
- **미완료:** Codex CLI `/hooks`에서 프로젝트 hook 신뢰, 실제 모델 세션에서 hook 자동 호출 확인.

## 2단계: 작업 흐름 게이트·측정·문맥 (코드 반영, 실사용 연결 전)

사용자 요청("Potpie·Ponytail·Serena·Stop 게이트·측정·실패 기억 연결")으로 2026-09-14 15:46~16:11 Codex 세션에서 작성했다. 작업은 `knowledge_hub_v1.10_FINAL_FREEZE_v7/_devguard_session` 사본에서 했고, 사용량 한도로 세션이 끊긴 뒤 Claude Code 세션에서 이 레포로 옮겨 `32758b8`로 커밋했다.

| 파일 | 내용 |
|---|---|
| `workflow.py` | 신뢰 설정 `~/.dev-guard/workflows.json`(프로젝트 밖에만 허용) 로드. SessionStart에 소스 스냅샷 저장, Stop에 변경 파일 수·순증 줄 수·분기 증가(Ponytail식), 테스트 삭제·skip, 의존성 manifest 변경을 검사하고 등록된 check를 실행. 결과는 소스 해시에 묶여 코드가 바뀌면 이전 PASS를 인정하지 않음. check 도중 소스 변경은 FAIL. 재시도 한도(기본 3) 초과 시 `RETRY_LIMIT`로 중단. 실패 기억은 분류와 해시만 저장(원문 출력 없음). Ponytail 규칙 파일은 sha256 고정 후 SessionStart 문맥에 주입 |
| `execution.py` | psutil로 프로세스 트리 RSS·CPU를 20ms 간격 측정, 시간·메모리·출력 한도, 종료 시 자식 프로세스 정리, 비밀값 형식 출력 가림, 토큰류 환경변수 제거 |
| `benchmark.py` | 등록된 작업만 워밍업 1회 + 3~9회 반복 측정(중앙값). 기준값은 덮어쓰지 않음. 최신 게이트가 같은 소스 해시로 PASS일 때만 비교, 대상 지표 5% 이상 개선 + 다른 지표 10% 이내 악화일 때 `IMPROVED` |
| `context.py` | UserPromptSubmit에서 Potpie CLI 검색 결과와 최근 실패 기억을 "신뢰할 수 없는 근거"로 표시해 주입 |
| `cli.py` | `dev-guard workflow start/check/status/context/memory`, `dev-guard benchmark <profile> [--baseline]`. SessionStart/Stop hook 라우팅. Stop 중 내부 오류는 성공으로 보고하지 않고 중단 |

- 옮기면서 수정: 검사 중 강제 종료로 남은 `running.lock`이 이후 모든 Stop을 막던 문제(잠금에 PID 기록, 죽은 PID면 회수). 수정 전 코드에서 실패하는 회귀 테스트 추가. `context.py` 줄 길이 lint.
- 검증: `.venv-runtime` + `PYTHONPATH=src`로 **129 passed**, `ruff check`·`ruff format --check`·`git diff --check` 통과. 기존 `.venv`에는 psutil이 없어 workflow 테스트가 실패한다.

### 외부 도구 준비 상태 (요구사항 폴더 `_integration_sources/`, 이 레포 밖)

- Potpie(`potpie-ai/potpie` `6373454`): `potpie-env`에 설치. `CONTEXT_ENGINE_BACKEND=embedded`, `HOST_MODE=in_process`, `EMBEDDER=local`, `POTPIE_TELEMETRY_DISABLED=1`로 pot `code-agent-devguard` 생성. **소스 미등록(`sources: {}`)이라 검색 결과 없음.** `context.py`의 `--json search --pot ... -- <질의>` 호출 형식은 실제 CLI와 대조하지 않았다.
- Serena(`oraios/serena` `403ad0a`): `serena-env`에 설치, `--help`만 확인. 프로젝트 등록·언어 서버(jedi) 설정과 dev-guard 연결 없음. SessionStart 문구에 사용 안내만 있다.
- Ponytail(`DietrichGebert/ponytail` `356918e`): 소스만 받음. `workflows.json`의 `ponytail.path/sha256` 설정 없음.
- Local-Only 관점의 egress 차단은 검증하지 않았다.

### 알려진 한계

- `workflows.json`이 없으면 게이트는 아무것도 하지 않는다(`NOT_CONFIGURED`). 아직 어느 프로젝트에도 만들지 않았다.
- 복잡도·테스트 이름·skip 탐지는 Python AST와 정규식 기반이다. TS/JS·Java는 변경량과 skip 패턴만 본다.
- 메모리는 샘플링 RSS라 짧은 순간 peak를 놓칠 수 있다. Python heap·JVM·V8 내부 측정이 아니다.
- 잠금 PID 재사용 시 살아 있는 것으로 오판할 수 있다(이 경우 사용자가 잠금 파일을 지운다).

### 실사용 연결 (2026-09-14 저녁, 모의 호출까지 완료)

- `~/.dev-guard/workflows.json`(PC 로컬, 커밋 안 함) 작성. Code_Agent 기준:
  - check 3개: `code-agent-pytest`(scope `code-agent/`, 300초, 2GB), `code-agent-ruff`(scope `code-agent/`), `evals-pytest`(scope `evals/`). 실행 Python은 Code_Agent에 venv가 없어 `.venv-runtime`을 쓰고 `PYTHONPATH=code-agent/src`를 준다. basetemp는 `~/.dev-guard/tmp/`.
  - `exclude`: `backend-spring/`, `frontend-nextjs/`, `rag-fastapi/`, `docker/`, `docker-compose.yml`, `게시물/`. 검사 명령이 없어 게이트 대상에서 뺐다(이 영역 변경은 검사되지 않는다).
  - 한도: 변경 30파일, 순증 600줄, 파일당 분기 증가 20, manifest 변경 불허, 재시도 3회.
- Code_Agent `.claude/settings.local.json`, `.codex/hooks.json`에 SessionStart(60초)·Stop(600초) 추가. Codex 공식 문서에서 두 이벤트와 Stop의 `decision: block`/`continue: false` 출력을 확인했다.
- 사전 확인: dev-guard 실행기로 code-agent 131 passed/5 skipped(2.3초, peak RSS 61MB), evals 9 passed, code-agent ruff 통과.
- launcher로 실제 hook 입력을 흉내 낸 흐름 확인: SessionStart 문맥 주입 → 변경 없음 `UNCHANGED` → code-agent 임시 파일 추가 시 검사 실행 `PASS` → 테스트 함수 제거 시 `TEST_REMOVAL`로 block → 복원 후 `UNCHANGED`. Claude Code·Codex 어댑터 둘 다. 임시 파일과 모의 상태·실패 기억은 삭제했고 Code_Agent 작업 트리는 깨끗하다.
- SessionStart 문구에서 연결되지 않은 Serena 안내를 뺐다.
- **미확인:** 실제 Claude Code·Codex 세션에서의 자동 호출. Codex는 `/hooks`에서 새 hook 신뢰가 필요하다.

## 다음 단계 (추천 순서)

1. **실제 세션 확인:** Code_Agent에서 Codex `/hooks` 신뢰 → 작은 수정 작업으로 Stop 게이트 호출·차단·재시도 한도 확인. Claude Code는 새 세션에서 같은 확인. 1~2주 오탐·지연 수집.
2. **Potpie:** Code_Agent 소스 등록, 검색 명령 형식 확인, on/off로 문맥 주입 효과 측정.
3. **Serena:** 읽기 전용 프로젝트 등록과 호출처 조회 연결.
4. **Ponytail:** 규칙 파일 경로·해시 설정, 변경량 한도 값 조정.
5. **언어 확장:** TS/JS(npm test, V8 profile), Java(gradle/mvn test, JFR) check·benchmark 템플릿.
6. **최적화 루프:** 후보 생성 → 게이트 PASS → benchmark `IMPROVED`만 채택.
