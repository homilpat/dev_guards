import pytest

from devguard import paths, secrets, testguard
from devguard.model import FileChange


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/.env.production",
        "C:\\app\\.ENV",
        "certs/server.pem",
        "deploy/tls.key",
        "keys/client.p12",
        "store.pfx",
        "android/release.keystore",
        "backend/keystore.jks",
        "credentials.json",
        "secret/db.txt",
        "app/secrets/token",
        "~/.ssh/id_rsa",
        ".npmrc",
    ],
)
def test_sensitive_paths_from_m08(path):
    assert paths.sensitive(path)


@pytest.mark.parametrize("path", ["README.md", "src/keyboard.py", "envs/dev.txt", "secretary.md"])
def test_ordinary_paths_are_not_sensitive(path):
    assert not paths.sensitive(path)


@pytest.mark.parametrize(
    "path",
    [
        ".dev-guard.toml",
        "project/.claude/settings.json",
        ".claude/settings.local.json",
        ".claude/hooks/guard.py",
        ".codex/hooks.json",
        "C:\\Users\\me\\.dev-guard\\policy.toml",
    ],
)
def test_guard_configuration_paths(path):
    assert paths.guard_config(path)


def test_skills_and_ordinary_claude_files_are_not_guard_configuration():
    assert not paths.guard_config(".claude/skills/review/SKILL.md")
    assert not paths.guard_config("docs/hooks/intro.md")


def test_high_confidence_secrets_counted_against_existing_text():
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    assert secrets.new_high_confidence(f"aws = '{key}'", "") == ["aws-access-key"]
    assert secrets.new_high_confidence(f"aws = '{key}' # moved", f"aws = '{key}'") == []
    assert secrets.new_high_confidence("-----BEGIN PRIVATE KEY-----", "") == ["private-key"]
    assert secrets.new_high_confidence("sk-ant-" + "a" * 30, "") == ["anthropic-key"]


def test_generic_assignment_is_suspicious_only():
    assert secrets.new_assignment('password = "hunter22"', "")
    assert not secrets.new_assignment("password = get_password()", "")


@pytest.mark.parametrize(
    "path,added",
    [
        ("tests/test_api.py", "@pytest.mark.skip(reason='flaky')\ndef test_x(): ..."),
        ("web/app.test.ts", "it.skip('renders', () => {})"),
        ("web/app.spec.js", "describe.only('suite', () => {})"),
        ("src/test/java/AppTest.java", "@Disabled\n@Test void works() {}"),
    ],
)
def test_disabling_tests_is_denied(path, added):
    findings = testguard.check(FileChange(path, "", added))
    assert [item.rule_id for item in findings] == ["DG-TST-001"]


def test_removing_test_definitions_asks_and_ordinary_files_are_ignored():
    removed = "def test_a():\n    pass\n\ndef test_b():\n    pass\n"
    findings = testguard.check(FileChange("tests/test_a.py", removed, "def test_a():\n    pass\n"))
    assert [item.rule_id for item in findings] == ["DG-TST-002"]
    assert testguard.check(FileChange("src/app.py", "", "@pytest.mark.skip")) == []
    assert testguard.check(FileChange("tests/test_a.py", "x", "", deleted=True))[0].rule_id == (
        "DG-TST-002"
    )
