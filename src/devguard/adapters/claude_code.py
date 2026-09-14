"""Claude Code hook adapter (PreToolUse and UserPromptSubmit).

Exit code 2 blocks and shows stderr to Claude; ASK is rendered as a PreToolUse
`permissionDecision: "ask"`. Field names that vary between versions are read defensively.
"""

import json
from pathlib import Path
from typing import TextIO

from devguard.adapters import existing_text, optional_string, required_string
from devguard.model import Decision, FileChange, ToolCall, Verdict

NAME = "claude-code"
SHELL_TOOLS = ("Bash", "PowerShell")


def to_call(payload: dict, cwd: Path) -> ToolCall | None:
    tool = payload.get("tool_name")
    data = payload.get("tool_input") or {}
    if not isinstance(data, dict):
        raise ValueError("tool_input must be a JSON object")
    base = {"agent": NAME, "tool": str(tool), "cwd": str(cwd)}

    if tool == "Read":
        return ToolCall(**base, reads=(required_string(data, "file_path"),))
    if tool == "Grep":
        reads = tuple(
            value
            for value in (data.get("path"), data.get("glob"))
            if isinstance(value, str) and value
        )
        return ToolCall(**base, reads=reads) if reads else None
    if tool == "Write":
        path = required_string(data, "file_path")
        added = optional_string(data, "content", "file_contents")
        return ToolCall(**base, changes=(FileChange(path, existing_text(cwd, path), added),))
    if tool == "Edit":
        path = required_string(data, "file_path")
        removed = optional_string(data, "old_string", "old_contents")
        added = optional_string(data, "new_string", "new_contents")
        return ToolCall(**base, changes=(FileChange(path, removed, added),))
    if tool == "MultiEdit":
        path = required_string(data, "file_path")
        edits = [edit for edit in data.get("edits") or [] if isinstance(edit, dict)]
        removed = "\n".join(optional_string(edit, "old_string") for edit in edits)
        added = "\n".join(optional_string(edit, "new_string") for edit in edits)
        return ToolCall(**base, changes=(FileChange(path, removed, added),))
    if tool == "NotebookEdit":
        path = required_string(data, "notebook_path")
        return ToolCall(
            **base, changes=(FileChange(path, "", optional_string(data, "new_source")),)
        )
    if tool in SHELL_TOOLS:
        return ToolCall(**base, command=required_string(data, "command"))
    return None


def emit(event: str, verdict: Verdict, stdout: TextIO, stderr: TextIO) -> int:
    if verdict.decision is Decision.DENY:
        stderr.write(verdict.reason() + "\n")
        return 2
    if verdict.decision is Decision.ASK and event == "PreToolUse":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": verdict.reason(),
            }
        }
        stdout.write(json.dumps(output, ensure_ascii=False))
    return 0
