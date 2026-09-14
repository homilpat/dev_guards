# 프로젝트 한정 실사용 파일럿

대상은 Code_Agent 한 곳이다. 전역 Python 패키지, Claude 플러그인,
사용자 `~/.codex` 설정을 변경하지 않고 프로젝트 hook으로 연결한다.

## 실행 환경

기존 `.venv`가 존재해도 기반 Python이 삭제되었으면 실행할 수 없다.
사용 가능한 Python 3.11 이상으로 별도 `.venv-runtime`을 만든다.
기존 환경은 보존하며 dev-guard 런타임에는 외부 패키지가 필요 없다.

```powershell
& '<existing-python.exe>' -m venv --without-pip '<dev-guard>/.venv-runtime'
& '<dev-guard>/.venv-runtime/Scripts/python.exe' -I '<dev-guard>/src/devguard/launcher.py' --version
```

launcher는 자기 위치의 소스만 import한다. 프로젝트의 `devguard.py`,
현재 디렉터리, `PYTHONPATH`에 의해 검사 코드가 바뀌지 않게 `-I`로 실행한다.
hook 명령에는 Python과 launcher의 절대 경로를 사용한다.
dev-guard checkout 자체의 변경 권한까지 격리하는 보안 경계는 아니다.

## 프로젝트 설정

- Claude Code: `<Code_Agent>/.claude/settings.local.json`에
  PreToolUse와 UserPromptSubmit을 등록한다. 민감 파일의 광역 검색 노출을 줄이기
  위해 `permissions.deny`에 Read 경로 패턴도 추가한다.
- Codex: `<Code_Agent>/.codex/hooks.json`에 PreToolUse(`Bash|apply_patch`)와
  UserPromptSubmit을 등록하고 프로젝트 `config.toml`에서 `features.hooks = true`.
- 기존 설정이 있으면 해당 내용을 보존하고 병합한다. PC별 절대 경로가 든 파일은
  로컬 파일로 보관한다.
- Code_Agent는 클라우드 도구로 개발하는 저장소다. 제품 내부의 Local-Only 요구와
  개발 저장소의 `classification = "local-only"`를 혼동하지 않는다.
- Codex에서 `/hooks`를 열어 정확한 hook 정의를 검토하고 신뢰해야 활성화된다.
  설정 파일 생성, stdin 모의 호출 성공, 실제 세션 적용은 서로 다른 검증 단계다.

Codex의 ASK는 현재 DENY로 반환한다. execpolicy `prompt`를 함께 설치해도
이 차단을 해제하지 못한다. 파일럿에는 중복 execpolicy를 설치하지 않는다.
정책 예외는 필요성과 범위를 검토한 사용자가 설정한다.

## 검증과 수집

모의 hook payload로 정상 명령, 네트워크 명령, 민감 파일 수정,
테스트 skip 추가를 검사한다. 금지 명령 자체를 실행하거나 실제 비밀 파일을 읽지 않는다.
확인·차단 로그는 기존 사용자 정책 경로의 `audit.jsonl`에 남으며
명령 원문과 파일 내용은 기록하지 않는다.

실제 세션에서는 안전한 조회와 합성 파일 대상 검사를 수행해 호출 여부를 확인한다.
오탐/누락, 규칙 ID, 사용자 조치, 작업 지연을 모아 1~2주 뒤 규칙을 조정한다.
hook은 임의 스크립트 내부 I/O, 모든 외부 도구, 네트워크 통신을 통제하는
샌드박스가 아니며 완전한 유출 방지라고 주장하지 않는다.

## 다음 구현: Stop 게이트

신뢰 설정에 고정한 테스트 명령, timeout, 최대 재시도 횟수를 사용한다.
마지막 변경 이후 실행된 검사인지 소스 식별값과 연결하며, 실패·시간 초과·검사 불가를
구분한다. 실패하면 제한된 피드백을 보내고, 반복 한도에서 미완료 사유를 보고한다.
에이전트가 명령·한도·테스트를 약화해서 통과하지 못하게 한다.
Python부터 연결하고 TS/JS와 Java는 프로젝트별 명령을 등록한 뒤 확대한다.
메모리·CPU 측정은 기능 테스트와 분리하고 그 이후 최적화 루프로 확장한다.

참고: [Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Claude Code hooks](https://code.claude.com/docs/en/hooks).
