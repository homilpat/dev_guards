# dev-guard

Claude Code와 Codex CLI로 개발할 때 **결정적 코드로 강제하는 가드레일**입니다. 프롬프트로 부탁하는 규칙이 아니라, 에이전트가 파일을 읽고 고치고 명령을 실행하기 **직전에** hook이 검사해 차단하거나 사용자 확인을 요구합니다.

규칙은 Knowledge Hub v1.10 요구사항(M06 Sandbox·Command Policy, M08 Local-Only Security 등)과 code-agent 구현에서 가져왔습니다. 대화형 개발에 맞게 일부 규칙은 "차단" 대신 "확인"으로 조정했습니다.

## 가드레일

`dev-guard rules`로 전체 목록과 근거 문서를 볼 수 있습니다.

| 규칙 | 기본 | 내용 | 근거 |
|---|---|---|---|
| DG-SEC-001 | 차단 | 민감 파일(`.env*`, `*.pem/.key/.p12/.pfx/.keystore/.jks`, `credentials*`, `secret(s)/` 등) 읽기·수정·셸 사용 | M08 §5, M02-FR-01 |
| DG-SEC-002 | 차단 | 개인키·AWS·GitHub·Anthropic·OpenAI·Slack·Google 키 추가 | M06 §5, M08 §5 |
| DG-SEC-003 | 차단 | `local-only`로 지정한 저장소에서 클라우드 에이전트 사용 | M08 §2 |
| DG-SEC-004 | 확인 | `password = "..."` 같은 비밀값 의심 할당 추가 | M06 §5 |
| DG-CTL-001 | 차단 | `.git/` 메타데이터 수정 | M06 §5 |
| DG-CTL-002 | 차단 | dev-guard 정책, `.claude/settings*`, `.claude/hooks/`, `.codex/` 수정 | M06 §4 |
| DG-CTL-003 | 확인 | `AGENTS.md`, `CLAUDE.md` 수정 | M06 §4 |
| DG-CMD-001 | 차단 | `curl`, `wget`, `ssh`, `scp`, `nc`, `Invoke-WebRequest` 등 네트워크 명령 | M06 §4, M08 §3 |
| DG-CMD-002 | 차단 | `sudo`, `su`, `mount`, `mkfs`, `diskpart`, `shutdown` 등 | M06 §4, M08 §8 |
| DG-CMD-003 | 차단 | `rm -rf`, `Remove-Item -Recurse -Force`, `rd /s /q` | M06 §4, M08 §8 |
| DG-CMD-004 | 차단 | Docker socket 마운트, `--privileged` | M06 §2, M08 §8 |
| DG-CMD-005 | 확인 | `pip/npm/pnpm/yarn/uv/poetry install·add`, `npx` | M06 §3 |
| DG-CMD-006 | 확인 | `git push`, `git remote add/set-url`, `gh pr merge` 등 | M08 §5, M11 |
| DG-CMD-007 | 확인 | `git reset --hard`, `git clean -f`, `git checkout -- .` | M06 §5 |
| DG-CMD-008 | 차단 | `apt/brew/choco/winget/scoop install` 등 호스트 패키지 변경 | M06 §4 |
| DG-CMD-009 | 확인 | 인코딩된 PowerShell, `eval`/`iex`, 과도한 중첩 | M06 §4 |
| DG-TST-001 | 차단 | 테스트 skip/only/`@Disabled` 추가 (Python·JS/TS·Java) | 모델 금지 사항, M07 |
| DG-TST-002 | 확인 | 테스트 파일·테스트 함수 삭제 | Test Selection Integrity |

### 원본 요구사항에서 조정한 부분

- **M08 "외부 LLM API 금지"**: 이 도구는 Claude/Codex 위에서 동작하므로 그대로 적용할 수 없습니다. 대신 민감 파일은 에이전트에 들어가지 않게 막고, 반출이 금지된 저장소는 `local-only`로 지정해 클라우드 에이전트 사용 자체를 막습니다.
- **M06 "명백한 secret 추가 차단"**: 형식이 확실한 키만 차단하고, 테스트 픽스처에 흔한 일반 할당은 확인으로 둡니다.
- **M06 "공개 레지스트리 금지"·"raw shell 금지"**: 개발 중에는 필요한 경우가 많아 확인으로 둡니다.
- **Codex**: hook이 확인 창을 띄울 수 없어 "확인" 규칙도 차단합니다. 대신 `dev-guard codex-rules`가 만드는 execpolicy 규칙이 Codex 자체의 승인 프롬프트를 띄웁니다.

## 정책 위치와 신뢰 경계

권한을 바꾸는 정책은 **저장소 밖**에만 둡니다(M06 §4).

- `~/.dev-guard/policy.toml` (또는 `$DEV_GUARD_HOME/policy.toml`): 사용자 정책. 규칙 결정 변경, 민감/허용 경로, local-only 경로.

  ```toml
  local_only_roots = ["C:/work/customer-repo"]
  sensitive_paths = ["data/*.csv"]
  allow_paths = [".env.example"]

  [overrides]
  "DG-CMD-005" = "allow"
  ```

- 저장소의 `.dev-guard.toml`: **제한을 추가만** 할 수 있습니다. 다른 필드가 있으면 오류로 차단합니다.

  ```toml
  classification = "local-only"   # 또는 "normal"
  sensitive_paths = ["fixtures/customer/*"]
  ```

- 정책 파일이 잘못됐거나 hook이 오류를 내면 **차단**합니다(fail-closed). 긴급 시 환경변수 `DEV_GUARD_FAIL_OPEN=1`로만 해제합니다.
- 확인·차단 결정은 `~/.dev-guard/audit.jsonl`에 남습니다. 명령 원문과 파일 내용은 저장하지 않고 명령은 SHA-256만 기록합니다(M08 §6).

## 설치

Python 3.11 이상이 필요하고 외부 의존성은 없습니다. hook은 `python -m devguard`를 실행하므로, 에이전트가 사용하는 `python`에 설치합니다.

```powershell
python -m pip install -e C:\path\to\dev-guard
dev-guard rules
```

### Claude Code

```powershell
claude plugin marketplace add C:\path\to\dev-guard
claude plugin install dev-guard@dev-guard
```

플러그인은 PreToolUse(Read, Grep, Write, Edit, MultiEdit, NotebookEdit, Bash, PowerShell)와 UserPromptSubmit hook, 그리고 차단 시 행동 지침 skill을 설치합니다. Bash 하위 프로세스가 직접 여는 파일은 hook이 볼 수 없으므로 Claude Code의 `permissions.deny`와 sandbox를 함께 쓰는 것을 권장합니다.

### Codex CLI

```powershell
dev-guard codex-rules > $HOME\.codex\rules\dev-guard.rules
Copy-Item adapters\codex\hooks.json $HOME\.codex\hooks.json   # 기존 hooks.json이 있으면 병합
```

`adapters/codex/AGENTS.dev-guard.md` 내용을 `~/.codex/AGENTS.md`에 추가합니다. 새 hook은 Codex의 `/hooks`에서 신뢰 승인이 필요합니다.

## 개발

```powershell
python -m pip install -e ".[dev]"
python -m pytest
ruff check . ; ruff format --check .
dev-guard check --command "curl https://example.com"
```

## 한계

- 셸 명령은 토큰 분석이라 `python -c "..."`처럼 다른 인터프리터 안에서 하는 동작은 보지 못합니다. 샌드박스와 네트워크 차단을 대체하지 않습니다.
- 비밀값 탐지는 형식 기반이며 모든 비밀값을 찾는다고 보장하지 않습니다.
- 현재 단계는 차단·확인 가드레일입니다. 테스트·메모리·성능 측정 게이트와 최적화 루프는 다음 단계입니다.
