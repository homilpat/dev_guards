import io
import json
import sys

import pytest

from devguard import context, workflow

# Stands in for `potpie --json search`: records its argv and prints a large ranked result.
FAKE_POTPIE = r"""
import json, sys
open(sys.argv[1], "w").write(json.dumps(sys.argv[2:]))
related = 'pytest needs --basetemp; password = "secret-value"'
items = [
    {"include": "docs", "payload": {"fact": related, "source_refs": ["record:1"],
     "properties": {"semantic_similarity": 0.61}}},
    {"include": "docs", "payload": {"fact": "unrelated scoped note", "source_refs": ["record:2"],
     "properties": {"semantic_similarity": 0.01}}},
]
print("warning: noise before the json", file=sys.stderr, flush=True)
print(json.dumps({"items": items, "padding": "x" * 20000}))
"""


def provider(tmp_path, **extra):
    return {"argv": [sys.executable, "-c", FAKE_POTPIE, str(tmp_path / "argv.json")], **extra}


def test_potpie_evidence_is_filtered_compact_and_redacted(tmp_path):
    cfg = {"root": str(tmp_path), "potpie": provider(tmp_path, pot="pot_x")}
    result = context.query(cfg, "why does pytest fail with permission errors")
    potpie = result["potpie"]
    assert potpie["status"] == "AVAILABLE"  # 20 KB output and a stderr line still parse
    assert [e["source"] for e in potpie["evidence"]] == ["record:1"]
    assert "secret-value" not in json.dumps(potpie)
    args = json.loads((tmp_path / "argv.json").read_text())
    assert args[:4] == ["--json", "search", "--pot", "pot_x"]
    assert args[args.index("--include") + 1] == context.INCLUDES
    assert args[-2:] == ["--", "why does pytest fail with permission errors"]


def test_potpie_evidence_filters_items_too_far_below_the_best_match():
    data = {
        "items": [
            {
                "payload": {
                    "fact": fact,
                    "source_refs": [source],
                    "properties": {"semantic_similarity": similarity},
                }
            }
            for fact, source, similarity in (
                ("best", "record:1", 0.61),
                ("also useful", "record:2", 0.58),
                ("threshold-only distractor", "record:3", 0.55),
            )
        ]
    }

    result = context.evidence(data, {"min_similarity": 0.5, "max_similarity_gap": 0.04, "limit": 5})

    assert [item["source"] for item in result] == ["record:1", "record:2"]
    assert [item["similarity"] for item in result] == [0.61, 0.58]


def test_prompt_is_silent_without_relevant_context(tmp_path, isolated_home):
    root = tmp_path / "repo"
    root.mkdir()
    isolated_home.mkdir()

    def register(**extra):
        cfg = {"root": str(root.resolve()), "potpie": provider(tmp_path, **extra)}
        (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))

    register(min_similarity=0.9)
    output = io.StringIO()
    context.prompt({"prompt": "anything"}, root, output)
    assert output.getvalue() == ""
    register()
    context.prompt({"prompt": "pytest"}, root, output)
    injected = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "basetemp" in injected and "unrelated scoped note" not in injected


def test_relative_potpie_executable_is_rejected(tmp_path, isolated_home):
    root = tmp_path / "repo"
    root.mkdir()
    isolated_home.mkdir()
    cfg = {"root": str(root), "potpie": {"argv": ["potpie"]}}
    (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))
    with pytest.raises(ValueError, match="absolute"):
        workflow.configuration(root)


@pytest.mark.parametrize("value", [True, -0.1, 1.1, "0.04"])
def test_invalid_potpie_similarity_gap_is_rejected(tmp_path, isolated_home, value):
    root = tmp_path / "repo"
    root.mkdir()
    isolated_home.mkdir()
    cfg = {
        "root": str(root),
        "potpie": {"argv": [sys.executable], "max_similarity_gap": value},
    }
    (isolated_home / "workflows.json").write_text(json.dumps({"version": 1, "projects": [cfg]}))

    with pytest.raises(ValueError, match="max_similarity_gap"):
        workflow.configuration(root)
