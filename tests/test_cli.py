import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "arguments,decision",
    [
        (["--read", "README.md"], "ALLOW"),
        (["--read", ".env"], "DENY"),
        (["--write", ".codex/hooks.json"], "DENY"),
        (["--command", "pytest -q"], "ALLOW"),
        (["--command", "rules"], "ALLOW"),
        (["--command", "curl https://example.com"], "DENY"),
    ],
)
def test_check_cli_dispatches_to_requested_target(tmp_path, arguments, decision):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(  # noqa: S603 - fixed interpreter and CLI under test
        [sys.executable, "-B", "-m", "devguard", "check", "--cwd", str(tmp_path), *arguments],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == (0 if decision == "ALLOW" else 1), result.stderr
    assert result.stdout.splitlines()[0] == decision


def test_isolated_launcher_from_unrelated_directory(tmp_path):
    # A project-local module with the same name must not replace the guard.
    (tmp_path / "devguard.py").write_text("raise RuntimeError('shadow module loaded')\n")
    payload = {
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "curl https://example.com"},
    }
    result = subprocess.run(  # noqa: S603 - fixed isolated launcher, fake tool is never run
        [sys.executable, "-I", str(ROOT / "src/devguard/launcher.py"), "hook", "codex"],
        cwd=tmp_path,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "DG-CMD-001" in result.stderr
