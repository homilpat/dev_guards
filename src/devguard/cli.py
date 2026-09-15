"""dev-guard command line: hook entry points and inspection helpers."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TextIO

from devguard import __version__, audit
from devguard.adapters import claude_code, codex
from devguard.engine import evaluate
from devguard.model import Decision, FileChange, ToolCall
from devguard.policy import load_policy
from devguard.rules import RULES

ADAPTERS = {claude_code.NAME: claude_code, codex.NAME: codex}
RECALL_ACTIONS = ("add", "list", "invalidate", "search", "server-start", "server-stop", "server")
RECALL_OK = {"AVAILABLE", "RECORDED", "INVALIDATED", "RUNNING", "STARTING", "STOPPED"}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        # Hooks talk JSON over pipes; the Windows default code page would garble Korean text.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="dev-guard")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="subcommand", required=True)
    hook = sub.add_parser("hook", help="Hook entry point; reads the agent JSON payload on stdin.")
    hook.add_argument("agent", choices=sorted(ADAPTERS))
    check = sub.add_parser("check", help="Evaluate a command or path outside an agent session.")
    check.add_argument("--cwd", default=".")
    target = check.add_mutually_exclusive_group(required=True)
    target.add_argument("--command")
    target.add_argument("--read")
    target.add_argument("--write", help="Path to be written (content not checked).")
    sub.add_parser("rules", help="List guardrails with their source documents.")
    sub.add_parser("codex-rules", help="Print Codex execpolicy rules for ~/.codex/rules/.")
    flow = sub.add_parser("workflow", help="Run source-bound checks or inspect local integrations.")
    flow.add_argument("action", choices=["start", "check", "status", "context", "memory"])
    flow.add_argument("--cwd", default=".")
    flow.add_argument("--session", default="manual")
    flow.add_argument("--query", default="")
    bench = sub.add_parser("benchmark", help="Measure a trusted registered workload.")
    bench.add_argument("profile")
    bench.add_argument("--cwd", default=".")
    bench.add_argument("--baseline", action="store_true")
    note = sub.add_parser("recall", help="Manage verified project notes and the embedding server.")
    note.add_argument("action", choices=[*RECALL_ACTIONS])
    note.add_argument("--cwd", default=".")
    note.add_argument("--fact", default="")
    note.add_argument("--source", default="manual")
    note.add_argument("--anchor", action="append", default=[], help="File the note depends on.")
    note.add_argument("--expires-days", type=float)
    note.add_argument("--id", default="")
    note.add_argument("--query", default="")
    args = parser.parse_args(argv)

    if args.subcommand == "hook":
        return run_hook(ADAPTERS[args.agent], sys.stdin, sys.stdout, sys.stderr)
    if args.subcommand == "rules":
        for rule in RULES.values():
            print(f"{rule.id}  {rule.default.name:<5}  {rule.title}  [{rule.source}]")
        return 0
    if args.subcommand == "codex-rules":
        sys.stdout.write(codex.render_rules())
        return 0
    if args.subcommand in {"workflow", "benchmark", "recall"}:
        return _workflow(args)
    return _check(args)


def run_hook(adapter, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    try:
        payload = json.loads(stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        event = payload.get("hook_event_name", "")
        cwd = Path(payload.get("cwd") or os.getcwd())
        if event in {"SessionStart", "Stop"}:
            from devguard import workflow

            return workflow.hook(event, payload, cwd, stdout)
        if event == "PreToolUse":
            call = adapter.to_call(payload, cwd)
        elif event == "UserPromptSubmit":
            call = ToolCall(agent=adapter.NAME, tool="prompt", cwd=str(cwd))
        else:
            return 0
        if call is None:
            return 0
        policy = load_policy(cwd)
        verdict = evaluate(call, policy)
        audit.record(policy.home, event, call, verdict)
        if event == "UserPromptSubmit" and verdict.decision is Decision.ALLOW:
            from devguard import context

            context.prompt(payload, cwd, stdout)
        return adapter.emit(event, verdict, stdout, stderr)
    except Exception as exc:  # A guardrail that crashes must not silently allow (M06 fail-closed).
        if isinstance(locals().get("payload"), dict) and payload.get("hook_event_name") == "Stop":
            stdout.write(
                json.dumps(
                    {
                        "continue": False,
                        "stopReason": "dev-guard verification unavailable; report incomplete work: "
                        + type(exc).__name__,
                    }
                )
            )
            return 0
        if os.environ.get("DEV_GUARD_FAIL_OPEN") == "1":
            stderr.write(f"[dev-guard] 내부 오류로 검사를 건너뜀: {type(exc).__name__}: {exc}\n")
            return 0
        stderr.write(f"[dev-guard] 내부 오류로 차단함(fail-closed): {type(exc).__name__}: {exc}\n")
        return 2


def _check(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd)
    if args.command is not None:
        call = ToolCall("cli", "check", str(cwd), command=args.command)
    elif args.read is not None:
        call = ToolCall("cli", "check", str(cwd), reads=(args.read,))
    else:
        call = ToolCall("cli", "check", str(cwd), changes=(FileChange(args.write),))
    verdict = evaluate(call, load_policy(cwd))
    print(verdict.decision.name)
    if verdict.findings:
        print(verdict.reason())
    return 0 if verdict.decision is Decision.ALLOW else 1


def _workflow(args: argparse.Namespace) -> int:
    from devguard import benchmark, context, workflow

    cfg = workflow.configuration(Path(args.cwd))
    if not cfg:
        print(json.dumps({"status": "NOT_CONFIGURED"}))
        return 1
    if args.subcommand == "recall":
        return _recall(args, cfg)
    if args.subcommand == "benchmark":
        result = benchmark.run(cfg, args.profile, args.baseline)
    elif args.action == "start":
        result = workflow.start(cfg, args.session)
    elif args.action == "check":
        result = workflow.gate(cfg, args.session)
    elif args.action == "context":
        result = context.query(cfg, args.query)
    elif args.action == "memory":
        result = {"failures": workflow.memories(cfg)}
    else:
        result = {
            "root": cfg["root"],
            "checks": [c["id"] for c in cfg.get("checks", [])],
            "recall_configured": bool(cfg.get("recall")),
            "potpie_configured": bool(cfg.get("potpie")),
            "ponytail_configured": bool(cfg.get("ponytail")),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "PASS") in {"PASS", "UNCHANGED"} else 1


def _recall(args: argparse.Namespace, cfg: dict) -> int:
    from devguard import recall

    provider = cfg.get("recall")
    try:
        if not provider:
            result = {"status": "NOT_CONFIGURED"}
        elif args.action == "add":
            result = recall.add(cfg, args.fact, args.source, args.anchor, args.expires_days)
        elif args.action == "list":
            result = {"status": "AVAILABLE", "notes": recall.notes(cfg)}
        elif args.action == "invalidate":
            result = recall.invalidate(cfg, args.id)
        elif args.action == "search":
            result = recall.search(cfg, args.query)
        elif args.action == "server-start":
            result = {"status": recall.start_server(provider)}
        elif args.action == "server-stop":
            result = {"status": recall.stop_server(provider)}
        else:
            result = recall.server_status(provider)
    except recall.NotReady as exc:
        result = {"status": exc.status}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in RECALL_OK else 1
