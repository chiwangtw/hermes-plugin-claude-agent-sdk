# Handoff — Hermes Agent × Claude subscription via Agent SDK

Entry point for any new session. Research snapshot 2026-09-18; status section kept current.

## Status (2026-09-18, after grilling)

- **Prototype works end to end** (`hermes chat --provider claude-agent-sdk`): Runtime proposes
  `read_file`, Hermes executes, second call answers with 94% prompt-cache hit; aux tasks
  (title generation) also run on the Runtime. Verified with the real subscription
  (`apiKeySource: none`).
- Code: `__init__.py` (ProviderProfile), `client.py` (OpenAI-shaped client over
  `claude_agent_sdk.ClaudeSDKClient`), `plugin.yaml`. Local dev install is a symlink
  `~/.hermes/plugins/model-providers/claude-agent-sdk -> <this repo>`; the SDK is installed in
  `~/.hermes/hermes-agent/venv` (`uv pip install --python <venv python> claude-agent-sdk`).
- Read in this order: `docs/PRD.md` (scope, v0.1 decisions), `CONTEXT.md` (glossary — use its
  terms), `docs/adr/0001-*.md` (why Tool Bridge, not tool mapping), `docs/UPSTREAM.md` (what
  only a hermes-agent core change can fix), then `client.py`.
- Reference clones (gitignored): `reference/hermes-agent/` (v0.21.x) and
  `reference/claude-agent-sdk-pi/`.

### v0.1 work remaining (order matters; each slice test-first, tests are live against Haiku)

1. Timeout / interrupt must terminate the Runtime subprocess (today a timed-out Turn leaves
   it running). Do this first — every later live test relies on it.
2. Reasoning effort: `ProviderProfile.build_api_kwargs_extras(reasoning_config=...)` →
   top-level kwarg → SDK `effort` (mapping in PRD); declare `supported_reasoning_efforts`.
3. `supports_vision=True`: images inside tool results go straight to the Runtime.
4. Model list (fable-5-1 / opus-5 / sonnet-5 default / haiku-4-5 aux), `plugin.yaml`
   `python_dependencies: [claude-agent-sdk]`, MIT `LICENSE`, README install section
   (`hermes plugins install <github url>` primary, `git clone` alternative).
5. `/code-review` against main, tag `v0.1.0`, rename repo to
   `hermes-plugin-claude-agent-sdk`, post to Discord `#plugins-skills-and-skins` and
   comment on #25267.

### Facts that cost real digging (do not re-derive)

- `permission_mode="dontAsk"` alone denies every tool locally; `can_use_tool` is never
  consulted in that mode. Cut the Turn at the `message_stop` stream event once a
  `tool_use` block was seen (needs `include_partial_messages=True`), before the Runtime spends
  a round-trip reacting to its own denial. Breaking out of `query()` mid-stream raises
  "aclose(): asynchronous generator is already running"; `ClaudeSDKClient` + `async with`
  is clean.
- SDK message stream yields several `AssistantMessage`s per API message (one per block);
  accumulate, dedupe tool_use by id. Usage: `message_start` (input/cache) and
  `message_delta` (output) events, not `AssistantMessage.usage`.
- `system_prompt=None` in the Python SDK means an **empty** system prompt, not the Claude
  Code preset. We pass Hermes' system messages as a custom string.
- The SDK spawns the CLI with `os.environ` inherited: an `ANTHROPIC_API_KEY` in Hermes' env
  would silently switch billing to pay-per-token. Guard = SystemMessage `init`
  `apiKeySource` must be `none`.
- Hermes only sends `extra_body["reasoning"]` to allow-listed hosts; a plugin gets effort
  via `build_api_kwargs_extras`. Ladder: none/minimal/low/medium/high/xhigh/max/ultra.
- `hermes model` / `/model` pickers skip out-of-tree `external_process` providers on
  purpose (`models_catalog_static.py` ~359, `main.py` `_PROVIDER_MODEL_FLOWS`). Supported:
  `--provider`, `/model <m> --provider claude-agent-sdk [--global]`, config.yaml.
- `hermes plugins install <github url>` clones to `~/.hermes/plugins/<repo>/`, installs
  `plugin.yaml` `python_dependencies` into the venv, and provider discovery step 2b imports
  `kind: model-provider` from there.

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

