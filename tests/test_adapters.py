import io
import json
import shutil
import subprocess

import pytest

from devguard.adapters import claude_code, codex
from devguard.cli import run_hook


def hook(adapter, payload) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    code = run_hook(adapter, io.StringIO(raw), stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def pre(tmp_path, tool, **tool_input):
    return {
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
        "tool_name": tool,
        "tool_input": tool_input,
    }


def test_claude_read_of_sensitive_file_is_blocked_with_reason(tmp_path, isolated_home):
    code, stdout, stderr = hook(claude_code, pre(tmp_path, "Read", file_path=".env"))
    assert code == 2 and stdout == ""
    assert "DG-SEC-001" in stderr and "M08" in stderr
    entry = json.loads((isolated_home / "audit.jsonl").read_text(encoding="utf-8"))
    assert entry["decision"] == "DENY" and entry["paths"] == [".env"]


def test_claude_ask_uses_permission_decision(tmp_path):
    code, stdout, stderr = hook(claude_code, pre(tmp_path, "Bash", command="git push"))
    assert code == 0 and stderr == ""
    output = json.loads(stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "ask"
    assert "DG-CMD-006" in output["permissionDecisionReason"]


def test_claude_write_compares_with_existing_file(tmp_path):
    (tmp_path / "tests").mkdir()
    fixture = 'KEY = "-----BEGIN PRIVATE KEY-----"\n'
    (tmp_path / "tests" / "test_keys.py").write_text(fixture, encoding="utf-8")
    payload = pre(tmp_path, "Write", file_path="tests/test_keys.py", content=fixture + "# more\n")
    assert hook(claude_code, payload)[0] == 0
    payload = pre(tmp_path, "Write", file_path="src/new.py", content=fixture)
    assert hook(claude_code, payload)[0] == 2


def test_claude_edit_field_name_variants(tmp_path):
    payload = pre(
        tmp_path,
        "Edit",
        file_path="tests/test_a.py",
        old_contents="def test_a(): ...",
        new_contents="@pytest.mark.skip\ndef test_a(): ...",
    )
    code, _, stderr = hook(claude_code, payload)
    assert code == 2 and "DG-TST-001" in stderr


def test_claude_unknown_tools_and_events_pass(tmp_path):
    assert hook(claude_code, pre(tmp_path, "WebSearch", query="x"))[0] == 0
    assert hook(claude_code, {"hook_event_name": "Stop", "cwd": str(tmp_path)})[0] == 0


def test_local_only_prompt_is_rejected(tmp_path):
    (tmp_path / ".dev-guard.toml").write_text('classification = "local-only"\n', encoding="utf-8")
    payload = {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "hi"}
    code, _, stderr = hook(claude_code, payload)
    assert code == 2 and "DG-SEC-003" in stderr


def test_malformed_input_fails_closed_unless_opted_out(tmp_path, monkeypatch):
    assert hook(claude_code, "not json")[0] == 2
    assert hook(claude_code, pre(tmp_path, "Read"))[0] == 2
    (tmp_path / ".dev-guard.toml").write_text("oops = 1\n", encoding="utf-8")
    assert hook(claude_code, pre(tmp_path, "Read", file_path="README.md"))[0] == 2
    monkeypatch.setenv("DEV_GUARD_FAIL_OPEN", "1")
    assert hook(claude_code, "not json")[0] == 0


def test_codex_shell_and_ask_become_blocks(tmp_path):
    code, _, stderr = hook(codex, pre(tmp_path, "Bash", command="curl https://x"))
    assert code == 2 and "DG-CMD-001" in stderr
    code, _, stderr = hook(codex, pre(tmp_path, "Bash", command=["git", "push"]))
    assert code == 2 and "DG-CMD-006" in stderr and "Codex" in stderr
    assert hook(codex, pre(tmp_path, "Bash", command="pytest -q"))[0] == 0


def test_codex_apply_patch_is_parsed(tmp_path):
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: config/.env",
            "+TOKEN=1",
            "*** Update File: src/app.py",
            "@@",
            "-old = 1",
            "+new = 1",
            "*** End Patch",
        ]
    )
    changes = codex.parse_patch(patch, tmp_path)
    assert [c.path for c in changes] == ["config/.env", "src/app.py"]
    assert changes[1].removed == "old = 1" and changes[1].added == "new = 1"
    code, _, stderr = hook(codex, pre(tmp_path, "apply_patch", command=patch))
    assert code == 2 and "DG-SEC-001" in stderr


def test_codex_apply_patch_delete_and_move(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): ...\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Delete File: tests/test_x.py\n*** End Patch"
    code, _, stderr = hook(codex, pre(tmp_path, "apply_patch", command=patch))
    assert code == 2 and "DG-TST-002" in stderr
    moved = "*** Update File: a.py\n*** Move to: .codex/hooks.json\n+x"
    assert {c.path for c in codex.parse_patch(moved, tmp_path)} == {"a.py", ".codex/hooks.json"}
    with pytest.raises(ValueError):
        codex.parse_patch("not a patch", tmp_path)


def test_generated_codex_rules_shape():
    rules = codex.render_rules()
    assert 'prefix_rule(pattern=["curl"], decision="forbidden"' in rules
    assert 'prefix_rule(pattern=["git", "push"], decision="prompt"' in rules


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI not installed")
@pytest.mark.parametrize(
    "command,expected",
    [(["curl", "https://x"], "forbidden"), (["git", "push"], "prompt"), (["pytest"], None)],
)
def test_generated_rules_load_in_codex(tmp_path, command, expected):
    rules = tmp_path / "dev-guard.rules"
    rules.write_text(codex.render_rules(), encoding="utf-8")
    executable = shutil.which("codex")
    completed = subprocess.run(  # noqa: S603 - fixed argv to the locally installed CLI
        [executable, "execpolicy", "check", "--rules", str(rules), "--", *command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    decision = result.get("decision")
    assert decision == expected, completed.stdout
