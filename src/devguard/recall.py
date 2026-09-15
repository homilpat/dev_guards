"""Project recall: verified notes ranked by a resident local embedding model.

Covers what dev-guard used from Potpie (record a note, embed it, rank by cosine) and adds
rules Potpie does not enforce: a note anchored to files is dropped once those files
change, because current source outranks history; notes can expire; notes containing
secrets are refused; and notes embedded by another model are reported, not scored 0.
"""

import contextlib
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
import urllib.request
from array import array
from collections import Counter
from pathlib import Path

from devguard import execution, workflow
from devguard.policy import home, load_policy

MAX_FACT = 2000
MAX_QUERY = 4000
START_GRACE_SECONDS = 180
SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    fact TEXT NOT NULL,
    source TEXT NOT NULL,
    anchors TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    invalid_at REAL,
    model TEXT NOT NULL,
    vector BLOB NOT NULL
)
"""


class NotReady(Exception):
    """The embedding server is not serving; `status` says why."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def server_directory(provider: dict) -> Path:
    identity = json.dumps([provider["python"], provider["model"], provider.get("cache")])
    path = home() / "embedder" / workflow.key(identity)[:16]
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_state(provider: dict) -> dict | None:
    import psutil

    try:
        path = server_directory(provider) / "server.json"
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = state.get("pid")
    if state.get("model") != provider["model"] or not isinstance(pid, int) or pid <= 0:
        return None
    return state if psutil.pid_exists(pid) else None


def start_server(provider: dict) -> str:
    """Launch the server detached unless it runs or a launch is still loading the model."""
    if server_state(provider):
        return "RUNNING"
    directory = server_directory(provider)
    marker = directory / "starting"
    try:
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        with contextlib.suppress(OSError):
            if time.time() - marker.stat().st_mtime < START_GRACE_SECONDS:
                return "STARTING"
        marker.unlink(missing_ok=True)  # a launch that never became ready
        return start_server(provider)
    env = execution.environment()
    env.update(provider.get("env", {}))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    argv = [provider["python"], "-m", "devguard.embedder", "--state", str(directory)]
    argv += ["--model", provider["model"]]
    if provider.get("cache"):
        argv += ["--cache", provider["cache"]]
    if os.name == "nt":
        detached = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        # Leave the hook's job object so the server survives the hook; retry if not allowed.
        attempts = [detached | subprocess.CREATE_BREAKAWAY_FROM_JOB, detached]
    else:
        attempts = [0]
    with open(directory / "server.log", "ab") as log:
        for index, flags in enumerate(attempts):
            try:
                subprocess.Popen(  # noqa: S603 - trusted interpreter from workflows.json
                    argv,
                    cwd=directory,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=flags,
                    start_new_session=os.name != "nt",
                )
                break
            except OSError:
                if index == len(attempts) - 1:
                    marker.unlink(missing_ok=True)
                    raise
    return "STARTING"


def stop_server(provider: dict) -> str:
    import psutil

    state = server_state(provider)
    if state:
        with contextlib.suppress(psutil.Error):
            process = psutil.Process(state["pid"])
            if "devguard.embedder" in " ".join(process.cmdline()):  # never a reused PID
                process.terminate()
                process.wait(timeout=10)
    directory = server_directory(provider)
    (directory / "server.json").unlink(missing_ok=True)
    (directory / "starting").unlink(missing_ok=True)
    return "STOPPED"


def _request(state: dict, path: str, body: dict | None, timeout: float) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(state['port'])}{path}",
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + state["token"], "Content-Type": "application/json"},
    )
    # Loopback only: an HTTP(S)_PROXY from the hook environment must never see the token.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - fixed loopback URL
        return json.loads(response.read())


def server_status(provider: dict) -> dict:
    state = server_state(provider)
    if state is None:
        starting = (server_directory(provider) / "starting").exists()
        return {"status": "STARTING" if starting else "STOPPED"}
    try:
        return {"status": "RUNNING", **_request(state, "/health", None, provider.get("timeout", 5))}
    except (OSError, ValueError):
        return {"status": "UNAVAILABLE", "pid": state["pid"]}


def embed(provider: dict, texts: list[str]) -> tuple[str, list[list[float]]]:
    """Return (model identity, vectors); a stopped server is launched when autostart is on."""
    state = server_state(provider)
    if state is None:
        if not provider.get("autostart", True):
            raise NotReady("NOT_RUNNING")
        try:
            status = start_server(provider)
        except OSError as exc:
            raise NotReady("NOT_AVAILABLE") from exc
        state = server_state(provider) if status == "RUNNING" else None
        if state is None:
            raise NotReady(status)
    try:
        reply = _request(state, "/embed", {"texts": texts}, provider.get("timeout", 5))
    except (OSError, ValueError) as exc:  # URLError, HTTPError and timeouts are OSError
        raise NotReady("UNAVAILABLE") from exc
    vectors = reply.get("vectors")
    if (
        reply.get("model") != provider["model"]
        or not isinstance(vectors, list)
        or len(vectors) != len(texts)
    ):
        raise NotReady("INVALID_REPLY")
    return f"{reply['model']}:{reply['dims']}", vectors


@contextlib.contextmanager
def _notes(cfg: dict):
    connection = sqlite3.connect(workflow.directory(cfg) / "recall.sqlite3")
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(SCHEMA)
        with connection:
            yield connection
    finally:
        connection.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _anchors(cfg: dict, names: list[str]) -> dict[str, str]:
    root = Path(cfg["root"])
    policy = load_policy(root)
    anchors = {}
    for name in names:
        path = (root / name).resolve()
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else None
        if relative is None or not path.is_file() or policy.sensitive(relative):
            raise ValueError("Anchor must be a non-sensitive file inside the project: " + name)
        anchors[relative] = _digest(path)
    return anchors


def _current(root: Path, anchors: dict[str, str]) -> bool:
    try:
        return all(_digest(root / name) == digest for name, digest in anchors.items())
    except OSError:
        return False


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def select(items: list[dict], provider: dict) -> list[dict]:
    """Threshold, keep items within `max_similarity_gap` of the best one, cap and round."""
    threshold = provider.get("min_similarity", 0.3)
    picked = sorted(
        (item for item in items if item["similarity"] >= threshold),
        key=lambda item: item["similarity"],
        reverse=True,
    )
    max_gap = provider.get("max_similarity_gap")
    if max_gap is not None and picked:
        cutoff = picked[0]["similarity"] - max_gap
        picked = [item for item in picked if item["similarity"] >= cutoff]
    limited = picked[: provider.get("limit", 5)]
    return [{**item, "similarity": round(item["similarity"], 3)} for item in limited]


def add(
    cfg: dict,
    fact: str,
    source: str,
    anchors: list[str] | tuple[str, ...] = (),
    expires_days: float | None = None,
) -> dict:
    fact = " ".join(fact.split())
    if not 0 < len(fact) <= MAX_FACT or not 0 < len(source) <= 200:
        raise ValueError("A note needs a fact of 1..2000 characters and a source")
    if execution.scrub(fact) != fact or execution.scrub(source) != source:
        raise ValueError("Notes must not contain secrets")
    if expires_days is not None and not 0 < expires_days <= 3650:
        raise ValueError("expires_days must be within 0..3650")
    hashes = _anchors(cfg, list(anchors))
    model, [vector] = embed(cfg["recall"], [fact])
    now = time.time()
    note_id = workflow.key(fact + "\0" + source)[:16]
    with _notes(cfg) as db:
        db.execute(
            "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
            (
                note_id,
                fact,
                source,
                json.dumps(hashes, sort_keys=True),
                now,
                now + expires_days * 86400 if expires_days else None,
                None,
                model,
                array("f", vector).tobytes(),
            ),
        )
    return {"status": "RECORDED", "id": note_id, "model": model, "anchors": sorted(hashes)}


def notes(cfg: dict) -> list[dict]:
    with _notes(cfg) as db:
        rows = db.execute(
            "SELECT id,fact,source,anchors,created_at,expires_at,invalid_at,model FROM notes "
            "ORDER BY created_at"
        ).fetchall()
    return [{**dict(row), "anchors": sorted(json.loads(row["anchors"]))} for row in rows]


def invalidate(cfg: dict, note_id: str) -> dict:
    with _notes(cfg) as db:
        changed = db.execute(
            "UPDATE notes SET invalid_at=? WHERE id=? AND invalid_at IS NULL",
            (time.time(), note_id),
        ).rowcount
    return {"status": "INVALIDATED" if changed else "NOT_FOUND", "id": note_id}


def search(cfg: dict, query: str) -> dict:
    provider = cfg["recall"]
    query = execution.scrub(query).strip()[:MAX_QUERY]
    with _notes(cfg) as db:
        rows = db.execute(
            "SELECT id,fact,source,anchors,expires_at,model,vector FROM notes "
            "WHERE invalid_at IS NULL"
        ).fetchall()
    if not rows or not query:
        return {"status": "AVAILABLE", "evidence": []}
    try:
        model, [target] = embed(provider, [query])
    except NotReady as exc:
        return {"status": exc.status}
    root = Path(cfg["root"])
    now = time.time()
    threshold = provider.get("min_similarity", 0.3)
    skipped: Counter[str] = Counter()
    candidates = []
    for row in rows:
        if row["expires_at"] is not None and row["expires_at"] <= now:
            skipped["expired"] += 1
            continue
        if row["model"] != model:
            skipped["model_mismatch"] += 1
            continue
        similarity = cosine(target, array("f", row["vector"]))
        if similarity < threshold:
            continue
        # Current source outranks history: a relevant note whose anchored files changed is dropped.
        if not _current(root, json.loads(row["anchors"])):
            skipped["stale_anchor"] += 1
            continue
        candidates.append(
            {
                "id": row["id"],
                "fact": row["fact"],
                "source": row["source"],
                "similarity": similarity,
            }
        )
    return {
        "status": "AVAILABLE",
        "evidence": select(candidates, provider),
        "skipped": dict(skipped),
    }
