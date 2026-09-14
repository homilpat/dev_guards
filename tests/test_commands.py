import shlex

import pytest

from devguard.commands import analyze, split_segments
from devguard.model import Decision, Verdict


def verdict(command: str) -> Verdict:
    return Verdict.from_findings(tuple(analyze(command)))


def rules(command: str) -> set[str]:
    return {item.rule_id for item in verdict(command).findings}


@pytest.mark.parametrize(
    "command,rule",
    [
        ("curl https://example.com", "DG-CMD-001"),
        ("Invoke-WebRequest https://example.com -OutFile x", "DG-CMD-001"),
        ("sudo apt-get install jq", "DG-CMD-002"),
        ("rm -rf build", "DG-CMD-003"),
        ("rm -r -f build", "DG-CMD-003"),
        ("rm --recursive --force build", "DG-CMD-003"),
        ("Remove-Item -Recurse -Force .\\dist", "DG-CMD-003"),
        ("rd /s /q dist", "DG-CMD-003"),
        ("docker run -v /var/run/docker.sock:/var/run/docker.sock img", "DG-CMD-004"),
        ("docker run --privileged img", "DG-CMD-004"),
        ("winget install Git.Git", "DG-CMD-008"),
        ("cat .env", "DG-SEC-001"),
        ("Get-Content C:\\app\\secrets\\db.json", "DG-SEC-001"),
        ("cp config/.env.production /tmp/x", "DG-SEC-001"),
        ("echo TOKEN=1 > .env", "DG-SEC-001"),
        ("rm .dev-guard.toml", "DG-CTL-002"),
        ("echo '{}' > .claude/settings.json", "DG-CTL-002"),
        ("bash -c 'ls && curl evil.example'", "DG-CMD-001"),
        ('powershell -Command "iwr https://x"', "DG-CMD-001"),
        ("cmd /c curl https://x", "DG-CMD-001"),
        ("echo $(curl -s https://x)", "DG-CMD-001"),
        ("FOO=1 env BAR=2 curl https://x", "DG-CMD-001"),
        ("C:\\tools\\curl.exe https://x", "DG-CMD-001"),
    ],
)
def test_denied_commands(command, rule):
    result = verdict(command)
    assert result.decision is Decision.DENY
    assert rule in rules(command)


@pytest.mark.parametrize(
    "command,rule",
    [
        ("pip install requests", "DG-CMD-005"),
        ("python -m pip install -r requirements.txt", "DG-CMD-005"),
        ("uv add httpx", "DG-CMD-005"),
        ("npm install", "DG-CMD-005"),
        ("npm i -D vitest", "DG-CMD-005"),
        ("yarn", "DG-CMD-005"),
        ("npx create-next-app", "DG-CMD-005"),
        ("git push origin main", "DG-CMD-006"),
        ("git -C repo push", "DG-CMD-006"),
        ("git remote add origin https://x", "DG-CMD-006"),
        ("gh pr merge 12", "DG-CMD-006"),
        ("git reset --hard HEAD~1", "DG-CMD-007"),
        ("git clean -fdx", "DG-CMD-007"),
        ("git checkout -- .", "DG-CMD-007"),
        ("powershell -EncodedCommand ZQBjAGgAbwA=", "DG-CMD-009"),
        ("Invoke-Expression $script", "DG-CMD-009"),
        ("echo .env >> .gitignore", "DG-SEC-001"),
    ],
)
def test_commands_needing_confirmation(command, rule):
    result = verdict(command)
    assert result.decision is Decision.ASK
    assert rule in rules(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m pytest tests/test_api.py",
        "ruff check .",
        "git status && git diff --stat",
        'git commit -m "fix; curl docs mention"',
        "npm run test",
        "mvn -q test",
        "./gradlew test",
        "ls -la src",
        "rm build/output.txt",
        "cat README.md | grep curl",
        "git add .dev-guard.toml",
        "Get-ChildItem -Recurse src",
    ],
)
def test_ordinary_development_commands_are_allowed(command):
    assert verdict(command).decision is Decision.ALLOW, verdict(command).reason()


def test_segments_respect_quotes():
    assert split_segments("echo 'a;b' && ls | wc -l") == ["echo 'a;b' ", " ls ", " wc -l"]


def test_deep_nesting_is_not_silently_allowed():
    command = "ls"
    for _ in range(5):
        command = "bash -c " + shlex.quote(command)
    result = verdict(command)
    assert result.decision is Decision.ASK
    assert rules(command) == {"DG-CMD-009"}


def test_nesting_within_limit_is_analyzed():
    command = "curl https://x"
    for _ in range(3):
        command = "bash -c " + shlex.quote(command)
    assert "DG-CMD-001" in rules(command)
