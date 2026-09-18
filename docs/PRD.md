# PRD — hermes-claude-agent-sdk

## Goal

Subscriber 能以自己的 Subscription Login 合規地使用 Hermes Agent。
合規＝(1) 走 Anthropic 認可的訂閱計費路徑，避免帳號被禁；(2) Hermes Agent 升級後仍可使用。

## Non-goals

- 多人共用同一個 Subscription Login。
- 修改 Hermes core 或以 fork 方式發布。

## Users

Subscriber：持有 Subscription Login（Pro 或 Max）的 Hermes Agent 使用者。
公開發布（Discord #plugins-skills-and-skins、issue #25267），同事為其中一群。

## Requirements

- 每位 Subscriber 使用自己的 Subscription Login；plugin 不持有、不轉發任何登入憑證。
- Runtime 只提出 Tool Proposal，不執行工具；所有 Tool Execution 由 Hermes 完成。
- 沒有 Hermes 對應的 Built-in Tool（WebFetch、WebSearch、Agent 等）在啟動時就對 Runtime 停用，事前擋而非事後擋。
- Hermes 副模型（context 壓縮、摘要、標題）同樣走 Runtime，預設 Haiku 4.5。
- 以 standalone plugin 形式安裝於 `~/.hermes/plugins/model-providers/`，`git clone` 即可用，不修改 Hermes core。

## Open questions

- `hermes model` 互動選單／`/model` 列表不會顯示本 provider（core 刻意略過 out-of-tree `external_process`）。要不要送上游 PR？見 `docs/UPSTREAM.md`。

- 出貨前重驗 Anthropic Help Center：Agent SDK 是否仍計入訂閱額度。
