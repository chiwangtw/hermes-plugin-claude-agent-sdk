# hermes-claude-agent-sdk

Standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) model-provider
plugin: run Hermes on a Claude Pro/Max subscription through the official
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

Status: **prototype** — one Hermes turn round-trips through the SDK, the Runtime
proposes Hermes tools, Hermes executes them. See `docs/PRD.md` for scope and
`docs/HANDOFF.md` for the research behind the design.

## How it works

```
Hermes loop (owns tools, context, memory)
   │  messages + Hermes tool schemas
   ▼
ClaudeAgentSDKClient ── claude_agent_sdk ──► Claude Code Runtime (your subscription login)
   ▲                                          • Built-in tools disabled (tools=[])
   │  tool_use proposals only                 • Hermes tools visible via in-process MCP
   └── Hermes executes → results fed back     • permission_mode=dontAsk → never executes
```

- The Runtime never executes a tool. Every proposal comes back to Hermes as an
  OpenAI-style `tool_calls` entry and Hermes runs it with its own toolset.
- The Runtime refuses to run if it would authenticate with an API key
  (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`): that path bills pay-per-token,
  not your subscription.
- Nothing in Hermes core is modified; the plugin lives entirely under
  `~/.hermes/plugins/model-providers/`.

## Install

Prerequisites (once per person):

1. Claude Code installed and logged in with your own Pro/Max subscription:
   `claude login`. Do not set `ANTHROPIC_API_KEY` in the shell that runs Hermes.
2. The Python SDK in Hermes' virtualenv (the SDK bundles its own Claude Code
   binary, ~90 MB):

   ```
   uv pip install --python ~/.hermes/hermes-agent/venv/bin/python claude-agent-sdk
   ```

Plugin:

```
git clone https://github.com/<owner>/hermes-claude-agent-sdk \
  ~/.hermes/plugins/model-providers/claude-agent-sdk
```

## Selecting the provider and model

Three ways, all of which go through Hermes' own model-switch pipeline:

```
# one session
hermes chat --provider claude-agent-sdk -m claude-sonnet-5

# inside a chat, this session only / persisted to config.yaml
/model claude-sonnet-5 --provider claude-agent-sdk
/model claude-sonnet-5 --provider claude-agent-sdk --global

# or edit ~/.hermes/config.yaml directly
model:
  provider: claude-agent-sdk
  default: claude-sonnet-5
  base_url: claude-agent-sdk://local
  api_mode: chat_completions
```

Models: `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`
(auxiliary tasks — context compression, summaries, titles — default to Haiku 4.5
and also run on your subscription).

Known limitation: the provider does **not** appear in the interactive
`hermes model` picker or in the `/model` list, and the `claude-agent-sdk:<model>`
shorthand is not recognised. Hermes core deliberately skips out-of-tree
`external_process` providers there; see `docs/UPSTREAM.md` for the core change
that would lift this. The `--provider` forms above are fully supported.

## Layout

```
plugin.yaml   # manifest (kind: model-provider)
__init__.py   # ProviderProfile registration
client.py     # OpenAI-shaped client over claude_agent_sdk
CONTEXT.md    # glossary
docs/         # PRD, handoff notes
```
