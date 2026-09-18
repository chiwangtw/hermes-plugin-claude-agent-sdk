# Upstream changes worth proposing to hermes-agent

Things this plugin cannot do from outside the tree. Each item names the core
location (hermes-agent as of 2026-09-18, v0.21.x) so a PR can be scoped tightly.
None of these are vendor integrations; they generalise the existing
`external_process` plumbing that `copilot-acp` uses in-tree.

## 1. Let out-of-tree `external_process` providers reach the pickers

- `hermes_cli/models_catalog_static.py` (`CANONICAL_PROVIDERS` auto-extend, ~line 359):
  plugin profiles with `auth_type` in `{oauth_*, external_process, aws_sdk, copilot, vertex}`
  are skipped with the comment "Non-api-key flows need bespoke picker UX". For
  `external_process` the bespoke part is small: "configured" already means "the binary
  resolves" (`hermes_cli/auth.py:get_external_process_provider_status`), and the model list
  is the profile's `fetch_models()` / `fallback_models`.
- `hermes_cli/main.py` `_PROVIDER_MODEL_FLOWS` (~line 1930): no generic branch for
  `external_process`. A `_model_flow_external_process(config, provider_id, current_model)`
  that mirrors `_model_flow_copilot_acp` but reads everything from the profile
  (`process_command`, `fetch_models`, `base_url`) would serve copilot-acp *and* any plugin.
- `hermes_cli/model_switch_providers.py` `_lap_canonical_rows` (~line 890): credential check
  is api-key / auth-store / pool only; add
  `get_external_process_provider_status(slug).get("configured")` so the `/model` list
  shows the row.
- `hermes_cli/model_switch.py` `_route_from_model_input` → `_convert_vendor_colon_slug`:
  `claude-agent-sdk:<model>` does not resolve; `--provider claude-agent-sdk` (PATH A) works.

Effect once landed: `hermes model` lists "Claude Agent SDK", picking it shows the curated
models and persists via `_finish_model(..., base_url=profile.base_url,
api_mode="chat_completions")`; `/model` lists it; the colon shorthand works.

## 2. `ProviderProfile.create_client()` receives no `cwd`

`agent/agent_runtime_helpers.py:_provider_supplied_client` passes the OpenAI client kwargs
(`api_key`, `base_url`, `command`, `args`, timeouts). The Runtime's working directory
matters for an agent CLI (relative paths in tool proposals); today the client falls back
to `os.getcwd()`. Passing the agent's cwd would be a one-line addition to `client_kwargs`.

## Not upstream: things the plugin owns

- The Tool Bridge (all Hermes tools via in-process MCP, built-ins disabled) — ADR 0001.
- The subscription-only guard (`apiKeySource` must be `none`).
