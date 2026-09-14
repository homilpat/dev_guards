"""Shell command guardrails (M06 §4 Command Execution Policy).

Commands are split on shell operators outside quotes, tokenized, and each simple command is
checked. Nested shells (`bash -c`, `powershell -Command`, `cmd /c`) and `$(...)` substitutions
are analyzed recursively; anything that cannot be analyzed asks instead of silently passing.
"""

import re
import shlex
from collections.abc import Callable

from devguard import paths
from devguard.model import Decision, Finding
from devguard.rules import finding

MAX_DEPTH = 3

NETWORK = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "telnet",
        "invoke-webrequest",
        "iwr",
        "invoke-restmethod",
        "irm",
        "start-bitstransfer",
    }
)
PRIVILEGE = frozenset(
    {"sudo", "su", "doas", "runas", "mount", "umount", "diskpart", "shutdown", "reboot", "halt"}
)
HOST_PACKAGE_MANAGERS = {
    "apt": frozenset({"install", "remove", "purge", "upgrade", "dist-upgrade"}),
    "apt-get": frozenset({"install", "remove", "purge", "upgrade", "dist-upgrade"}),
    "yum": frozenset({"install", "remove", "update"}),
    "dnf": frozenset({"install", "remove", "upgrade"}),
    "brew": frozenset({"install", "uninstall", "upgrade"}),
    "choco": frozenset({"install", "uninstall", "upgrade"}),
    "winget": frozenset({"install", "uninstall", "upgrade"}),
    "scoop": frozenset({"install", "uninstall"}),
}
# (command, subcommands) pairs that fetch packages from public registries.
PACKAGE_INSTALLS = {
    "pip": frozenset({"install"}),
    "pip3": frozenset({"install"}),
    "pipx": frozenset({"install"}),
    "poetry": frozenset({"add", "install"}),
    "conda": frozenset({"install"}),
    "npm": frozenset({"install", "i", "add", "ci"}),
    "pnpm": frozenset({"add", "install", "i"}),
    "yarn": frozenset({"add", "install"}),
    "bun": frozenset({"add", "install"}),
    "gem": frozenset({"install"}),
    "cargo": frozenset({"install"}),
    "go": frozenset({"install"}),
}
DOWNLOAD_AND_RUN = frozenset({"npx", "bunx", "pnpx"})
ECHO_LIKE = frozenset({"echo", "printf", "write-output", "write-host"})
WRAPPERS = frozenset({"env", "nohup", "time", "exec", "command", "builtin"})
MUTATING = frozenset(
    {
        "rm",
        "del",
        "erase",
        "remove-item",
        "ri",
        "mv",
        "move",
        "move-item",
        "ren",
        "rename",
        "rename-item",
        "cp",
        "copy",
        "copy-item",
        "set-content",
        "add-content",
        "out-file",
        "tee",
        "tee-object",
        "sed",
        "perl",
        "truncate",
        "new-item",
    }
)
_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_REDIRECT = re.compile(r"^(?:\d?>>?|&>)(.*)$")

SensitivePredicate = Callable[[str], bool]


def analyze(command: str, is_sensitive: SensitivePredicate = paths.sensitive) -> list[Finding]:
    return _analyze(command, is_sensitive, 0)


def _analyze(command: str, is_sensitive: SensitivePredicate, depth: int) -> list[Finding]:
    if depth > MAX_DEPTH:
        return [finding("DG-CMD-009", "명령 중첩이 너무 깊어 분석할 수 없음")]
    findings = []
    for match in _SUBSTITUTION.finditer(command):
        findings += _analyze(match.group(1) or match.group(2) or "", is_sensitive, depth + 1)
    for segment in split_segments(command):
        tokens = _tokens(segment)
        if tokens:
            findings += _simple_command(tokens, is_sensitive, depth)
    return findings


def split_segments(command: str) -> list[str]:
    """Split on ; & | && || and newlines that are outside single or double quotes."""
    segments, current, quote, index = [], [], None, 0
    while index < len(command):
        char = command[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char in ";&|\n":
            segments.append("".join(current))
            current = []
            if command[index : index + 2] in ("&&", "||"):
                index += 1
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment for segment in segments if segment.strip()]


def _tokens(segment: str) -> list[str]:
    text = segment.replace("\\", "/")
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    while tokens and (_ASSIGNMENT.fullmatch(tokens[0]) or _name(tokens[0]) in WRAPPERS):
        tokens.pop(0)
    return tokens


def _name(token: str) -> str:
    name = token.strip("'\"").replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _simple_command(tokens: list[str], is_sensitive: SensitivePredicate, depth: int) -> list:
    name, args = _name(tokens[0]), tokens[1:]
    lowered = [arg.casefold() for arg in args]
    findings = []

    if name in PRIVILEGE or name.startswith("mkfs"):
        findings.append(finding("DG-CMD-002", f"권한 상승·시스템 명령: {name}"))
    if name in NETWORK:
        findings.append(finding("DG-CMD-001", f"외부 네트워크 명령: {name}"))
    if _recursive_force_delete(name, lowered):
        findings.append(finding("DG-CMD-003", f"재귀 강제 삭제: {name}"))
    if any("docker.sock" in arg for arg in lowered) or (
        name in ("docker", "podman") and "--privileged" in lowered
    ):
        findings.append(finding("DG-CMD-004", "Docker socket 또는 privileged 컨테이너"))
    if name in HOST_PACKAGE_MANAGERS and any(a in HOST_PACKAGE_MANAGERS[name] for a in lowered):
        findings.append(finding("DG-CMD-008", f"호스트 패키지 관리자 변경: {name}"))
    if _package_install(name, lowered):
        findings.append(finding("DG-CMD-005", f"패키지 설치·다운로드 실행: {name}"))
    findings += _git(name, args)
    findings += _nested(name, args, lowered, is_sensitive, depth)
    findings += _path_arguments(name, args, is_sensitive)
    return findings


def _recursive_force_delete(name: str, lowered: list[str]) -> bool:
    if name == "rm":
        short = "".join(a[1:] for a in lowered if a.startswith("-") and not a.startswith("--"))
        recursive = "r" in short or "--recursive" in lowered
        return recursive and ("f" in short or "--force" in lowered)
    if name in ("remove-item", "ri", "rmdir", "rd", "del", "erase"):
        powershell = any(a.startswith("-rec") for a in lowered) and any(
            a.startswith("-fo") for a in lowered
        )
        return powershell or ("/s" in lowered and "/q" in lowered)
    return False


def _package_install(name: str, lowered: list[str]) -> bool:
    if name in DOWNLOAD_AND_RUN:
        return True
    if name.startswith("python") or name == "py":
        if "-m" in lowered:
            module = lowered[lowered.index("-m") + 1 : lowered.index("-m") + 2]
            return module in (["pip"], ["pip3"]) and "install" in lowered
        return False
    if name == "uv":
        return lowered[:1] == ["add"] or lowered[:2] == ["pip", "install"]
    if name == "yarn" and not [a for a in lowered if not a.startswith("-")]:
        return True
    subcommands = PACKAGE_INSTALLS.get(name)
    first = next((a for a in lowered if not a.startswith("-")), None)
    return subcommands is not None and first in subcommands


def _git(name: str, args: list[str]) -> list[Finding]:
    if name == "gh":
        pair = tuple(a.casefold() for a in args[:2])
        if pair in {("pr", "merge"), ("release", "create"), ("repo", "delete"), ("repo", "create")}:
            return [finding("DG-CMD-006", f"GitHub 원격 변경: gh {' '.join(pair)}")]
        return []
    if name != "git":
        return []
    rest = _git_arguments(args)
    if not rest:
        return []
    sub, options = rest[0].casefold(), [a.casefold() for a in rest[1:]]
    if sub == "push" or (sub == "remote" and options[:1] in (["add"], ["set-url"])):
        return [finding("DG-CMD-006", f"원격 저장소 변경: git {sub}")]
    discards = (
        (sub == "reset" and "--hard" in options)
        or (sub == "clean" and any(o.startswith("-") and "f" in o for o in options))
        or (sub in ("checkout", "restore") and "." in options)
    )
    if discards:
        return [finding("DG-CMD-007", f"작업 내용 폐기 가능: git {sub}")]
    return []


def _git_arguments(args: list[str]) -> list[str]:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return args[index:]
    return []


def _nested(name, args, lowered, is_sensitive, depth) -> list[Finding]:
    if name in ("iex", "invoke-expression", "eval"):
        return [finding("DG-CMD-009", f"동적 실행: {name}")]
    if name in ("bash", "sh", "zsh", "dash", "ksh") and "-c" in args:
        position = args.index("-c") + 1
        inner = args[position] if position < len(args) else ""
        return _analyze(inner, is_sensitive, depth + 1)
    if name == "cmd":
        for flag in ("/c", "/k"):
            if flag in lowered:
                return _analyze(" ".join(args[lowered.index(flag) + 1 :]), is_sensitive, depth + 1)
    if name in ("powershell", "pwsh"):
        if any(a in ("-encodedcommand", "-enc", "-ec", "-e") for a in lowered):
            return [finding("DG-CMD-009", "인코딩된 PowerShell 명령")]
        for flag in ("-command", "-c"):
            if flag in lowered:
                inner = " ".join(args[lowered.index(flag) + 1 :])
                return _analyze(inner, is_sensitive, depth + 1)
    return []


def _path_arguments(name: str, args: list[str], is_sensitive: SensitivePredicate) -> list:
    findings = []
    write_targets = []
    candidates = []
    after_redirect = False
    for arg in args:
        if after_redirect:
            write_targets.append(arg)
            after_redirect = False
            continue
        redirect = _REDIRECT.match(arg)
        if redirect:
            if redirect.group(1):
                write_targets.append(redirect.group(1))
            else:
                after_redirect = True
            continue
        candidates.append(arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg)

    for target in write_targets:
        if is_sensitive(target):
            findings.append(finding("DG-SEC-001", f"민감 파일에 쓰기: {target}"))
        if paths.guard_config(target):
            findings.append(finding("DG-CTL-002", f"가드레일·에이전트 설정에 쓰기: {target}"))
    for candidate in candidates:
        if is_sensitive(candidate):
            decision = Decision.ASK if name in ECHO_LIKE else None
            findings.append(finding("DG-SEC-001", f"민감 파일 경로 사용: {candidate}", decision))
        if name in MUTATING and paths.guard_config(candidate):
            findings.append(finding("DG-CTL-002", f"가드레일·에이전트 설정 변경: {candidate}"))
    return findings
