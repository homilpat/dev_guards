"""Local audit trail for ASK/DENY decisions. Never stores command text or file content (M08 §6)."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from devguard.model import Decision, ToolCall, Verdict


def record(home: Path, event: str, call: ToolCall, verdict: Verdict) -> None:
    if verdict.decision is Decision.ALLOW:
        return
    entry = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent": call.agent,
        "event": event,
        "tool": call.tool,
        "cwd": call.cwd,
        "decision": verdict.decision.name,
        "rules": sorted({item.rule_id for item in verdict.findings}),
        "paths": sorted({*call.reads, *(change.path for change in call.changes)}),
    }
    if call.command is not None:
        entry["command_sha256"] = hashlib.sha256(call.command.encode("utf-8")).hexdigest()
    try:
        home.mkdir(parents=True, exist_ok=True)
        with (home / "audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # The decision itself is already enforced; a full disk must not turn DENY into ALLOW.
        pass
