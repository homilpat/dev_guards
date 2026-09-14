"""Repeated process measurements; compare candidates only after source-bound tests pass."""

import json
import platform
import statistics
from pathlib import Path

from devguard import execution, workflow


def measure(cfg: dict, profile: str) -> dict:
    specification = cfg.get("benchmarks", {}).get(profile)
    if not specification:
        raise ValueError("Benchmark must be registered in trusted workflows.json")
    before = workflow.snapshot(cfg)["hash"]
    argv = specification["argv"]
    if not Path(argv[0]).is_absolute():
        raise ValueError("Benchmark executable must be absolute")
    cwd = (Path(cfg["root"]) / specification.get("cwd", ".")).resolve()
    if not cwd.is_relative_to(Path(cfg["root"])):
        raise ValueError("Benchmark cwd escapes project")
    count = specification.get("repeats", 3)
    if not 3 <= count <= 9:
        raise ValueError("Benchmark repeats must be 3..9")
    results = [
        execution.run(
            argv,
            cwd,
            specification.get("timeout", 120),
            specification.get("memory_mb", 2048),
            specification.get("env"),
        )
        for _ in range(count + 1)
    ]  # first sample is warmup
    status = "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL"
    if workflow.snapshot(cfg)["hash"] != before:
        status = "SOURCE_CHANGED"
    samples = results[1:]
    metrics = {
        k: statistics.median(r.get(k, 0) for r in samples)
        for k in ("wall_seconds", "cpu_seconds", "peak_rss_bytes")
    }
    return {
        "status": status,
        "source_hash": before,
        "profile": profile,
        "spec_hash": workflow.key(json.dumps(specification, sort_keys=True)),
        "environment": platform.platform() + "|" + str(Path(argv[0]).resolve()),
        "repeats": count,
        "metrics": metrics,
        "sampling_note": "20ms sampled process-tree RSS/CPU; not Python heap or kernel peak",
    }


def run(cfg: dict, profile: str, baseline: bool) -> dict:
    path = workflow.directory(cfg) / ("benchmark-" + workflow.key(profile) + ".json")
    result = measure(cfg, profile)
    if result["status"] != "PASS":
        return result
    if baseline:
        if path.exists():
            raise ValueError("Baseline already exists; choose a new registered benchmark ID")
        workflow.save(path, result)
        return {**result, "comparison": "BASELINE_RECORDED"}
    if not path.exists():
        return {**result, "comparison": "BASELINE_MISSING"}
    old = json.loads(path.read_text(encoding="utf-8"))
    latest = workflow.directory(cfg) / "latest.json"
    verification = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}
    if (
        verification.get("status") != "PASS"
        or verification.get("source_hash") != result["source_hash"]
    ):
        return {**result, "comparison": "UNVERIFIED_CANDIDATE"}
    if old["spec_hash"] != result["spec_hash"] or old["environment"] != result["environment"]:
        return {**result, "comparison": "ENVIRONMENT_MISMATCH"}
    ratios = {k: result["metrics"][k] / value for k, value in old["metrics"].items() if value > 0}
    if len(ratios) != 3:
        return {**result, "comparison": "INCONCLUSIVE_MEASUREMENT"}
    target = cfg["benchmarks"][profile].get("metric", "peak_rss_bytes")
    improved = ratios[target] <= 0.95 and all(v <= 1.10 for v in ratios.values())
    return {**result, "ratios": ratios, "comparison": "IMPROVED" if improved else "NOT_IMPROVED"}
