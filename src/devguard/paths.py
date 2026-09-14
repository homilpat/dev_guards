"""Path classification: sensitive files, Git metadata, guard/agent configuration."""

from fnmatch import fnmatchcase

# M08 §5 examples and requirements M02-FR-01, plus code-agent security/ingestion.py additions.
SENSITIVE_NAMES = frozenset(
    {
        "credentials",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "id_rsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
    }
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
SENSITIVE_DIRECTORIES = frozenset({"secret", "secrets"})
INSTRUCTION_FILES = frozenset({"agents.md", "claude.md"})


def parts(path: str) -> tuple[str, ...]:
    """Case-folded path components; separators and quoting normalized, '.' dropped."""
    cleaned = path.strip().strip("'\"").replace("\\", "/")
    return tuple(p.casefold() for p in cleaned.split("/") if p not in ("", "."))


def sensitive(path: str) -> bool:
    components = parts(path)
    if not components:
        return False
    name = components[-1]
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in SENSITIVE_NAMES
        or name.endswith(SENSITIVE_SUFFIXES)
        or any(c in SENSITIVE_DIRECTORIES for c in components)
    )


def git_metadata(path: str) -> bool:
    return ".git" in parts(path)


def guard_config(path: str) -> bool:
    """Files whose edit would let an agent grant itself permissions (M06 §4)."""
    components = parts(path)
    if not components:
        return False
    joined = "/".join(components)
    return (
        components[-1] == ".dev-guard.toml"
        or ".dev-guard" in components[:-1]
        or ".codex" in components[:-1]
        or joined.endswith((".claude/settings.json", ".claude/settings.local.json"))
        or "/".join(components[-3:-1]).endswith(".claude/hooks")
        or (len(components) >= 2 and components[-2] == "hooks" and ".claude" in components)
    )


def instruction_file(path: str) -> bool:
    components = parts(path)
    return bool(components) and components[-1] in INSTRUCTION_FILES


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    components = parts(path)
    if not components or not patterns:
        return False
    joined = "/".join(components)
    return any(
        fnmatchcase(joined, pattern.casefold()) or fnmatchcase(components[-1], pattern.casefold())
        for pattern in patterns
    )
