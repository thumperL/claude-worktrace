# Security

## Reporting vulnerabilities

If you discover a security issue, please report it privately via [GitHub Security Advisories](../../security/advisories/new) rather than opening a public issue.

## What this project does with your data

- **All data stays local.** Worklog entries and preferences are written to `~/Documents/AI/` and `~/.claude/` on your machine. Nothing is sent to external servers.
- **The only network call** is `claude -p --model sonnet` which sends a condensed transcript excerpt (max ~8KB) to the Claude API via your own CLI credentials. This is the same API your Claude Code session already uses.
- **No telemetry, no analytics, no tracking.**

## Hook security

The hooks run Python scripts triggered by Claude Code events. They:
- Only read the transcript file path provided by Claude Code via stdin JSON
- Only write to `~/Documents/AI/` and `~/.claude/`
- Never execute user-provided input as code
- Never access the network directly (only via `claude` CLI subprocess)

## Dependencies

Zero external dependencies. Uses only Python 3.9 stdlib and the `claude` CLI.
