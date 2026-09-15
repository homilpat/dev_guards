import io
import json
import math
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from devguard import cli, context, embedder, recall, workflow

KEYWORDS = ("pytest", "import", "api", "lunch")


def fake_vector(text):
    lowered = text.lower()
    raw = [lowered.count(word) + 0.01 for word in KEYWORDS]
    norm = math.sqrt(sum(value * value for value in raw))
    return [value / norm for value in raw]


def fake_embed(model):
    return lambda provider, texts: (model, [fake_vector(text) for text in texts])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(recall, "embed", fake_embed("fake:4"))
    provider = {
        "python": sys.executable,
        "model": "fake",
        "min_similarity": 0.5,
        "max_similarity_gap": 0.2,
    }
    return {"root": str(root.resolve()), "recall": provider}


def test_search_ranks_related_notes_and_drops_notes_whose_anchor_changed(cfg):
    root = Path(cfg["root"])
    (root / "runner.py").write_text("pytest --basetemp\n")
    anchored = recall.add(cfg, "run pytest with --basetemp", "runbook", ["runner.py"])
    recall.add(cfg, "pytest needs PYTHONPATH", "runbook")
    recall.add(cfg, "lunch menu ideas", "chat")
    assert anchored["anchors"] == ["runner.py"]

    found = recall.search(cfg, "how do I run pytest")
    assert found["status"] == "AVAILABLE"
    assert {e["fact"] for e in found["evidence"]} == {
        "run pytest with --basetemp",
        "pytest needs PYTHONPATH",
    }

    (root / "runner.py").write_text("pytest -x\n")
    stale = recall.search(cfg, "how do I run pytest")
    assert [e["fact"] for e in stale["evidence"]] == ["pytest needs PYTHONPATH"]
    assert stale["skipped"] == {"stale_anchor": 1}


def test_invalidated_other_model_and_expired_notes_are_not_scored(cfg, monkeypatch):
    kept = recall.add(cfg, "pytest note", "runbook", expires_days=1)
    other = recall.add(cfg, "second pytest note", "runbook")
    assert recall.invalidate(cfg, other["id"])["status"] == "INVALIDATED"
    assert recall.invalidate(cfg, other["id"])["status"] == "NOT_FOUND"
    assert [e["id"] for e in recall.search(cfg, "pytest")["evidence"]] == [kept["id"]]

    monkeypatch.setattr(recall, "embed", fake_embed("other:4"))
    mismatch = recall.search(cfg, "pytest")
    assert mismatch == {"status": "AVAILABLE", "evidence": [], "skipped": {"model_mismatch": 1}}

    future = recall.time.time() + 2 * 86400
    monkeypatch.setattr(recall, "time", SimpleNamespace(time=lambda: future))
    assert recall.search(cfg, "pytest")["skipped"] == {"expired": 1}


@pytest.mark.parametrize(
    "fact, anchors",
    [
        ('password = "secret-value" for pytest', []),
        ("pytest note", ["../outside.py"]),
        ("pytest note", ["missing.py"]),
        ("pytest note", ["id_rsa"]),
        ("   ", []),
    ],
)
def test_unsafe_or_empty_notes_are_refused(cfg, fact, anchors):
    root = Path(cfg["root"])
    (root / "id_rsa").write_text("key")
    (root.parent / "outside.py").write_text("x")
    with pytest.raises(ValueError):
        recall.add(cfg, fact, "runbook", anchors)
    assert recall.notes(cfg) == []


def test_prompt_injects_recall_evidence_and_stays_quiet_while_the_server_starts(
    cfg, isolated_home, monkeypatch
):
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))
    recall.add(cfg, "run pytest with --basetemp", "runbook")
    output = io.StringIO()
    context.prompt({"prompt": "pytest please"}, Path(cfg["root"]), output)
    injected = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "--basetemp" in injected

    def starting(provider, texts):
        raise recall.NotReady("STARTING")

    monkeypatch.setattr(recall, "embed", starting)
    assert recall.search(cfg, "pytest") == {"status": "STARTING"}
    quiet = io.StringIO()
    context.prompt({"prompt": "pytest please"}, Path(cfg["root"]), quiet)
    assert quiet.getvalue() == ""


def test_server_requires_its_token_and_bounds_requests():
    server = embedder.make_server(lambda texts: [[1.0, 0.0] for _ in texts], "fake", 2, "token-1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        provider = {"python": sys.executable, "model": "fake", "timeout": 5}
        state = {"pid": os.getpid(), "port": port, "token": "token-1", "model": "fake", "dims": 2}
        embedder.write_state(recall.server_directory(provider), state)
        assert recall.embed(provider, ["hello"]) == ("fake:2", [[1.0, 0.0]])
        assert recall.server_status(provider)["status"] == "RUNNING"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for token, body, code in (
            ("wrong", {"texts": ["x"]}, 401),
            ("token-1", {"texts": []}, 400),
            ("token-1", {"texts": ["x" * 4001]}, 400),
        ):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/embed",
                data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + token},
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                opener.open(request, timeout=5)
            assert error.value.code == code
            error.value.close()
    finally:
        server.shutdown()
        server.server_close()


def test_autostart_launches_one_detached_server_without_credentials(monkeypatch):
    launches = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            launches.append((argv, kwargs))

    monkeypatch.setattr(recall.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("SERVICE_API_TOKEN", "must-not-leak")
    provider = {"python": sys.executable, "model": "fake", "env": {"HF_HUB_OFFLINE": "1"}}
    for _ in range(2):
        with pytest.raises(recall.NotReady) as error:
            recall.embed(provider, ["hello"])
        assert error.value.status == "STARTING"
    assert len(launches) == 1
    argv, kwargs = launches[0]
    assert argv[1:3] == ["-m", "devguard.embedder"]
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
    assert "SERVICE_API_TOKEN" not in kwargs["env"]
    assert Path(kwargs["env"]["PYTHONPATH"], "devguard", "embedder.py").is_file()
    with pytest.raises(recall.NotReady) as error:
        recall.embed({**provider, "model": "other", "autostart": False}, ["hello"])
    assert error.value.status == "NOT_RUNNING"


@pytest.mark.parametrize(
    "provider, message",
    [
        ({"python": "python", "model": "m"}, "absolute"),
        ({"python": sys.executable}, "model"),
        ({"python": sys.executable, "model": "m", "min_similarity": 2}, "min_similarity"),
    ],
)
def test_invalid_recall_configuration_is_rejected(tmp_path, isolated_home, provider, message):
    root = tmp_path / "repo"
    root.mkdir()
    isolated_home.mkdir()
    cfg = {"root": str(root), "recall": provider}
    (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))
    with pytest.raises(ValueError, match=message):
        workflow.configuration(root)


def test_cli_records_lists_and_searches_notes(cfg, isolated_home, capsys):
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))
    root = cfg["root"]
    assert cli.main(["recall", "add", "--cwd", root, "--fact", "pytest needs --basetemp"]) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert cli.main(["recall", "search", "--cwd", root, "--query", "pytest"]) == 0
    assert [e["id"] for e in json.loads(capsys.readouterr().out)["evidence"]] == [recorded["id"]]
    assert cli.main(["recall", "list", "--cwd", root]) == 0
    assert json.loads(capsys.readouterr().out)["notes"][0]["source"] == "manual"
