# hermes-claude-agent-sdk

Standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) model-provider
plugin: run Hermes on a Claude Pro/Max subscription through the official
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

> [!WARNING]
> **Deprecated.** This plugin is no longer maintained. Use Nous Research's official
> [Claude Subscription DirectSDK](https://github.com/NousResearch/hermes-plugin-claude-subscription-directsdk)
> provider instead. It does the same job (Hermes on your Claude Pro/Max subscription, Hermes
> keeps its own tools, approvals and compaction) with a better transport and support in
> Hermes core.
>
> v0.1.2 is the last release. There will be no further fixes or features, and the repository
> will be archived. It still works on Hermes v0.21.5 (checked on 2026-09-27 against `main` @
> `a1d2a5bd`), so existing installs keep running while you migrate.

## Migrating to the official plugin

What changes for you: history is replayed as real tool-use/tool-result blocks with signed
thinking instead of one flattened transcript; text streams as it is generated; each Hermes
call makes exactly one upstream request; 1M context windows are reported to Hermes; `hermes
model` and `/model` list the provider with your account's own models.

1. Update Hermes to 0.21.4 or newer (`hermes update`). Read the Windows notes below first if
   your gateway runs on Windows.
2. Install the standalone Claude Code CLI and log in: `npm install -g @anthropic-ai/claude-code`
   (or the native installer), then `claude auth login`. This plugin ran the copy of Claude Code
   bundled inside `claude-agent-sdk`. The official plugin runs the `claude` on your `PATH`,
   which may be older than you expect. Check `claude --version`: Opus 5.5 needs 2.1.280 or
   newer (`claude update`).
3. Install the plugin:

   ```
   hermes plugins install NousResearch/hermes-plugin-claude-subscription-directsdk
   ```

   `hermes plugins install claude-subscription-directsdk` installs the version pinned in the
   Hermes catalog instead of `main`.
4. In `~/.hermes/config.yaml`, change the model block and delete the `base_url` line:

   ```yaml
   model:
     provider: claude-subscription-directsdk-experimental
     default: opus        # or sonnet / haiku / fable; the 1M routes are chosen for you
     api_mode: chat_completions
   ```

5. Restart Hermes (or the gateway). Existing sessions pick up the new provider on their next
   turn; if one does not, send `/new`.
6. Leave `~/.hermes/plugins/claude-agent-sdk` in place until you are satisfied: switching
   `model.provider` back is a one-line rollback. Then run `hermes plugins remove claude-agent-sdk`.

### Windows notes from our own migration

These are Hermes core behaviours on the way from 0.21.3 to 0.21.5, not bugs in either plugin.

- `hermes update` stops and relaunches running gateways by itself. If your gateway is started
  by a wrapper that puts secrets into its environment (for example a scheduled task that
  decrypts the bot token), the relaunched gateway comes up without them. Stop the gateway
  through your wrapper before updating, and start it the same way afterwards.
- If the update prints `CLI exposure failed: source launcher publication failed`, the old
  `%LOCALAPPDATA%\hermes\bin\hermes.exe` was in use and could not be removed. It then shadows
  the new `hermes.cmd`, and Hermes reports `No module named 'pydantic_core._pydantic_core'`
  when it loads plugins. Once nothing is running `hermes.exe`, delete it (and `hermes-acp.exe`
  if present) so `hermes.cmd` takes over.
- With `gateway.multiplex_profiles: true`, newer Hermes reads platform tokens and the webhook
  secret only from each profile's `.env`, never from the process environment. A gateway that
  gets its tokens from the environment then logs `telegram is enabled but no profile (default
  or secondary) provided a bot credential` and skips webhook routes whose secret is missing.
  If you have no secondary profiles, set it to `false`; otherwise move the tokens into the
  profile's `.env`.

---

The rest of this README describes v0.1.2 as it was.

Status: **v0.1.2, deprecated** — Hermes turns round-trip through the SDK, the Runtime proposes
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
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python claude-agent-sdk
hermes plugins install https://github.com/chiwangtw/hermes-plugin-claude-agent-sdk
```

The first line puts `claude-agent-sdk` (which bundles its own Claude Code binary,
~90 MB) into Hermes' virtualenv; released Hermes (0.21.x) reports the dependency but
does not install it for you (newer Hermes main does). The second line clones the plugin
into `~/.hermes/plugins/`.

Hermes scans every community plugin before installing and will report a
*caution* verdict for this one: the findings are prose matches in `README.md`,
`docs/` and `tests/` (words like "git clone", "pip install", "ANTHROPIC_API_KEY",
a `ps` call in the test harness). Review them, then confirm at the prompt; in a
non-interactive shell pass `--force`. No `hermes plugins enable` step is needed —
model-provider plugins are picked up by the provider registry directly.

Manual alternative to the second line:

```
git clone https://github.com/chiwangtw/hermes-plugin-claude-agent-sdk \
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

Models: `claude-sonnet-5` (suggested default), `claude-fable-5-1`, `claude-opus-5-5`,
`claude-opus-5`, `claude-haiku-4-5-20251001`. Auxiliary tasks — context compression, summaries,
titles — use Haiku 4.5 and also run on your subscription. Hermes' `--reasoning` /
`/reasoning` levels are forwarded as the SDK effort (`none` turns thinking off).

A new session starts from `model.default` in `config.yaml`: `/model … --provider` without
`--global` only changes the current session.

New models need a new Runtime. The SDK drives the Claude Code binary bundled inside
`claude-agent-sdk`, and `claude update` does not touch that copy. When a model released
after your SDK fails with "version X or newer is required", the plugin upgrades the SDK in
Hermes' virtualenv by itself and runs the Turn again. No restart is needed: the SDK finds its
bundled binary again on every Turn. It does this only when all of these hold:

- A dry run shows `claude-agent-sdk` is the only installed package that would change (new
  dependencies may be added). Anything more means Hermes' pins would move: the error then
  says to update Hermes first.
- No other Runtime is running: Windows locks the binary while it runs. The plugin waits up
  to 60 s for other Turns to finish.
- This process has not already tried to upgrade from the installed version.

Turn it off with `HERMES_CLAUDE_AGENT_SDK_AUTO_UPGRADE=0`. When the automatic upgrade is off
or does not happen, the error says why and gives the manual fix, which is:

```
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python -P claude-agent-sdk claude-agent-sdk
```

`-P` upgrades only the SDK. The package name appears twice on purpose. Plain `-U` would
also upgrade packages Hermes pins, such as `pydantic` and `mcp`. Run it outside Hermes'
checkout: its `pyproject.toml` sets `exclude-newer = "14 days"`, which hides a fresh SDK.

From a chat app (Telegram etc.) the error gives the same fix as three steps:

1. Switch to a model the old Runtime runs: `/model claude-sonnet-5 --provider claude-agent-sdk`.
2. Send Hermes the quoted block. Hermes runs the command with its terminal tool.
3. Send `/new`. No gateway restart is needed: the SDK picks up the new bundled binary on the
   next Turn.

Alternatively, point `HERMES_CLAUDE_AGENT_SDK_CLI` at a standalone `claude` that is new
enough (check `claude --version`). A standalone `claude` updates itself only when it runs,
so a copy nobody runs stays as old as the bundled one.

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
- `HERMES_CLAUDE_AGENT_SDK_AUTO_UPGRADE` — `0` stops the plugin from upgrading
  `claude-agent-sdk` when the bundled Runtime is too old for a model (default: on). The upgrade
  runs `uv`, found on `PATH` or in `$HERMES_HOME/bin`.

## Tests

All tests are live: they run real Turns on your own subscription (Haiku 4.5) and
need Hermes' virtualenv so `claude_agent_sdk` and Hermes' modules resolve.

```
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python pytest pyyaml
~/.hermes/hermes-agent/venv/bin/python -m pytest tests -v
```

## Layout

```
plugin.yaml        # manifest (kind: model-provider, python_dependencies)
__init__.py        # ProviderProfile registration, effort mapping
client.py          # OpenAI-shaped client over claude_agent_sdk
runtime_upgrade.py # guarded upgrade of the bundled Runtime
tests/             # live tests
CONTEXT.md         # glossary
docs/              # PRD, ADRs, handoff notes, upstream notes
```
