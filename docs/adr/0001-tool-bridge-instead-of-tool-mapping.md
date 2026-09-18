---
status: accepted
date: 2026-09-18
---

# Expose every Hermes tool through the Tool Bridge; do not map Claude Code built-in tools

The reference design (`claude-agent-sdk-pi`, and `docs/HANDOFF.md`) keeps Claude Code's
built-in tools (Read, Write, Edit, Bash, Grep, Glob) enabled and translates names and
arguments both ways between them and the host's tools. We decided instead to disable all
built-in tools (`tools=[]`) and expose every Hermes tool, with its real JSON Schema, through
one in-process MCP server (`mcp__hermes__<name>`), stripping the prefix on the way back.

## Why

- Hermes tools do not mirror Claude Code's: `terminal`, `read_file`, `patch`,
  `search_files` have different parameters (`patch` has `mode`/`replace_all`, `terminal` has
  `background`/`pty`/`workdir`). A mapping layer would have to translate arguments in both
  directions and keep up with both sides' schema changes; a wrong translation is a silent
  bug the model cannot see.
- Hermes replays history with its own tool names; with a mapping, every replay would also
  have to rewrite historical tool calls into Claude Code names, or the model sees two names
  for one tool.
- The Python SDK's `tool()` accepts a JSON Schema dict, so the Runtime sees the exact
  parameter docs Hermes' own models see. (`claude-agent-sdk-pi` had to pass an empty schema
  because the TypeScript SDK rejects plain JSON Schema — the mapping there was partly a
  workaround.)
- One code path for all 25 tools, including `tool_search` / `tool_call`, instead of two.

## Consequences

- Claude models are more practised with `Read`/`Bash` than with `read_file`/`terminal`.
  We accept a possible small quality cost; if it shows up in practice, the fix is a
  Hermes-side prompt hint, not a mapping layer.
- Claude Code's own tool-use system-prompt guidance is not loaded (we pass a custom system
  prompt), so the Runtime behaves as a plain model behind Hermes' prompt — which is the
  intent: Hermes owns the agent loop.
- Reversing this means adding argument translation for six tools and a history rewriter;
  feasible, but it should be driven by measured quality loss, not by the reference design.
