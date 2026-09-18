# hermes-claude-agent-sdk

Standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) model-provider
plugin: run Hermes on a Claude Pro/Max subscription through the official
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

Status: **v0.1** — Hermes turns round-trip through the SDK, the Runtime proposes
Hermes tools, Hermes executes them; effort levels and images are forwarded. See
`docs/PRD.md` for scope and `docs/HANDOFF.md` for the research behind the design.

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

Prerequisite, once per person: Claude Code logged in with **your own** Pro/Max
subscription (`claude login`). Do not set `ANTHROPIC_API_KEY` in the shell that
runs Hermes — the plugin refuses to run on an API key.

```
hermes plugins install https://github.com/<owner>/hermes-plugin-claude-agent-sdk
```

That clones the plugin into `~/.hermes/plugins/` and installs `claude-agent-sdk`
(which bundles its own Claude Code binary, ~90 MB) into Hermes' virtualenv.

Hermes scans every community plugin before installing and will report a
*caution* verdict for this one: the findings are prose matches in `README.md`,
`docs/` and `tests/` (words like "git clone", "pip install", "ANTHROPIC_API_KEY",
a `ps` call in the test harness). Review them, then confirm at the prompt; in a
non-interactive shell pass `--force`. No `hermes plugins enable` step is needed —
model-provider plugins are picked up by the provider registry directly.

Manual alternative:

```
git clone https://github.com/<owner>/hermes-plugin-claude-agent-sdk \
  ~/.hermes/plugins/model-providers/claude-agent-sdk
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python claude-agent-sdk
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

Models: `claude-sonnet-5` (suggested default), `claude-fable-5-1`, `claude-opus-5`,
`claude-haiku-4-5-20251001`. Auxiliary tasks — context compression, summaries,
titles — use Haiku 4.5 and also run on your subscription. Hermes' `--reasoning` /
`/reasoning` levels are forwarded as the SDK effort (`none` turns thinking off).

Known limitation: the provider does **not** appear in the interactive
`hermes model` picker or in the `/model` list, and the `claude-agent-sdk:<model>`
shorthand is not recognised. Hermes core deliberately skips out-of-tree
`external_process` providers there; see `docs/UPSTREAM.md` for the core change
that would lift this. The `--provider` forms above are fully supported.

## Configuration knobs

Provider aliases: `claude-sdk`, `claude-subscription`. Environment variables, all
optional:

- `HERMES_CLAUDE_AGENT_SDK_COMMAND` / `CLAUDE_CODE_EXECUTABLE` — where Hermes looks for
  the `claude` binary when checking that the provider is configured.
- `HERMES_CLAUDE_AGENT_SDK_CLI` — make the SDK drive that Claude Code binary instead of
  the one bundled with `claude-agent-sdk`.

## Tests

All tests are live: they run real Turns on your own subscription (Haiku 4.5) and
need Hermes' virtualenv so `claude_agent_sdk` and Hermes' modules resolve.

```
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python pytest pyyaml
~/.hermes/hermes-agent/venv/bin/python -m pytest tests -v
```

## Layout

```
plugin.yaml   # manifest (kind: model-provider, python_dependencies)
__init__.py   # ProviderProfile registration, effort mapping
client.py     # OpenAI-shaped client over claude_agent_sdk
tests/        # live tests
CONTEXT.md    # glossary
docs/         # PRD, ADRs, handoff notes, upstream notes
```
