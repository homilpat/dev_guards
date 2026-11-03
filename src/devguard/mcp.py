"""Local stdio MCP adapter for the existing deterministic policy engine.

Inspection only. This server cannot execute, edit, or authorize future calls.
The integration must enforce policy again at the actual execution boundary.
"""

import argparse
import json
import sys
from pathlib import Path

from devguard import audit
from devguard.engine import evaluate
from devguard.model import Decision, FileChange, ToolCall
from devguard.policy import load_policy

PROTOCOL_VERSION = "2025-03-26"
MAX_MESSAGE = 1024 * 1024

TOOLS = [
    {
        "name": "check_command",
        "description": "Inspect a proposed shell command; never executes it.",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_file_access",
        "description": "Inspect a proposed read or write path; never reads/writes the file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "operation": {"type": "string", "enum": ["read", "write"]},
            },
            "required": ["path", "operation"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_patch",
        "description": (
            "Inspect proposed text changes using the shared policy engine; no patch is applied."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "removed": {"type": "string"},
                "added": {"type": "string"},
                "deleted": {"type": "boolean"},
            },
            "required": ["path", "removed", "added"],
            "additionalProperties": False,
        },
    },
    {
        "name": "validate_task",
        "description": (
            "Check a limited task envelope and proposed command/path against policy; no dispatch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "action": {"type": "string", "enum": ["read", "write", "command"]},
                "target": {"type": "string"},
            },
            "required": ["task_id", "action", "target"],
            "additionalProperties": False,
        },
    },
]


class InvalidArguments(ValueError):
    """A tool call did not match its advertised schema."""


def _arguments(name: str, values: object) -> dict:
    schemas = {item["name"]: item["inputSchema"] for item in TOOLS}
    if name not in schemas:
        raise InvalidArguments("unknown tool")
    if not isinstance(values, dict):
        raise InvalidArguments("arguments must be an object")
    schema = schemas[name]
    if set(values) - set(schema["properties"]) or not set(schema["required"]) <= set(values):
        raise InvalidArguments("missing or unknown argument")
    for field, value in values.items():
        prop = schema["properties"][field]
        kind = prop["type"]
        if kind == "string" and (
            not isinstance(value, str)
            or (not value and field not in {"removed", "added"})
            or len(value) > 100000
        ):
            raise InvalidArguments("invalid string argument: " + field)
        if kind == "boolean" and not isinstance(value, bool):
            raise InvalidArguments("invalid boolean argument: " + field)
        if "enum" in prop and value not in prop["enum"]:
            raise InvalidArguments("invalid enum argument: " + field)
    return values


def check(name: str, arguments: object, root: Path) -> dict:
    """Inspection is advisory; a positive verdict is not a capability token."""
    values = _arguments(name, arguments)
    if name == "check_command":
        call = ToolCall("mcp", name, str(root), command=values["command"])
    elif name == "check_file_access":
        path = values["path"]
        call = ToolCall(
            "mcp",
            name,
            str(root),
            reads=(path,) if values["operation"] == "read" else (),
            changes=(FileChange(path),) if values["operation"] == "write" else (),
        )
    elif name == "check_patch":
        call = ToolCall(
            "mcp",
            name,
            str(root),
            changes=(
                FileChange(
                    values["path"], values["removed"], values["added"], values.get("deleted", False)
                ),
            ),
        )
    else:
        action, target = values["action"], values["target"]
        call = ToolCall(
            "mcp",
            name,
            str(root),
            command=target if action == "command" else None,
            reads=(target,) if action == "read" else (),
            changes=(FileChange(target),) if action == "write" else (),
        )
    policy = load_policy(root)
    verdict = evaluate(call, policy)
    audit.record(policy.home, "MCPInspection", call, verdict)
    return {
        "decision": verdict.decision.name,
        "allowed_without_confirmation": verdict.decision is Decision.ALLOW,
        "advisory_only": True,
        "findings": [
            {
                "rule_id": finding.rule_id,
                "decision": finding.decision.name,
                "source": finding.source,
            }
            for finding in verdict.findings
        ],
    }


def handle(request: object, root: Path) -> dict | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    method = request.get("method")
    request_id = request.get("id")
    if "id" not in request:  # notifications have no response
        return None
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "dev-guard", "version": "0.1.0"},
            "instructions": "Inspection only; enforce policy at the real execution boundary.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(request_id, -32602, "Invalid params")
        try:
            verdict = check(params["name"], params.get("arguments", {}), root)
            result = {
                "content": [{"type": "text", "text": json.dumps(verdict, ensure_ascii=False)}],
                "structuredContent": verdict,
                "isError": False,
            }
        except (InvalidArguments, OSError, ValueError) as exc:
            result = {
                "content": [{"type": "text", "text": "Inspection rejected: " + type(exc).__name__}],
                "isError": True,
            }
        except Exception:
            result = {
                "content": [{"type": "text", "text": "Inspection unavailable (fail closed)"}],
                "isError": True,
            }
    else:
        return _error(request_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(root: Path) -> None:
    """MCP stdio uses one UTF-8 JSON-RPC message per line, not HTTP framing."""
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while raw := stdin.readline(MAX_MESSAGE + 1):
        if len(raw) > MAX_MESSAGE:
            response = _error(None, -32700, "Message too large")
            # Discard the remainder of this line before accepting another request.
            while raw and not raw.endswith(b"\n"):
                raw = stdin.readline(MAX_MESSAGE + 1)
        else:
            try:
                response = handle(json.loads(raw), root)
            except (ValueError, UnicodeDecodeError):
                response = _error(None, -32700, "Parse error")
        if response is not None:
            stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-guard-mcp")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.cwd.resolve(strict=True)
    if not root.is_dir():
        parser.error("--cwd must be a directory")
    serve(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
