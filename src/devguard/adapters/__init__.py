"""Agent adapters: parse hook payloads into ToolCall and render verdicts back."""

from pathlib import Path

from devguard import paths

MAX_EXISTING_BYTES = 1024 * 1024


def existing_text(cwd: Path, path: str) -> str:
    """Current content of a file about to be replaced, for before/after comparisons.

    Sensitive files are never read here; they are denied by path before content matters.
    """
    if paths.sensitive(path):
        return ""
    target = Path(path) if Path(path).is_absolute() else cwd / path
    try:
        if not target.is_file() or target.stat().st_size > MAX_EXISTING_BYTES:
            return ""
        return target.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""


def required_string(data: dict, *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"tool_input is missing {' / '.join(keys)}")


def optional_string(data: dict, *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""
