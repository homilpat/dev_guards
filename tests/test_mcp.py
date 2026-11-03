"""MCP adapter contract and policy reuse tests."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devguard.mcp import check, handle

ROOT = Path(__file__).resolve().parents[1]


def test_tool_list_and_initialize(tmp_path):
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, tmp_path)
    assert init["result"]["capabilities"]["tools"]
    result = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tmp_path)
    assert {item["name"] for item in result["result"]["tools"]} == {
        "check_command", "check_file_access", "check_patch", "validate_task"
    }


def test_existing_engine_is_reused(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DEV_GUARD_HOME", str(home))
    assert check("check_command", {"command": "pytest -q"}, tmp_path)["decision"] == "ALLOW"
    denied = check("check_command", {"command": "curl https://example.com"}, tmp_path)
    assert denied["decision"] == "DENY"
    assert denied["allowed_without_confirmation"] is False
    assert any(item["rule_id"] == "DG-CMD-001" for item in denied["findings"])
    assert (home / "audit.jsonl").is_file()


def test_sensitive_file_and_protected_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_GUARD_HOME", str(tmp_path / "home"))
    assert check("check_file_access", {"path": ".env", "operation": "read"}, tmp_path)[
        "decision"
    ] == "DENY"
    assert check("check_patch", {
        "path": ".codex/hooks.json", "removed": "", "added": "{}"
    }, tmp_path)["decision"] == "DENY"



@pytest.mark.parametrize("removed,added", [("", "new"), ("old", ""), ("", "")])
def test_empty_patch_text_is_valid(tmp_path, removed, added):
    result = check(
        "check_patch",
        {"path": "src/example.py", "removed": removed, "added": added},
        tmp_path,
    )
    assert result["decision"] == "ALLOW"



def test_invalid_arguments_fail_closed(tmp_path):
    for values in ({"command": "ls", "cwd": "unknown_cwd"}, {"command": 3}, {}):
        response = handle({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "check_command", "arguments": values}
        }, tmp_path)
        assert response["result"]["isError"] is True


def test_task_envelope_does_not_execute(tmp_path):
    result = check("validate_task", {
        "task_id": "t-1", "action": "command", "target": "pytest -q"
    }, tmp_path)
    assert result["advisory_only"] is True
    assert result["decision"] == "ALLOW"


def test_stdio_round_trip(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "DEV_GUARD_HOME": str(tmp_path / "home")}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "check_command", "arguments": {"command": "sudo reboot"}}},
    ]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and module under test
        [sys.executable, "-m", "devguard.mcp", "--cwd", str(tmp_path)],
        input="".join(json.dumps(item) + "\n" for item in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["id"] for item in messages] == [1, 2, 3]
    assert json.loads(messages[-1]["result"]["content"][0]["text"])["decision"] == "DENY"


def test_invalid_policy_is_not_allow(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "policy.toml").write_text("=not toml", encoding="utf-8")
    monkeypatch.setenv("DEV_GUARD_HOME", str(home))
    response = handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "check_command", "arguments": {"command": "pytest"}}
    }, tmp_path)
    assert response["result"]["isError"] is True
