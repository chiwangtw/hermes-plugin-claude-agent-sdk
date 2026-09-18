# hermes-claude-agent-sdk

Hermes Agent 的 model-provider plugin：讓 Hermes 透過官方 Claude Agent SDK 使用 Claude 訂閱方案，
而非直接呼叫 Anthropic API。本檔是詞彙表，不記實作決策。

## Language

### 約束

**Compliant（合規）**:
同時滿足兩件事：走 Anthropic 認可的訂閱計費路徑（Agent SDK），且不觸碰 Hermes core、
在 Hermes 升級後仍可運作。
_Avoid_: 合法、safe、官方

### Hermes 側

**Provider Profile**:
Hermes 用來選用與認證某個模型來源的註冊物件；本 plugin 就是一個 Provider Profile。
_Avoid_: provider、adapter、integration

**Client**:
Provider Profile 產出、供 Hermes loop 送出訊息並取回回覆的物件。
_Avoid_: transport、shim、bridge

**Hermes Tool**:
Hermes 自己 toolset 內註冊、由 Hermes 執行的工具。
_Avoid_: function、skill

**Turn**:
Hermes loop 一次送出訊息到取回完整回覆（含所有 Tool Proposal）的週期。
_Avoid_: request、query、round-trip

### Claude 側

**Runtime**:
Claude Agent SDK 啟動的 Claude Code 程序，持有 Subscription Login 並產生回覆。
_Avoid_: Claude Code、CLI、subprocess、agent

**Subscription Login**:
Runtime 所使用、由 Claude 訂閱方案（Pro 或 Max，不區分）計費的登入狀態。
_Avoid_: OAuth token、API key、credential、Max 訂閱

**Subscriber**:
持有 Subscription Login 的 Hermes 使用者；本 plugin 唯一的使用者類型，不分同事或外部人士。
_Avoid_: 同事、colleague、user

**Built-in Tool**:
Runtime 原生自帶的工具（如 Read、Write、Edit、Bash、Grep、Glob）；本 plugin 一律停用，Runtime 只看得到 Hermes Tool。
_Avoid_: native tool、Claude tool

### 兩側交界

**Tool Proposal**:
Runtime 回傳、尚未執行的工具呼叫請求；Runtime 本身永遠不執行工具。
_Avoid_: tool call、tool_use、function call

**Tool Execution**:
Hermes 依 Tool Proposal 執行 Hermes Tool 並產生結果回填給 Runtime。
_Avoid_: dispatch、invoke

**Tool Bridge**:
讓 Runtime 看得到全部 Hermes Tool（含其參數 schema）的唯一揭露機制；Runtime 端的名稱帶 `mcp__hermes__` 前綴，回到 Hermes 時去掉。
_Avoid_: MCP server、tool export、Tool Mapping
