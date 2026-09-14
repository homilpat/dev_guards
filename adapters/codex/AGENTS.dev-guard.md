<!-- Append to ~/.codex/AGENTS.md or a repository AGENTS.md. Enforcement is done by hooks and rules. -->

## dev-guard guardrails

dev-guard hooks check shell commands and `apply_patch` edits before they run; blocks carry a `DG-*` rule id.

- If a call is blocked, do not retry it through another command, encoding or path alias. Report the rule id and propose a safe alternative.
- Never read or edit `.env*`, private keys, certificates, `credentials*` or `secret(s)/`. Use fake placeholders in tests.
- Do not modify `.git/`, `.dev-guard.toml`, `~/.dev-guard/`, `.codex/` or `.claude/settings*`.
- Fix the code, not the tests: no skip/only/disabled markers, no deleting tests to get green.
- No network tools, privilege escalation, recursive force delete or host package manager changes. Package installs, `git push` and history-discarding git commands need the user's explicit approval.
- Write or identify a failing test first, make the smallest fix, run the tests, and measure before claiming memory or performance gains.
