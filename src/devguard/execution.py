"""Bounded, measured execution of commands selected by trusted workflow configuration."""

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

from devguard.secrets import ASSIGNMENT, HIGH_CONFIDENCE


def scrub(text: str) -> str:
    for pattern in HIGH_CONFIDENCE.values():
        text = pattern.sub("[REDACTED]", text)
    return ASSIGNMENT.sub("[REDACTED ASSIGNMENT]", text)


def environment() -> dict:
    """The parent environment without credential-looking variables."""
    return {
        k: v
        for k, v in os.environ.items()
        if not any(word in k.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    }


def run(
    argv: list[str],
    cwd: Path,
    timeout: float = 120,
    memory_mb: int = 2048,
    env_extra: dict | None = None,
    output_bytes: int = 1_000_000,
    keep_bytes: int = 8000,
    redact: bool = True,
) -> dict:
    """Run argv; `keep_bytes` is the output tail returned, `redact` scrubs it for secrets."""
    import psutil

    started = time.monotonic()
    env = environment()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_ADDOPTS", None)
    env.update(env_extra or {})
    tail = bytearray()
    total = 0
    digest = hashlib.sha256()
    lock = threading.Lock()
    try:
        process = subprocess.Popen(  # noqa: S603 - trusted argv; shell=False
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as exc:
        return {"status": "NOT_AVAILABLE", "error": type(exc).__name__}

    def drain():
        nonlocal total
        try:
            while chunk := process.stdout.read1(4096):
                with lock:
                    digest.update(chunk)
                    total += len(chunk)
                    tail.extend(chunk)
                    if len(tail) > 2 * keep_bytes:
                        del tail[: len(tail) - keep_bytes]
        finally:
            process.stdout.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        parent = psutil.Process(process.pid)
        known = {process.pid: parent}
    except psutil.NoSuchProcess:
        parent = None
        known = {}
    cpu_by_pid: dict[int, float] = {}
    peak = 0
    status = "PASS"
    while process.poll() is None:
        rss = 0
        try:
            if parent is not None:
                known.update({p.pid: p for p in parent.children(recursive=True)})
        except psutil.Error:
            pass
        for pid, child in list(known.items()):
            try:
                rss += child.memory_info().rss
                times = child.cpu_times()
                cpu_by_pid[pid] = max(cpu_by_pid.get(pid, 0), times.user + times.system)
            except psutil.Error:
                pass
        peak = max(peak, rss)
        if time.monotonic() - started > timeout:
            status = "TIMEOUT"
        elif peak > memory_mb * 1024 * 1024:
            status = "MEMORY_LIMIT"
        elif total > output_bytes:
            status = "OUTPUT_LIMIT"
        if status != "PASS":
            break
        time.sleep(0.02)
    # Never leave discovered child processes alive after a check, including timeout paths.
    for child in reversed(list(known.values())):
        try:
            if child.is_running():
                child.kill()
        except psutil.Error:
            pass
    process.wait(timeout=5)
    reader.join(timeout=5)
    if reader.is_alive():
        status = "INCONCLUSIVE"
    if status == "PASS" and total > output_bytes:
        status = "OUTPUT_LIMIT"
    if status == "PASS" and process.returncode:
        status = "FAIL"
    with lock:
        text = bytes(tail).decode("utf-8", errors="replace")
        output = (scrub(text) if redact else text)[-keep_bytes:]
        output_hash = digest.hexdigest()
    return {
        "status": status,
        "exit_code": process.returncode,
        "wall_seconds": round(time.monotonic() - started, 4),
        "cpu_seconds": round(sum(cpu_by_pid.values()), 4),
        "peak_rss_bytes": peak,
        "sample_interval_ms": 20,
        "output_bytes": total,
        "output_sha256": output_hash,
        "output": output,
    }
