# Handoff — Hermes Agent × Claude subscription via Agent SDK

Research snapshot as of 2026-09-18. Read this before touching code.

## Decision

Build a **standalone** Hermes model-provider plugin (not an in-tree PR).
Route Hermes LLM calls through the official Claude Agent SDK so a Claude
Pro/Max subscription is billed against plan usage, not "extra usage" credits.

## Why not the existing Hermes OAuth path

- Hermes `anthropic` provider with OAuth (`hermes auth add anthropic --type oauth`)
  calls `api.anthropic.com/v1/messages` directly with the `sk-ant-oat01` token.
  Anthropic bills that as pay-per-token "extra usage"; base Max allowance is never
  consumed; Pro cannot use it at all. Official docs say so; tracked in
  NousResearch/hermes-agent#40014 (open, P2).
- `agent/anthropic_adapter.py` already spoofs Claude Code identity (system prefix,
  user-agent, tool-name aliases to dodge Anthropic's billing classifier). Do not go
  further down that road — it is the path Anthropic's 2026-02-20 policy bans, and
  it risks the account.

## Why not an in-tree PR to hermes-agent (#25267)

- Maintainer policy `in-tree-provider-integration` (AGENTS.md): third-party vendor
  provider integrations must ship as standalone plugin repos under
  `~/.hermes/plugins/` or a pip entry point. `hermes-sweeper` bot auto-closes
  in-tree attempts: #71763 and #33999 both closed `not_planned`.
- Five open PRs already queue for the same thing: #56413, #65982 (41 commits,
  86 comments, stuck on `needs-decision`), #80469, #105863, #81375.
- Only existing standalone: `nnnet/hermes-plugin-claude-agent-sdk` — bound to
  their private Meridian proxy, not general-purpose. A general plugin is a real gap.

## Sanctioned billing path (Anthropic side)

- Help Center "Use the Claude Agent SDK with your Claude plan": the June 15 2026
  separate-credit change is **paused**; Agent SDK, `claude -p`, and third-party
  apps built on the Agent SDK currently draw from the subscription's normal usage
  limits. Re-verify before shipping; Anthropic said they will give notice before
  changing it again.

## Architecture (proven by `prateekmedia/claude-agent-sdk-pi`, ★117)

```
Hermes loop (owns tools, context, memory)
   │  messages + tool schemas
   ▼
plugin client ── claude_agent_sdk.query() ──► Claude Code runtime (subscription OAuth)
   ▲
   │  tool_use proposals only — Claude Code is DENIED tool execution
   └── map back to Hermes tool names → Hermes executes → results fed back
```

Key rules:
- Claude Code proposes tool calls; Hermes executes. Avoids two competing agent loops.
- Map built-in names both ways (Read/Write/Edit/Bash/Grep/Glob ↔ Hermes equivalents).
- Expose remaining Hermes tools to the SDK via an in-process MCP server
  (`mcp__hermes__<name>`), map back on return.
- Pass `--strict-mcp-config` and control `settingSources` so `~/.claude.json`
  MCP schemas do not bloat every request.
- Hermes-side entry point: subclass `ProviderProfile`, override `create_client()`
  (see `plugins/model-providers/copilot-acp/__init__.py`, 64 lines) with
  `auth_type="external_process"`. Client body: model on
  `agent/copilot_acp_client.py` (476 lines) and
  `agent/transports/hermes_tools_mcp_server.py` (Hermes-tools-as-MCP precedent
  for the Codex app-server runtime).

Effort estimate: 300–500 lines Python for the client; tool mapping is the bulk.

## Reference material

- `reference/hermes-agent/` — shallow clone of NousResearch/hermes-agent at
  2026-09-18 (gitignored; re-clone with
  `git clone --depth 1 https://github.com/NousResearch/hermes-agent.git reference/hermes-agent`).
  Files to read first:
  - `plugins/model-providers/README.md`
  - `plugins/model-providers/copilot-acp/__init__.py`
  - `agent/copilot_acp_client.py`
  - `agent/acp_openai_bridge.py`
  - `agent/transports/hermes_tools_mcp_server.py`
  - `agent/anthropic_adapter.py` (what NOT to replicate)
  - `AGENTS.md` (Contribution Rubric, plugin policy)
- Sibling project to port from: https://github.com/prateekmedia/claude-agent-sdk-pi
  (`pi install npm:claude-agent-sdk-pi`).

## Links

- Hermes provider docs: https://hermes-agent.nousresearch.com/docs/integrations/providers
- Issue #25267 (feature request): https://github.com/NousResearch/hermes-agent/issues/25267
- Issue #40014 (OAuth billed as pay-per-token): https://github.com/NousResearch/hermes-agent/issues/40014
- PR #65982 (largest open attempt): https://github.com/NousResearch/hermes-agent/pull/65982
- PR #71763 / #33999 (closed by policy): https://github.com/NousResearch/hermes-agent/pull/71763 , https://github.com/NousResearch/hermes-agent/pull/33999
- Anthropic Help Center: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- Anthropic policy news (2026-02-20): https://alternativeto.net/news/2026/2/anthropic-officially-bans-using-subscription-authentication-for-third-party-claude-use
- nnnet plugin (deployment-specific): https://github.com/nnnet/hermes-plugin-claude-agent-sdk

## Next steps

1. Chi fills `docs/PRD.md`.
2. Read the reference files above; then scaffold `plugin.yaml` + `__init__.py`.
3. Prototype: one round-trip with tools denied in Claude Code, Hermes executing.
4. Publish to Discord `#plugins-skills-and-skins`; comment on #25267 with the link.
