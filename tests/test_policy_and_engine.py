from pathlib import Path

import pytest

from devguard.engine import evaluate
from devguard.model import Decision, FileChange, ToolCall
from devguard.policy import PolicyError, load_policy


def call(cwd: Path, **kwargs) -> ToolCall:
    return ToolCall("test", "tool", str(cwd), **kwargs)


def test_local_only_repository_denies_everything_below_it(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".dev-guard.toml").write_text('classification = "local-only"\n', encoding="utf-8")
    policy = load_policy(repo / "src")
    result = evaluate(call(repo / "src", reads=("README.md",)), policy)
    assert result.decision is Decision.DENY
    assert [f.rule_id for f in result.findings] == ["DG-SEC-003"]
    assert evaluate(call(tmp_path, reads=("README.md",)), load_policy(tmp_path)).decision is (
        Decision.ALLOW
    )


@pytest.mark.parametrize(
    "content",
    ['[overrides]\n"DG-CMD-001" = "allow"\n', 'allow_paths = [".env"]\n', 'classification = "x"\n'],
)
def test_repository_policy_cannot_loosen_guardrails(tmp_path, content):
    (tmp_path / ".dev-guard.toml").write_text(content, encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(tmp_path)


def test_repository_policy_can_add_sensitive_paths(tmp_path):
    (tmp_path / ".dev-guard.toml").write_text(
        'sensitive_paths = ["data/*.csv"]\n', encoding="utf-8"
    )
    result = evaluate(call(tmp_path, reads=("data/patients.csv",)), load_policy(tmp_path))
    assert result.decision is Decision.DENY


def test_user_policy_overrides_and_allow_paths(tmp_path, isolated_home):
    isolated_home.mkdir()
    (isolated_home / "policy.toml").write_text(
        'allow_paths = [".env.example"]\n\n[overrides]\n"DG-CMD-005" = "allow"\n',
        encoding="utf-8",
    )
    policy = load_policy(tmp_path)
    assert evaluate(call(tmp_path, command="pip install x"), policy).decision is Decision.ALLOW
    assert evaluate(call(tmp_path, reads=(".env.example",)), policy).decision is Decision.ALLOW
    assert evaluate(call(tmp_path, reads=(".env",)), policy).decision is Decision.DENY


@pytest.mark.parametrize(
    "content", ['[overrides]\n"DG-NOPE" = "allow"\n', '[overrides]\n"DG-CMD-001" = "maybe"\n', "=x"]
)
def test_invalid_user_policy_is_an_error(isolated_home, tmp_path, content):
    isolated_home.mkdir()
    (isolated_home / "policy.toml").write_text(content, encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(tmp_path)


def test_change_checks_combine_to_most_restrictive(tmp_path):
    changes = (
        FileChange("AGENTS.md", "", "new rule"),
        FileChange(".git/config", "", "[core]"),
        FileChange("src/settings.py", "", 'API_KEY = "abcd1234"'),
    )
    result = evaluate(call(tmp_path, changes=changes), load_policy(tmp_path))
    assert result.decision is Decision.DENY
    assert {f.rule_id for f in result.findings} == {"DG-CTL-003", "DG-CTL-001", "DG-SEC-004"}
