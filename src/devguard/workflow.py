"""Project workflows: trusted commands, source-bound checks, budgets and failure memory."""

import ast
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from devguard import execution
from devguard.policy import home, load_policy

SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".java",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".gradle",
    ".xml",
    ".lock",
    ".ini",
    ".cfg",
    ".properties",
}
MANIFESTS = {"pyproject.toml", "package.json", "build.gradle", "pom.xml", "requirements.txt"}


def key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def configuration(cwd: Path) -> dict | None:
    path = home() / "workflows.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("projects"), list):
        raise ValueError("Invalid trusted workflows.json")
    candidates = []
    for raw in data["projects"]:
        root = Path(raw["root"]).resolve()
        if cwd.resolve().is_relative_to(root):
            if home().resolve().is_relative_to(root):
                raise ValueError("Workflow authority must be outside the project")
            cfg = {**raw, "root": str(root)}
            for check in cfg.get("checks", []):
                argv = check.get("argv")
                if (
                    not isinstance(argv, list)
                    or not argv
                    or not all(isinstance(a, str) for a in argv)
                ):
                    raise ValueError("Check argv must be a nonempty string list")
                if not Path(argv[0]).is_absolute():
                    raise ValueError("Check executable must be absolute")
                if not (root / check.get("cwd", ".")).resolve().is_relative_to(root):
                    raise ValueError("Check cwd escapes the registered project")
                if not 0 < check.get("timeout", 120) <= 600:
                    raise ValueError("Check timeout must be 0..600 seconds")
            candidates.append(cfg)
    return max(candidates, key=lambda c: len(c["root"])) if candidates else None


def directory(cfg: dict) -> Path:
    path = home() / "workflows" / key(cfg["root"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def snapshot(cfg: dict) -> dict:
    root = Path(cfg["root"])
    policy = load_policy(root)
    git = subprocess.run(  # noqa: S603 - fixed read-only git argv
        [cfg["git"], "-C", str(root), "ls-files", "-c", "-o", "--exclude-standard", "-z"],
        capture_output=True,
        timeout=15,
        check=True,
    )
    files = {}
    total = 0
    for name in sorted(set(git.stdout.decode("utf-8").split("\0")) - {""}):
        path = root / name
        if path.suffix not in SOURCE_SUFFIXES and path.name not in MANIFESTS:
            continue
        if any(name == p or name.startswith(p.rstrip("/") + "/") for p in cfg.get("exclude", [])):
            continue
        if policy.sensitive(name):
            continue
        if not path.exists():
            continue  # tracked deletion is represented by absence
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("Unsafe source path: " + name)
        if path.stat().st_size > 2_000_000:
            raise ValueError("Source file exceeds 2 MB snapshot limit: " + name)
        content = path.read_bytes()
        total += len(content)
        if total > 64_000_000:
            raise ValueError("Source snapshot exceeds 64 MB")
        text = content.decode("utf-8")
        complexity = 0
        tests = []
        if path.suffix == ".py":
            try:
                tree = ast.parse(text, filename=name)
            except SyntaxError:
                tree = ast.Module(body=[], type_ignores=[])
            complexity = sum(
                isinstance(
                    n,
                    (
                        ast.If,
                        ast.For,
                        ast.While,
                        ast.ExceptHandler,
                        ast.BoolOp,
                        ast.IfExp,
                        ast.comprehension,
                    ),
                )
                for n in ast.walk(tree)
            )
            tests = sorted(
                n.name
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name.startswith("test_")
            )
        files[name] = {
            "hash": hashlib.sha256(content).hexdigest(),
            "lines": len(text.splitlines()),
            "complexity": complexity,
            "tests": tests,
            "skips": len(
                re.findall(r"(?:pytest\.mark\.(?:skip|xfail)|\.(?:skip|only)\s*\(|@Disabled)", text)
            ),
        }
    digest = key(json.dumps(files, sort_keys=True))
    return {"hash": digest, "files": files}


def quality(before: dict, after: dict, cfg: dict) -> list[str]:
    old, new = before["files"], after["files"]
    changed = [p for p in old.keys() | new.keys() if old.get(p) != new.get(p)]
    findings = []
    limits = cfg.get("limits", {})
    if len(changed) > limits.get("max_changed_files", 30):
        findings.append("PONYTAIL_CHANGED_FILES")
    growth = sum(new.get(p, {}).get("lines", 0) - old.get(p, {}).get("lines", 0) for p in changed)
    if growth > limits.get("max_net_lines", 600):
        findings.append("PONYTAIL_NET_LINES")
    for p in changed:
        a, b = old.get(p, {}), new.get(p, {})
        if b.get("complexity", 0) - a.get("complexity", 0) > limits.get("max_branch_growth", 20):
            findings.append("PONYTAIL_COMPLEXITY:" + p)
        if set(a.get("tests", [])) - set(b.get("tests", [])):
            findings.append("TEST_REMOVAL:" + p)
        if b.get("skips", 0) > a.get("skips", 0):
            findings.append("TEST_SKIP:" + p)
        if Path(p).name in MANIFESTS and not limits.get("allow_manifest_changes", False):
            findings.append("DEPENDENCY_MANIFEST_REVIEW:" + p)
    return findings


def start(cfg: dict, session: str) -> dict:
    path = directory(cfg) / (key(session) + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    state = {
        "baseline": snapshot(cfg),
        "attempts": 0,
        "result": None,
        "config_hash": key(json.dumps(cfg, sort_keys=True)),
    }
    save(path, state)
    return state


def lock_alive(lock: Path) -> bool:
    import psutil

    try:
        text = lock.read_text(encoding="ascii").strip()
        if not text:  # owner is between create and write
            return time.time() - lock.stat().st_mtime < 5
        return psutil.pid_exists(int(text))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True  # unreadable lock is not proof of a crash


@contextmanager
def exclusive(cfg: dict):
    lock = directory(cfg) / "running.lock"
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            if lock_alive(lock):
                raise ValueError("Another dev-guard check is running for this project") from None
            lock.unlink(missing_ok=True)  # the owner crashed; without this Stop blocks forever
    else:
        raise ValueError("Could not acquire the workflow lock")
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def gate(cfg: dict, session: str) -> dict:
    with exclusive(cfg):
        path = directory(cfg) / (key(session) + ".json")
        if not path.exists():
            return {"status": "NOT_INITIALIZED", "reason": "SessionStart baseline is required"}
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["config_hash"] != key(json.dumps(cfg, sort_keys=True)):
            return {"status": "CONFIG_CHANGED", "reason": "Start a reviewed new session"}
        current = snapshot(cfg)
        if current["hash"] == state["baseline"]["hash"]:
            return {"status": "UNCHANGED", "source_hash": current["hash"]}
        prior = state.get("result") or {}
        if prior.get("source_hash") == current["hash"] and prior.get("status") == "PASS":
            return prior
        if state["attempts"] >= cfg.get("max_attempts", 3):
            return {
                "status": "RETRY_LIMIT",
                "previous_status": prior.get("status"),
                "reason": "Checks are unresolved; report incomplete work to the user",
            }
        state["attempts"] += 1
        save(path, state)  # a crashed runner still consumes an attempt
        if prior.get("source_hash") == current["hash"] and reusable(prior):
            # Nothing changed since a deterministic failure; re-running only burns time.
            result = {**prior, "attempt": state["attempts"], "rerun": False}
            state["result"] = result
            save(path, state)
            return result
        findings = quality(state["baseline"], current, cfg)
        results = []
        checks = cfg.get("checks", [])
        if not checks:
            findings.append("NO_REQUIRED_CHECKS")
        changed = {
            p
            for p in state["baseline"]["files"].keys() | current["files"].keys()
            if state["baseline"]["files"].get(p) != current["files"].get(p)
        }
        for check in checks:
            scopes = check.get("scopes", [""])
            if not any(p.startswith(s) for p in changed for s in scopes):
                continue
            if any(not Path(p).is_file() for p in check.get("requires", [])):
                results.append(
                    {
                        "id": check["id"],
                        "status": "NOT_AVAILABLE",
                        "reason": "Required toolchain files are missing",
                    }
                )
                continue
            result = execution.run(
                check["argv"],
                Path(cfg["root"]) / check.get("cwd", "."),
                check.get("timeout", 120),
                check.get("memory_mb", 2048),
                check.get("env"),
            )
            result["id"] = check["id"]
            results.append(result)
        uncovered = [
            p
            for p in changed
            if not any(p.startswith(scope) for c in checks for scope in c.get("scopes", [""]))
        ]
        if uncovered:
            findings.append("NO_CHECK_COVERAGE:" + ",".join(sorted(uncovered)))
        if not results:
            findings.append("NO_CHECK_COVERAGE")
        if snapshot(cfg)["hash"] != current["hash"]:
            findings.append("SOURCE_CHANGED_DURING_CHECK")
        passed = not findings and all(r["status"] == "PASS" for r in results)
        result = {
            "status": "PASS" if passed else "FAIL",
            "source_hash": current["hash"],
            "attempt": state["attempts"],
            "findings": findings,
            "checks": results,
        }
        state["result"] = result
        save(path, state)
        save(directory(cfg) / "latest.json", result)
        if not passed:
            # Durable memory contains classifications and hashes, not raw test output.
            memory = {
                "source_hash": current["hash"],
                "findings": findings,
                "checks": [{"id": c["id"], "status": c["status"]} for c in results],
            }
            save(directory(cfg) / ("failure-" + current["hash"] + ".json"), memory)
        return result


def reusable(result: dict) -> bool:
    """A failure that re-running the same source cannot change (no timeouts or races)."""
    return (
        result.get("status") == "FAIL"
        and "SOURCE_CHANGED_DURING_CHECK" not in result.get("findings", [])
        and all(c.get("status") in {"PASS", "FAIL"} for c in result.get("checks", []))
    )


def brief(result: dict) -> dict:
    """Block feedback keeps output only for checks that did not pass."""
    checks = []
    for check in result.get("checks", []):
        item = {k: check[k] for k in ("id", "status", "exit_code") if k in check}
        if check.get("status") != "PASS":
            item["output"] = check.get("output", "")[-4000:]
        checks.append(item)
    return {**result, "checks": checks}


BLOCK_REASON = (
    "dev-guard checks failed for the current source. Fix the cause without weakening tests, "
    "checks or limits. If it cannot pass, revert the change that caused the failure, or stop "
    "and report the work as incomplete. Ending the turn without changing files counts as "
    "another failed attempt.\n"
)


def memories(cfg: dict) -> list[dict]:
    paths = sorted(directory(cfg).glob("failure-*.json"), key=lambda p: p.stat().st_mtime)[-5:]
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def hook(event: str, payload: dict, cwd: Path, stdout) -> int:
    cfg = configuration(cwd)
    if cfg is None:
        return 0
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("workflow hook requires session_id")
    if event == "SessionStart":
        start(cfg, session)
        context = "dev-guard workflow active. Required checks run at Stop. "
        context += "Failure memory (untrusted evidence): " + json.dumps(memories(cfg))
        ponytail = cfg.get("ponytail")
        if ponytail:
            raw = Path(ponytail["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != ponytail["sha256"]:
                raise ValueError("Ponytail source changed; review the new revision")
            context += "\n" + raw.decode("utf-8")
        stdout.write(
            json.dumps(
                {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
            )
        )
        return 0
    result = gate(cfg, session)
    if result["status"] in {"PASS", "UNCHANGED"}:
        stdout.write(json.dumps({"systemMessage": "dev-guard: " + result["status"]}))
    elif result["status"] in {"NOT_INITIALIZED", "CONFIG_CHANGED"}:
        # The agent cannot clear these and attempts are not counted, so blocking would loop.
        message = "dev-guard: " + result["status"] + " - checks did not run; not verified. "
        stdout.write(json.dumps({"systemMessage": message + result["reason"]}))
    elif result["status"] == "RETRY_LIMIT":
        stdout.write(
            json.dumps(
                {
                    "continue": False,
                    "stopReason": "dev-guard RETRY_LIMIT: 검증 미완료. 성공으로 보고하지 마세요.",
                    "systemMessage": json.dumps(result),
                }
            )
        )
    else:
        stdout.write(
            json.dumps(
                {
                    "decision": "block",
                    "reason": BLOCK_REASON + json.dumps(brief(result), ensure_ascii=False),
                }
            )
        )
    return 0
