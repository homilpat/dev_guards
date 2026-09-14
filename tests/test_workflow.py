import io
import json
import os
import shutil
import subprocess
import sys

import pytest

from devguard import benchmark, execution, workflow


@pytest.fixture
def project(tmp_path, isolated_home):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([shutil.which("git"), "init", str(root)], check=True, capture_output=True)  # noqa: S603
    (root / "app.py").write_text("value = 1\n")
    cfg = {
        "root": str(root.resolve()),
        "git": shutil.which("git"),
        "max_attempts": 2,
        "checks": [
            {
                "id": "test",
                "argv": [sys.executable, "-c", "import time; time.sleep(.08)"],
                "timeout": 5,
                "memory_mb": 1024,
            }
        ],
    }
    isolated_home.mkdir()
    (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))
    return cfg, root


def test_stop_binds_checks_to_current_source_and_rejects_stale_pass(project):
    cfg, root = project
    workflow.start(cfg, "s")
    assert workflow.gate(cfg, "s")["status"] == "UNCHANGED"
    (root / "app.py").write_text("value = 2\n")
    first = workflow.gate(cfg, "s")
    assert first["status"] == "PASS"
    assert first["checks"][0]["peak_rss_bytes"] > 0
    assert workflow.gate(cfg, "s") == first
    (root / "app.py").write_text("value = 3\n")
    second = workflow.gate(cfg, "s")
    assert second["status"] == "PASS" and second["source_hash"] != first["source_hash"]


def test_failing_check_remembered_and_retry_bounded(project):
    cfg, root = project
    cfg["checks"][0]["argv"] = [
        sys.executable,
        "-c",
        "print('synthetic failure'); raise SystemExit(1)",
    ]
    workflow.start(cfg, "s")
    (root / "app.py").write_text("value = 2\n")
    assert workflow.gate(cfg, "s")["status"] == "FAIL"
    assert workflow.gate(cfg, "s")["status"] == "FAIL"
    assert workflow.gate(cfg, "s")["status"] == "RETRY_LIMIT"
    memories = workflow.memories(cfg)
    assert memories[0]["checks"][0]["status"] == "FAIL"
    assert "synthetic failure" not in json.dumps(memories)


def test_test_deletion_skip_and_dependency_change_fail(project):
    cfg, root = project
    (root / "test_app.py").write_text("def test_one(): pass\n")
    before = workflow.snapshot(cfg)
    (root / "test_app.py").write_text("import pytest\n@pytest.mark.skip\ndef test_two(): pass\n")
    (root / "package.json").write_text("{}")
    findings = workflow.quality(before, workflow.snapshot(cfg), cfg)
    assert "TEST_REMOVAL:test_app.py" in findings
    assert "TEST_SKIP:test_app.py" in findings
    assert "DEPENDENCY_MANIFEST_REVIEW:package.json" in findings


def test_check_cannot_change_source_and_report_success(project):
    cfg, root = project
    cfg["checks"][0]["argv"] = [sys.executable, "-c", "open('app.py','w').write('value=99')"]
    workflow.start(cfg, "s")
    (root / "app.py").write_text("value = 2\n")
    result = workflow.gate(cfg, "s")
    assert result["status"] == "FAIL"
    assert "SOURCE_CHANGED_DURING_CHECK" in result["findings"]


def test_uncovered_language_cannot_borrow_python_success(project):
    cfg, root = project
    cfg["checks"][0]["scopes"] = ["app.py"]
    workflow.start(cfg, "s")
    (root / "app.py").write_text("value=2")
    (root / "other.ts").write_text("export const x = 1")
    assert workflow.gate(cfg, "s")["status"] == "FAIL"


def test_timeout_output_limit_and_redaction(tmp_path):
    result = execution.run(
        [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, timeout=0.1
    )
    assert result["status"] == "TIMEOUT"
    result = execution.run([sys.executable, "-c", "print('x'*20000)"], tmp_path, output_bytes=1000)
    assert result["status"] == "OUTPUT_LIMIT"
    assert "secret-value" not in execution.scrub('password = "secret-value"')


def test_config_inside_project_cannot_grant_workflow_commands(project, monkeypatch):
    cfg, root = project
    monkeypatch.setenv("DEV_GUARD_HOME", str(root))
    (root / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))
    with pytest.raises(ValueError, match="outside"):
        workflow.configuration(root)


def test_stop_without_baseline_does_not_claim_pass(project):
    cfg, root = project
    assert workflow.gate(cfg, "unknown")["status"] == "NOT_INITIALIZED"
    output = io.StringIO()
    workflow.hook("SessionStart", {"session_id": "s"}, root, output)
    assert "additionalContext" in output.getvalue()


def test_stale_lock_from_crashed_check_does_not_block_forever(project):
    cfg, root = project
    workflow.start(cfg, "s")
    lock = workflow.directory(cfg) / "running.lock"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait()
    lock.write_text(str(dead.pid))
    assert workflow.gate(cfg, "s")["status"] == "UNCHANGED"
    assert not lock.exists()
    lock.write_text(str(os.getpid()))
    with pytest.raises(ValueError, match="running"):
        workflow.gate(cfg, "s")


def test_benchmark_requires_verified_candidate_and_preserves_baseline(project):
    cfg, root = project
    cfg["benchmarks"] = {
        "sample": {
            "argv": [sys.executable, "-c", "import time; data=bytearray(1000000); time.sleep(.08)"],
            "repeats": 3,
        }
    }
    baseline = benchmark.run(cfg, "sample", True)
    assert baseline["comparison"] == "BASELINE_RECORDED"
    with pytest.raises(ValueError, match="already exists"):
        benchmark.run(cfg, "sample", True)
    result = benchmark.run(cfg, "sample", False)
    assert result["comparison"] == "UNVERIFIED_CANDIDATE"
