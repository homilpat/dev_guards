"""Policy loading. Authority lives outside repositories (M06 §4).

- User policy `~/.dev-guard/policy.toml` (or `$DEV_GUARD_HOME/policy.toml`) may override rule
  decisions, add sensitive or allowed paths and mark local-only roots.
- Repository `.dev-guard.toml` may only tighten: classify the repository as local-only or add
  sensitive paths. Anything else is rejected so a repository cannot grant itself permissions.
"""

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from devguard import paths
from devguard.model import Decision, Finding
from devguard.rules import RULES

REPO_FILE = ".dev-guard.toml"
USER_KEYS = frozenset({"overrides", "sensitive_paths", "allow_paths", "local_only_roots"})
REPO_KEYS = frozenset({"classification", "sensitive_paths"})
CLASSIFICATIONS = frozenset({"normal", "local-only"})


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Policy:
    home: Path
    sensitive_patterns: tuple[str, ...] = ()
    allow_patterns: tuple[str, ...] = ()
    local_only_roots: tuple[Path, ...] = ()
    overrides: dict[str, Decision] = field(default_factory=dict)

    def sensitive(self, path: str) -> bool:
        if paths.matches_any(path, self.allow_patterns):
            return False
        return paths.sensitive(path) or paths.matches_any(path, self.sensitive_patterns)

    def is_local_only(self, cwd: Path) -> bool:
        current = cwd.resolve()
        return any(
            current == root or current.is_relative_to(root) for root in self.local_only_roots
        )

    def apply(self, item: Finding) -> Finding:
        decision = self.overrides.get(item.rule_id)
        return item if decision is None else replace(item, decision=decision)


def home() -> Path:
    return Path(os.environ.get("DEV_GUARD_HOME") or Path.home() / ".dev-guard")


def load_policy(cwd: Path) -> Policy:
    root = home()
    user = _read(root / "policy.toml", USER_KEYS)
    overrides = {}
    for rule_id, value in _table(user, "overrides").items():
        if rule_id not in RULES:
            raise PolicyError(f"unknown rule id in overrides: {rule_id}")
        overrides[rule_id] = _decision(value)
    sensitive = list(_strings(user, "sensitive_paths"))
    roots = [Path(value).expanduser() for value in _strings(user, "local_only_roots")]

    repo_file = find_repo_config(cwd)
    if repo_file is not None:
        repo = _read(repo_file, REPO_KEYS)
        classification = repo.get("classification", "normal")
        if classification not in CLASSIFICATIONS:
            raise PolicyError(
                f"{repo_file}: classification must be one of {sorted(CLASSIFICATIONS)}"
            )
        if classification == "local-only":
            roots.append(repo_file.parent)
        sensitive.extend(_strings(repo, "sensitive_paths"))

    return Policy(
        home=root,
        sensitive_patterns=tuple(sensitive),
        allow_patterns=_strings(user, "allow_paths"),
        local_only_roots=tuple(r.resolve() for r in roots),
        overrides=overrides,
    )


def find_repo_config(cwd: Path) -> Path | None:
    start = cwd.resolve()
    for directory in (start, *start.parents):
        candidate = directory / REPO_FILE
        if candidate.is_file():
            return candidate
    return None


def _read(path: Path, allowed: frozenset[str]) -> dict:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"{path}: cannot read policy ({exc})") from exc
    unknown = set(data) - allowed
    if unknown:
        raise PolicyError(f"{path}: fields not allowed here: {sorted(unknown)}")
    return data


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise PolicyError(f"{key} must be a table")
    return value


def _strings(data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PolicyError(f"{key} must be a list of non-empty strings")
    return tuple(value)


def _decision(value: object) -> Decision:
    if not isinstance(value, str) or value.upper() not in Decision.__members__:
        raise PolicyError(f"decision must be allow, ask or deny, not {value!r}")
    return Decision[value.upper()]
