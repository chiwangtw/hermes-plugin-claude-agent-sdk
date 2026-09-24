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

### v0.1 範圍（2026-09-18 grilling 定案）

- 發布門檻：不等 `hermes model` 選單；`--provider`、`/model --provider [--global]`、config.yaml 三條路即可。
- Runtime 生命週期：每個 Turn 新起一個 Runtime、整段對話重送；persistent session 留待有延遲抱怨再評估（屆時另立 ADR）。
- 模型：`claude-fable-5-1`、`claude-opus-5-5`、`claude-opus-5`、`claude-sonnet-5`、`claude-haiku-4-5-20251001`；主模型預設 `claude-sonnet-5`；副模型 `claude-haiku-4-5-20251001`。
- Reasoning effort：由 profile 的 `build_api_kwargs_extras` 帶進 create() kwargs，client 轉成 SDK `effort`。對映：`none`→thinking disabled；`minimal`/`low`→`low`；`medium`→`medium`；`high`→`high`；`xhigh`→`xhigh`；`max`/`ultra`→`max`。profile 宣告 `supported_reasoning_efforts`。
- 圖片：使用者訊息與 tool result 內的圖片都直接送進 Runtime（`supports_vision=True`）。
- 合規守門：Runtime 會用 API key（`apiKeySource != none`）時直接拒跑並報錯，無開關。
- 逾時／中斷：逾時或 Hermes 中斷時確實終止 Runtime 子程序。
- 測試：一律真打 API（不做假 SDK），全部用 Haiku 4.5，只手動跑，發布前必跑。
- 發布：個人 GitHub 帳號，repo 改名 `hermes-plugin-claude-agent-sdk`；v0.1 完成打 tag `v0.1.0`，`plugin.yaml` version 同步；Discord 與 #25267 連到 tag。
- 安裝：主路徑 `hermes plugins install https://github.com/chiwangtw/hermes-plugin-claude-agent-sdk`，`plugin.yaml` 宣告 `python_dependencies`（已釋出的 Hermes 0.21.x 只回報不安裝，較新 main 會自動裝），README 把 `uv pip install` 進 Hermes venv 列為必要前置；README 保留 `git clone` 到 `~/.hermes/plugins/model-providers/` 的手動路徑。不寫 install.sh。未登入時 Runtime 報錯，錯誤訊息引導 `claude login`。
- 授權：MIT。
- 文件語言：對外（README、ADR、UPSTREAM、註解、commit）英文；PRD、CONTEXT.md 中文。

### v0.2 以後

- 審查遺留：測試夾具以 argv 子字串辨識 Runtime 程序（僅測試用）；遠端 http(s) 圖片 URL 直接轉發；未登入提示訊息無 live 測試（需登出狀態）。

- persistent Runtime session（視延遲抱怨）。
- 上游 PR：讓 out-of-tree `external_process` provider 進 `hermes model` / `/model`（`docs/UPSTREAM.md`）。

## Open questions

- `hermes model` 互動選單／`/model` 列表不會顯示本 provider（core 刻意略過 out-of-tree `external_process`）。要不要送上游 PR？見 `docs/UPSTREAM.md`。

- 出貨前重驗 Anthropic Help Center：Agent SDK 是否仍計入訂閱額度。
