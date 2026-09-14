---
name: guardrails
description: How to work with dev-guard blocks and confirmations. Use when a tool call is denied or asks for confirmation with a DG-* rule id, or before touching secrets, tests, git remotes, dependencies or agent configuration.
---

# dev-guard guardrails

dev-guard hooks check every file read, edit and shell command before it runs. Decisions are made by deterministic code, not by this text.

## When a call is blocked (DENY)

- Do not retry the same action through another tool, shell, encoding or path alias. That is a policy violation even if it happens to work.
- Tell the user which rule fired (the `DG-...` id and reason) and what you were trying to achieve.
- Propose a safe alternative, for example a fixture with fake values instead of a real `.env`, or a manual step the user performs.

## When confirmation is requested (ASK)

Explain why the action is needed before the user decides: which package and why, which remote and branch, what a `git reset --hard` would discard.

## Rules that shape how you work

| Rule | Meaning for you |
|---|---|
| DG-SEC-001 | Never read or edit `.env*`, keys, certificates, `credentials*`, `secret(s)/`. Ask the user for the non-secret facts you need. |
| DG-SEC-002 / 004 | Do not write real secrets. Use obviously fake placeholders in tests. |
| DG-CTL-001 / 002 | Do not modify `.git/`, dev-guard policy, `.claude/settings*`, `.claude/hooks/` or `.codex/`. |
| DG-TST-001 / 002 | Fix the code, not the tests. Never add skip/only/disabled markers or delete tests to get green. |
| DG-CMD-* | No network tools, privilege escalation, recursive force delete or host package changes. Package installs and pushes need the user's approval. |

## Working method

1. Understand the dependency or call sites before changing code.
2. Write or identify a failing test first and confirm it fails.
3. Make the smallest change that fixes the cause.
4. Run the tests. Treat memory and performance claims as unproven until measured.
