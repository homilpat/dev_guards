# dev-guard 진행 기록

기준일: 2026-09-15 (Asia/Seoul)

## 2026-09-15: Potpie → 자체 recall 전환

- `recall.py`: 기록을 SQLite에 저장하고 코사인으로 순위를 매긴 뒤 기준값·간격 필터를 적용한다. 규칙 네 가지를 추가했다.
  - 기준(anchor) 파일 hash가 바뀌면 그 기록을 제외한다(현재 소스 우선).
  - 기록 만료를 지원한다.
  - 비밀값이 든 기록은 거부한다.
  - 임베딩 모델이 다르면 0점 처리하지 않고 `skipped.model_mismatch`로 보고한다.
- `embedder.py`: BGE-M3를 상주시키는 서버. loopback만 쓰고 실행마다 새 토큰을 만들며, 요청 크기를 제한하고 요청 문장은 로그에 남기지 않는다. hook에서 자동으로 기동한다(Windows에서는 job object 이탈 후 detached 실행).
- CLI `recall add|list|invalidate|search|server-start|server-stop|server`. `context.py`는 recall과 Potpie 결과를 함께 다루고, 서버 기동 중에는 주입하지 않는다. 테스트 154 passed.
- 비교(질의 38개: 튜닝 10 + 검증 28, 정답은 점수 보기 전에 작성):
  - 검증 세트: 정답만 주입 16/20(Potpie 14/20), 무관 질의 무주입 8/8(동일), 1위 적중 18/20(동일).
  - Potpie식 card 텍스트를 넣은 결과는 Potpie와 모든 지표가 같다.
  - 우회 표현의 Local-Only 위반 질의("GPT-4 써도 돼?" 등)는 두 방식 모두 못 막는다. guard 규칙 영역이다.
- 3차원 축소 검토(사용자 질문): 비교 대상 56개에서 PCA 3차원 1위 적중 19%, 206개 이상에서 0%. 1024차원 원본은 93%/85%였다. 검색에는 원본 차원을 쓴다.
- 전환 확인: Code_Agent hook 명령 그대로 실행했다.
  - 관련 질의: 0.29초에 기록 주입. 무관 질의: 주입 없음.
  - 서버를 끄고 hook 실행: 0.14초에 조용히 반환하고 서버를 띄웠다. 서버는 hook 종료 후에도 살아 있었고 7.5초 뒤 준비됐다.
- 기록 이전: Potpie 6건을 옮겼다. 테스트 통과 개수를 못박은 문장("expected 131 passed")은 개수가 계속 바뀌므로 개수를 뺐다. ruff 기록은 `code-agent/pyproject.toml`을 기준 파일로 연결했다.

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
- **Claude Code 실제 세션 확인(16:43):** Code_Agent에서 `claude -p`(Claude Code 2.1.270, sonnet, Read·Edit만 허용)로 `code-agent/src/kh_agent/__init__.py`에 주석 한 줄 추가를 시켰다. SessionStart가 기준 스냅샷(소스 69개)을 만들고, Stop이 자동 호출되어 `code-agent-pytest`(131 passed/5 skipped, 2.2초, 62MB)와 `code-agent-ruff`를 실행해 `PASS`. scope 밖인 `evals-pytest`는 실행되지 않았다. 차단·확인 판정이 없어 감사 로그는 비어 있다. 테스트 주석은 되돌렸다.
- **Codex:** 사용자가 `/hooks`에서 4개 hook을 신뢰했다(`~/.codex/config.toml`의 `hooks.state`에 해시 기록, 16:41). 신뢰를 누른 세션은 이미 시작된 상태라 SessionStart가 돌지 않았고, 사용량 한도로 실제 작업 호출은 아직 확인하지 못했다.
- **Claude Code 실제 block 경로 확인(16:58, 16:59):** `claude -p`로 같은 파일에 쓰이지 않는 `import os` 추가를 시켰다. 수정 도구 검사(PreToolUse)는 통과하고 Stop에서 `code-agent-ruff` F401로 FAIL → `decision: block`이 에이전트에 전달됐다.
  - 1차(프롬프트에 "정확히 이 줄만, 다른 것 수정 금지" 조건 포함): 에이전트가 지시와 충돌한다며 사용자에게 선택지를 묻기만 했다. block 3회 후 `RETRY_LIMIT`으로 세션 중단, 작업 미완료로 끝남. 한도 동작 확인.
  - 2차(조건 없는 프롬프트): block 2회까지는 묻기만 하다가 3번째 block 후 스스로 import를 되돌렸고, 다음 Stop은 소스가 기준과 같아 `UNCHANGED`로 정상 종료. `# noqa`로 검사를 약화하지 않았다.
  - 비용: 회당 약 $0.11~0.12(sonnet). 테스트 변경과 합성 실패 기억은 삭제했다.
- 실사용에서 드러난 개선점:
  1. 소스가 그대로인데 Stop이 반복되면 check를 다시 실행하고 시도 횟수만 소모한다(2·3차 시도가 같은 해시). 같은 해시의 이전 FAIL은 재실행 없이 돌려주는 편이 낫다.
  2. block 사유 JSON에 통과한 check의 출력까지 들어가 길다. 실패한 check 출력만 넣는다.
  3. 사유 문구가 "고쳐라"뿐이라 에이전트가 되묻기로 시도를 쓴다. "통과시킬 수 없으면 변경을 되돌리거나 미완료로 보고" 같은 선택지를 사유에 명시한다.
  4. Claude Code가 block 때 "Stop hook error occurred" 알림을 띄웠다. 동작은 정상이었고 원인은 확인하지 않았다.
- **개선 반영(`ef4a67b`):**
  - 같은 소스 해시의 확정적 FAIL(모든 check가 PASS/FAIL, 검사 중 소스 변경 없음)은 재실행하지 않고 이전 결과를 돌려준다(`rerun: false`). 시도 횟수는 계속 센다. TIMEOUT 등 일시적 실패는 다시 실행한다.
  - block 사유에는 통과하지 못한 check의 출력만 넣는다(최대 4000자).
  - 사유 문구에 "통과시킬 수 없으면 원인 변경을 되돌리거나 미완료로 보고, 파일 변경 없이 턴을 끝내면 실패 시도로 센다"를 넣었다.
  - 추가 결함 수정: `NOT_INITIALIZED`(SessionStart 없이 Stop, 예: 세션 도중 hook 신뢰)와 `CONFIG_CHANGED`는 에이전트가 해소할 수 없고 시도도 세지 않아 block이 무한 반복될 수 있었다. 이제 block하지 않고 "검사 안 됨, 미검증" 경고만 낸다.
  - 테스트 4개 추가(132 passed). 이 중 3개는 이전 코드에서 실패함을 확인했고, 일시적 실패 재실행 테스트는 과도한 재사용을 막는 보호 테스트다.
- **개선 후 실측(17:16, 같은 조건 없는 프롬프트):** block 1회 후 에이전트가 바로 되돌리고 "Task incomplete"로 보고, 다음 Stop `UNCHANGED`. 개선 전 대비 block 3회→1회, 25초(전 39초), $0.06(전 $0.12), 사유 878자로 실패한 ruff 출력만 포함. 표본 1회라 경향 확인 수준이다.

## Potpie 문맥 연결 (2026-09-14 밤, 실측 완료)

### 확인한 Potpie 동작 (소스·실행 확인)

- Potpie는 코드 인덱서가 아니다. 스캐너는 삭제됐고 `source add`는 등록만 한다. 에이전트·사람이 기록한 결정·버그 패턴·절차를 그래프에 저장하고 꺼내는 "프로젝트 기억"이다.
- CLI `record`는 `--type/--summary/--scope`만 받는다. `fix`·`decision`·`bug_pattern`·`preference`·`verification`은 추가 필드가 필요해 CLI로는 거절되고, `workflow`·`runbook_note`·`integration_note`·`investigation` 등 자유 형식만 기록된다. 자유 형식은 검색 시 `docs` include로만 나온다.
- `embedded` 백엔드는 `CONTEXT_ENGINE_HOME/graph.json`에 저장되어 프로세스 간 유지된다. 기본 임베더는 해싱 임베더(모델 다운로드 없음), 서버 측 LLM reconciliation은 기본 꺼짐. 기록·검색에 LLM이 필요 없다.
- 검색은 범위 안의 기록을 관련 없는 질의에도 모두 약 0.52점으로 돌려준다. 실제 관련성은 `properties.semantic_similarity`로만 구분된다. 호출당 약 1초.

### dev-guard 수정 (`a5bc78a`)

- 기존 `context.py`는 `--include` 없이 호출해 자유 형식 기록을 못 가져왔고, `execution.run`이 출력 끝 8KB만 남겨 검색 JSON이 잘렸다. 비밀값 가리기를 JSON 원문에 적용하면 따옴표가 깨질 수 있었다.
- `--include docs,decisions,prior_bugs,coding_preferences` 전달, stderr가 앞에 섞여도 JSON 파싱, `semantic_similarity >= min_similarity`(기본 0.3) 항목만 사실·유사도·출처로 요약해 상위 5개, 넣을 것이 없으면 주입하지 않음, Potpie 실행 파일 절대 경로 검증. `execution.run`에 `keep_bytes`·`redact` 옵션.
- 테스트 3개 추가(135 passed), 이전 코드에서 3개 모두 실패 확인.

### 설정과 기록

- `workflows.json`에 `potpie` 추가: `_integration_sources/potpie-env` 실행 파일, pot `pot_61fbb33f277e`(code-agent-devguard), `runtime/potpie` home, embedded·in_process·local 임베더·텔레메트리 끔, 외부 통신 시도가 실패하도록 `HTTP(S)_PROXY=127.0.0.1:9`(localhost 제외).
- 이 세션에서 검증된 사실 6건을 `scope repo:Code_Agent`로 기록: basetemp 필요, 테스트 실행 환경, 옛 사본 수정 금지, 쓰이지 않는 import는 noqa 대신 제거, Local-Only 유지, 정확한 테스트 실행 명령.

### 검색 품질 실측 (기록 5건 기준)

| 질의 | 결과 |
|---|---|
| 무관한 영어 질의(Next.js 스타일, docker 포트) | 모두 0.16 미만, 주입 없음 |
| "pytest permission error on this machine" | 해당 기록 1건만 0.348로 통과 |
| "run the code-agent tests" | 5건 모두 통과(관련 없는 Local-Only 기록 포함) |
| "can the code agent call the OpenAI API" | Local-Only 기록(0.30)보다 무관한 venv 기록(0.54)이 높음 |
| 한국어 "코드 에이전트 테스트 돌려줘" | 전부 0.000, 주입 없음 |

해싱 임베더는 단어 겹침 수준이라 정밀도가 낮고, **영어 기록은 한국어 프롬프트로 찾을 수 없다.**

### on/off 실측 (Claude Code `claude -p`, sonnet, 같은 영어 프롬프트 "code-agent 테스트를 돌리고 개수 보고, 설치·수정 금지")

| | 결과 | 턴 | 시간 | 비용 |
|---|---|---|---|---|
| Potpie 끔 (`DEV_GUARD_HOME` 사본에서 potpie 제거) | 실패: "패키지 설치 없이는 실행 불가"로 보고 | 18 | 174초 | $0.22 |
| Potpie 켬, 기록 5건(경로 없는 모호한 실행 기록) | 성공(131/0/5). `.venv-runtime`을 찾아 디스크 전체를 뒤지고 `~/.dev-guard/workflows.json`과 다른 프로젝트의 Claude 메모리까지 읽어 명령을 알아냄 | 14 | 164초 | $0.20 |
| Potpie 켬, 정확한 절대 경로 명령 기록 1건 추가 | 성공(131/0/5). 기록된 명령을 바로 실행 | 4 | 15초 | $0.07 |

- 문맥 주입은 stream에 hook 응답 이벤트로 나오지 않았다. 켠 쪽이 Potpie 기록에만 있는 `.venv-runtime` 이름을 두 번째 명령에서 찾기 시작한 것으로 주입을 확인했다.
- 결론: 효과는 크지만 **기록 품질(절대 경로·정확한 명령)에 좌우된다.** 각 조건 1회라 경향 확인 수준이다.
- 모호한 기록일 때 에이전트가 관련 설정 파일과 다른 프로젝트 메모리를 탐색했다. 민감 파일 규칙에는 걸리지 않지만 탐색 범위가 넓어진다.

## 다음 단계 (추천 순서)

1. **실제 세션 확인 마무리:** Codex 새 세션에서 작은 수정으로 Stop 게이트 호출·block 확인(신뢰는 완료). 1~2주 오탐·지연 수집.
2. **Potpie 후속:** 한국어 프롬프트 대응(한·영 병기 기록 또는 다국어 임베딩 모델, 후자는 최초 모델 다운로드 필요), 기록 작성 규칙(절대 경로·정확한 명령·검증 날짜), Stop 게이트 실패·해결을 자동으로 기록할지 결정, 표본을 늘린 on/off 재측정.
3. **Serena:** 읽기 전용 프로젝트 등록과 호출처 조회 연결.
4. **Ponytail:** 규칙 파일 경로·해시 설정, 변경량 한도 값 조정.
5. **언어 확장:** TS/JS(npm test, V8 profile), Java(gradle/mvn test, JFR) check·benchmark 템플릿.
6. **최적화 루프:** 후보 생성 → 게이트 PASS → benchmark `IMPROVED`만 채택.
