# 建構任務：Plugin-First AI 需求工程平台（重做 spec / ver3）

> 這是一份給 AI builder agent（Claude Code 等）的完整建構指令。
> 自包含：不需存取舊程式碼即可執行。若你能讀到舊 repo（ai-dev-ver2），
> 可參考其業務邏輯，但**架構一律以本文件為準**。

---

## 1. 你的任務

從零建構一個 **plugin-first** 的 AI 需求工程平台：把「想法 → PRD → 架構 →
使用者故事 → 交付 → （可選）自動實作」串成 pipeline。但與一般 pipeline 不同 ——
**從第一天起，每個 stage、每個 AI agent、每個 delivery target 都是可獨立打包、
安裝、分發的 plugin**。使用者能完整客製化自己的 AI agent，並用表單式 UI
串出不同的流程。

目標使用者場景：open-source、單租戶自架；信任模型寬鬆（裝進目錄就載入，
像 pytest plugin / Django app），**不做安全沙箱、不做多租戶隔離**。

---

## 2. 設計哲學（五條鐵則，貫穿全系統）

1. **Plugin-first**：core 只提供 framework + contracts + host。所有「能力」
   一律是 plugin —— 連內建的 PRD/架構/故事 stage、GitHub/Jira 交付也是「內建
   plugin」。core 裡**不准**出現任何具體 stage 名稱的硬編碼。

2. **Data-driven flow**：流程是**資料**，不是程式碼。新增/刪除/重排 stage、
   改依賴關係，都只動資料（DB / config），**不改 core**。

3. **Host owns all I/O**：plugin handler **永遠拿不到** DB connection、ORM、
   檔案系統 raw access。它只收 host 注入的唯讀 context + 門面物件，回傳純資料。
   所有 DB 寫入、artifact 持久化、狀態轉換都在 host 層完成。這讓 plugin 作者
   不可能踩到並行/交易/狀態損壞的坑。

4. **嚴格分層的兩種 AI runtime**：
   - **Sync one-shot**：stage 生成（PRD/架構/故事…），一次呼叫拿完整輸出。
   - **Async long-running**：實作 agent（跑子程序、串流 log、工具呼叫、可取消）。
   這兩套 runtime **不可 cross-import、不共用資料表、不共用 run_id 形狀**。
   plugin 透過不同的門面接觸它們，預設只給 sync 那套。

5. **表單優先的 UI**：所有客製化（建 workflow、編 agent、裝 plugin）用表單/
   清單/選單完成。**不做**視覺化拖拉 DAG canvas。

---

## 3. 技術棧（建議值，可由使用者調整）

| 層 | 選型 | 備註 |
|---|---|---|
| Backend | Python 3.11+, FastAPI | async-first |
| 流程編排 | **自製輕量 `WorkflowEngine`（純 Python）** | **不要**用 LangGraph / 任何 graph framework 承載流程（理由見 §10） |
| 持久化 | SQLite 單檔（WAL 模式），DAL 抽象以便日後換 Postgres | |
| Manifest 解析 | 內建 `tomllib`（零新依賴） | |
| Model access | CLI adapters（claude-cli / codex-cli / …）+ local Ollama，**registry 模式** | adapter 也是 plugin capability |
| Frontend | Next.js (App Router) + TypeScript + Tailwind | |
| 邊界型別驗證 | zod | 對應 plugin 帶來的開放 stage 集合 |
| 測試 | pytest（後端）、tsc typecheck（前端） | |

**關鍵簡化（重做的紅利）**：不用 LangGraph。artifact 與 conversation history
直接存 DB（明確的表 + DAL），流程由 `WorkflowEngine` 管。這移除一整類並行/交易
風險（見 §10/§13）。

---

## 4. 系統分層

```
┌─────────────────────────────────────────────────────────┐
│ Frontend (Next.js) — catalog-driven，全部從 API 取流程定義   │
├─────────────────────────────────────────────────────────┤
│ HTTP API (FastAPI) — 通用 endpoint，無 per-stage 特例        │
├─────────────────────────────────────────────────────────┤
│ Host 層                                                    │
│  • WorkflowEngine（dispatch / 依賴推導 / 下游 reset）         │
│  • PluginHost（註冊入口）+ PluginLoader（discover/load）       │
│  • Registries（stage / workflow / agent / integration /     │
│    model_adapter / runner / validator / hook）              │
│  • Persistence / DAL（唯一碰 DB 的地方）                      │
│  • HarnessRunner（注入給 plugin 的 sync-AI 門面）             │
│  • ImplementRunner（注入給 plugin 的 async-AI 門面，受限）     │
├─────────────────────────────────────────────────────────┤
│ Plugins（含內建）                                           │
│  builtin_core_stages / builtin_integrations /              │
│  builtin_agents / <第三方>                                  │
│  每個 = plugin.toml + register(host) entry                  │
└─────────────────────────────────────────────────────────┘
```

---

## 5. Plugin 系統

### 5.1 Manifest（`plugins/<id>/plugin.toml`）

```toml
[plugin]
id = "builtin_core_stages"           # 全域唯一
name = "Core Requirements Stages"
version = "1.0.0"                     # plugin 自身版本 (semver)
description = "PRD / Architecture / Stories built-in stages"
host_api = ">=1.0,<2.0"              # 需要的 host plugin API 版本範圍
entry_module = "plugins.builtin_core_stages.register"  # 單一 import 入口
requires_plugins = []                 # 依賴的其他 plugin（拓樸排序用）

# 只宣告「貢獻什麼」供 loader 預檢 + UI 預覽；真正的 spec 在 register() 建構
[[contributes.stage]]
id = "prd"
label = "PRD"
[[contributes.stage]]
id = "architecture"
label = "Architecture"
[[contributes.workflow]]
id = "default"
label = "Standard Pipeline (PRD → Architecture → Stories)"
```

設計理由：manifest **只宣告存在**；Callable handler 一律在 `entry_module` 的
`register(host)` 裡建構（pytest plugin 模式），避免在 TOML 塞 Python 路徑字串
再動態 resolve（脆弱、無法 type-check）。

### 5.2 Loader（`backend/plugin_loader.py`）

啟動時、DB migration 後執行：

1. **discover**：內建 plugin 走固定清單；第三方掃 `backend/plugins/*/plugin.toml`
   （+ 可選的 `importlib.metadata` entry-point 發現，供 `pip install` 的 plugin）。
2. **host_api 檢查**：semver range 不符 → **skip 該 plugin + warn log，不打掛 app**。
3. **拓樸排序**：依 `requires_plugins`；遇環 → skip + 明確 log。
4. **兩階段註冊**：先對所有 plugin 呼叫 `register(host)`（收集全部 spec），
   **再**做一次 cross-reference 驗證（如 workflow 引用的 stage_id 必須都已註冊）；
   驗證失敗者 skip + warn。
5. **隔離失敗**：單一 plugin 的 import / register 例外不可影響其他 plugin 或 app 啟動。

### 5.3 PluginHost（註冊入口 + 注入門面）

```python
class PluginHost:
    def __init__(self, plugin_id: str): ...
    # 註冊入口 —— 各自寫進對應 registry，並記一筆 plugin_contributions
    def register_stage(self, spec: StageSpec) -> None: ...
    def register_workflow(self, spec: WorkflowSpec) -> None: ...
    def register_agent(self, spec: AgentSpec) -> None: ...          # seed 預設 agent
    def register_integration(self, spec: IntegrationSpec) -> None: ...
    def register_model_adapter(self, adapter: ModelAdapter) -> None: ...
    def register_runner(self, choice: str, cls: type) -> None: ...  # 存 class 非 instance
    def register_validator(self, stage: str, operation: str, fn) -> None: ...
    def register_hook(self, event: str, fn) -> None: ...
    # 提供給 stage handler 的執行門面（見 §7.3）
    def make_harness_runner(self, thread_id: str) -> HarnessRunner: ...
```

每次註冊在 `plugin_contributions(plugin_id, capability_type, capability_id)`
記一筆，供 GUI 顯示「這個 stage 由哪個 plugin 提供」+ disable 時清理。

各 registry 維持**各自合適的內部形狀**（不要硬統一成單一 dict 形狀），只統一
「註冊入口 + ownership metadata」。

---

## 6. Capability Contracts

> 全部由 host 在 `backend/plugin_api/` 定義，plugin 只 import 這裡，
> **絕不** import host 內部模組或另一套 runtime。

### 6.1 StageSpec —— 整個系統的心臟

```python
@dataclass(frozen=True)
class StageSpec:
    # 身份 —— 雙詞彙（務必兩套都帶，否則遙測/validator 對不上、靜默失效）
    id: str                       # UI/狀態詞彙: "prd" / "architecture" / "stories"
    label: str
    icon: str = ""                # 名稱字串（前端從 icon allowlist resolve）
    telemetry_stage: str = ""     # 遙測詞彙: "specify" / "design" / "deliver"
    generate_operation: str = ""  # 預設 f"generate_{id}"，可覆寫
    refine_operation: str = ""    # 預設 f"refine_{id}"

    # 依賴 —— 取代任何硬編碼依賴 dict；downstream 由 host 反推
    depends_on: tuple[str, ...] = ()

    # artifact 持久化 key（host 用它存/取，plugin 看不到儲存細節）
    artifact_key: str = ""        # 預設等於 id

    # prompt / agent 綁定
    prompt_key: str = ""          # prompt 模板識別
    default_agent_role: str = ""  # 預設綁哪個 agent role（可被 workflow 覆寫）

    # handlers（純函式；簽名見下）
    generate: Optional["StageGenerateFn"] = None
    refine: Optional["StageRefineFn"] = None
    supports_chat: bool = False

    # 少數需要保留的「stage 個性」side-effect（如 prd 完成時設旗標）
    on_complete_state_extra: dict = field(default_factory=dict)
```

### 6.2 StageContext / StageResult（plugin handler 的唯一 I/O）

```python
@dataclass(frozen=True)
class StageContext:
    thread_id: str
    stage_id: str
    model_choice: str
    instruction: str = ""                       # refine 用
    upstream_artifacts: dict[str, str] = field(default_factory=dict)  # host 依 depends_on 備好
    current_artifact: str = ""                  # refine/chat 時非空
    metadata: dict = field(default_factory=dict)

@dataclass(frozen=True)
class StageResult:
    artifact: str                               # 新的 artifact 正文
    telemetry_metadata: dict = field(default_factory=dict)

StageGenerateFn = Callable[[StageContext, "HarnessRunner"], StageResult]
StageRefineFn   = Callable[[StageContext, "HarnessRunner"], StageResult]
```

**鐵則**：handler 全程**不碰 DB / 不碰流程狀態**。它從 `ctx.upstream_artifacts`
取上游、用 `HarnessRunner` 跑 AI、回傳 `StageResult`。host 在 handler 回傳後
**統一**寫 artifact（DAL）、reset 下游、記 revision/event。→ plugin 作者想製造
狀態損壞都做不到。

範例（dogfood）：

```python
def _architecture_generate(ctx: StageContext, run: HarnessRunner) -> StageResult:
    prd = ctx.upstream_artifacts["prd"]
    agent = run.get_agent_for_stage("architecture")
    prompt = build_architecture_prompt(prd=prd, agent=agent)
    out = run.harnessed_step(
        telemetry_stage="design", operation="generate_architecture",
        prompt=prompt, metadata={"prd": prd},
        max_iterations=agent.max_iterations if agent else 1,
    )
    return StageResult(artifact=out.raw_output, telemetry_metadata={"prd": prd})
```

### 6.3 HarnessRunner（注入門面，強制 runtime 隔離）

```python
class HarnessRunner(Protocol):
    """Plugin stage handler 的唯一 AI 入口。刻意只暴露 sync one-shot harness；
    async 實作 runtime 的任何符號永不出現在 plugin_api。"""
    def harnessed_step(self, *, telemetry_stage: str, operation: str,
                       prompt: str, metadata: dict, max_iterations: int = 1
                       ) -> HarnessResult: ...
    def get_agent_for_stage(self, stage_id: str) -> Optional[AgentSpec]: ...
    def feedback_block(self, *, telemetry_stage: str, operation: str) -> str: ...
```

### 6.4 WorkflowSpec

```python
@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    description: str = ""
    stages: tuple[str, ...] = ()                      # 有序 stage_id 序列
    edges_override: dict[str, tuple[str, ...]] = field(default_factory=dict)
    agent_bindings: dict[str, str] = field(default_factory=dict)  # stage_id -> agent_id 覆寫
    source_plugin: str = ""
```

依賴推導用純函式 `compute_dependencies(workflow, stage_registry)`（取代任何手寫
依賴 dict），並反轉得 downstream。

### 6.5 AgentSpec —— 「完整客製化 AI agent」的核心

agent 既可由 plugin 帶 seed，也可由使用者在 UI 建立/編輯（存 DB，可覆蓋 seed）。

```python
@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    role: str                       # 綁定的能力/stage role（data-driven，非 frozenset）
    system_prompt: str
    model_choice: str
    skills: tuple[SkillSpec, ...] = ()   # 可組合、可排序的 prompt 片段
    tools: tuple[str, ...] = ()          # 允許使用的工具（給 tool-using / 實作 agent）
    max_iterations: int = 1
    enabled: bool = True
```

UI 必須能：列出全部 agent、新建 agent、編輯（prompt/model/skills/tools/iterations）、
綁定到 stage、啟用/停用。

### 6.6 IntegrationSpec（delivery target）

```python
@dataclass(frozen=True)
class IntegrationSpec:
    target: str                     # "github" / "jira" / "gitlab" / 第三方
    preview: Callable[[list[DeliveryItem], dict], list[dict]]
    publish: Callable[[list[DeliveryItem], dict], PublishResult]
    config_schema: dict             # 讓前端自動 render 設定表單
    description: str
```

### 6.7 ModelAdapter / Runner

- `ModelAdapter`（sync one-shot）：`{ model_choice, invoke(prompt)->str,
  is_available()->bool, context/budget tokens }`。registry: `dict[str, ModelAdapter]`。
- `Runner`（async long-running，給實作 agent）：abstract base，`build_argv()`、
  `is_available()`、`async run(cwd, prompt, timeout, on_log, on_event, hooks)->RunResult`。
  registry **存 class** 不是 instance。

### 6.8 Validator / Hook

- Validator：純函式 `(artifact, ctx) -> ValidationOutcome`，每個 outcome 帶
  `severity`（**預設一律 warn-only**，escalate 到 fail 需明確流程）+ `fix_hint`
  （祈使句、動詞開頭，告訴下一輪 model 修什麼）。registry key = `(telemetry_stage, operation)`。
- Hook：workflow 生命週期事件（`on_stage_complete` / `before_publish` / 實作 agent 的
  tool pre/post），用於 redact secrets、擋受保護分支等。

---

## 7. WorkflowEngine（流程引擎）

純 Python，無 graph。職責：

- `active_workflow_for(thread_id)`：讀該 thread 綁的 workflow（無綁定 → `default`）。
- `dispatch(thread_id, stage_id, op, instruction)`：
  1. 查 `StageSpec`；驗證 `stage_id ∈ active_workflow.stages`。
  2. `compute_dependencies` → 取上游 artifact（DAL）→ 缺則回 4xx + 明確訊息。
  3. 組 `StageContext` + `make_harness_runner` → 在 worker thread 跑 handler。
  4. handler 回傳後：DAL 寫 artifact、`_downstream_of` 反推所有下游並 reset、
     記 revision/event、發 SSE 進度事件、套用 `on_complete_state_extra`。
- 依賴檢查與下游 reset **完全從 spec/workflow 推導**，core 裡沒有任何
  `if stage == "architecture"` 之類的特例。

通用 HTTP endpoint（無 per-stage 特例）：

```
GET  /api/stages                       # 全部已註冊 stage capability（catalog）
GET  /api/workflows  /api/workflows/{id}
POST/PUT /api/workflows/{id}           # 表單式建立/編輯
POST /api/stage/{stage_id}/generate
POST /api/stage/{stage_id}/refine
POST /api/stage/{stage_id}/chat        # supports_chat 才開放
PUT  /api/stage/{stage_id}/{thread_id} # manual edit
GET  /api/stage/statuses/{thread_id}   # 依 active workflow 列舉
GET  /api/agents  /api/agents/{id}     # CRUD（含 POST 建立）
GET  /api/plugins  PATCH /api/plugins/{id}  # 列出 / enable-disable
GET  /api/stage/stream/{thread_id}     # SSE 進度
```

---

## 8. 資料模型（SQLite，單檔，WAL）

> 所有 schema migration 集中管理；唯一碰 DB 的是 DAL 層。

- `projects(thread_id, name, workflow_id, created_at, ...)` — workflow_id 可 NULL（lazy default）
- `stage_artifacts(thread_id, stage_id, content, updated_at)` — artifact 正文（**直接存表，
  不用 checkpoint blob**）
- `stage_status(thread_id, stage_id, status, ...)` — draft/approved/needs_revision
- `stage_revisions(thread_id, stage_id, source, summary, downstream_reset, content_len, ...)`
- `stage_events(thread_id, stage_id, kind, payload, ts)`
- `stage_messages(thread_id, stage_id, role, content, ts)` — chat / conversation history
- `agents` / `skills` / `agent_skills` — agent 客製化（seed 由 plugin 帶，user edit 覆蓋）
- `workflow_definitions(id, label, description, stages_json, source_plugin)`
- `plugin_contributions(plugin_id, capability_type, capability_id)`
- `plugin_state(plugin_id, enabled)` — enable/disable
- Sync-AI 遙測：`harness_runs` / `harness_events` / `harness_validation_results`
- Async 實作 agent（**獨立命名空間**）：`impl_sessions` / `impl_messages` /
  `impl_runs` / `milestones` / `telemetry_events` —— 與 sync 遙測表**不共用 run_id 形狀**
- `app_settings(key, value)` — 雜項設定（如 repo 工作目錄）

---

## 9. 內建 plugin（dogfood，證明 plugin API 夠用）

- **builtin_core_stages**：`prd` / `architecture` / `stories` 三個 `StageSpec` +
  generate/refine handler + 各自 validator + `default` workflow（prd→architecture→stories）。
  業務邏輯（prompt 內容、stories 摘要 heuristic 等）可從 ver2 移植，但**必須**走
  本文件的 contract。
- **builtin_integrations**：`github` / `jira` / `gitlab` 三個 `IntegrationSpec`
  （preview-before-publish）。
- **builtin_agents**：上述 stage 的 seed agent（system_prompt + skills + model）。
- （後期）**builtin_implement**：一個 async runner（claude-cli / codex-cli）+ 一種
  「實作 stage」，把交付項目自動寫 code、跑測試、開 PR。

> 鐵則：這些內建 plugin 用的 API，**和第三方完全相同**。core 不給內建走後門。

---

## 10. 為什麼不用 LangGraph（重做的關鍵決策）

ver2 用 LangGraph 只為了兩件事：SA chat node + SqliteSaver checkpoint 存 artifact。
代價是一條 hard rule：**graph node 內絕不能 `conn.commit()`**（會 trip SqliteSaver
的交易，造成 silent state corruption），逼得任何 node 內業務寫入都要另開連線。

重做沒有這個包袱：

- artifact 與 conversation 直接存 `stage_artifacts` / `stage_messages`（明確的表）。
- SA chat 就是「append message + 呼叫 model + append 回覆」，一般 endpoint 即可。
- 流程由 `WorkflowEngine` 純 Python 編排。

→ 整個「node 內 commit / checkpoint 序列化 / channel reducer merge」風險類別**直接消失**。
若日後真需要 conversation time-travel，再以明確的 message 版本表實現，而非引入 graph。

---

## 11. 必須遵守的 hard-won 教訓（來自 ver2 的血淚，務必編碼進去）

### 後端
- **兩種 AI runtime 嚴格分離**：sync one-shot 與 async long-running 不可 cross-import、
  不共用表、不共用 run_id 形狀。加一個 test：AST 掃描 plugins/* 不得 import async runtime。
- **`asyncio.create_task` 必須存強引用**：event loop 只持 weak ref，長 `await` 期間
  task 會被 GC 靜默消失（無例外、無 log、process 還活著）。用 module-level `set` 抓 task +
  `add_done_callback(set.discard)`。
- **validator 預設 warn-only**：不可擅自升級 fail。每個 outcome 帶 `fix_hint`。
- **雙詞彙對齊**：`StageSpec` 沒帶對 `telemetry_stage`/`operation` → validator
  靜默不跑、不報錯。加 test 斷言每個內建 stage 在 validator registry 有對應。
- **host 是唯一 DB 寫入者**：plugin 拿不到 connection（見 §2 鐵則 3）。
- **plugin 載入失敗要隔離**：一個壞 plugin 不可打掛整個 app。

### 前端（React/Next）
- **不要把 fragment 放進 CSS Grid**：fragment 在 DOM 透明，children 被父 grid 攤平、
  撞 auto-placement、斷 flex/scroll chain。固定欄 grid 內的 ReactNode 要保證是單一
  element（包 wrapper div，設 `flex` + `min-height/width:0` + `height:100%`）。
- **`useEffect(()=>localStorage.setItem(K,x),[x])` 在 mount 會 fire** 並用 state 預設值
  蓋掉已存值 —— 這 pattern 幾乎永遠是錯的。改用 `setAndPersist` setter wrapper。
  persistence 功能**必須真的重整頁面測**，不能只靠 typecheck。
- **polling refresh 不要 `setLoading(true)`**：會週期性 unmount 子樹 → 頁面高度劇變 →
  瀏覽器 clamp scrollY → 「跳回 top + 閃」。`loading=true` 只在 useState 初值給，
  refresh 永遠只在 finally 寫回 false。UI bug 別猜，instrument 現場看。
- **開放 stage 集合的型別安全**：`StageKey` 是 `string`（非 union），用 zod 在網路邊界
  runtime validate；查表一律 `catalog.byId[id] ?? fallback`，不可裸 index。
- **icon 不可硬綁 import 的 component**：用名稱字串 + `resolveIcon(name)` 從 allowlist
  取、未知時 fallback（外加 data-URI/URL escape hatch），讓 plugin 不必 ship component。
- **重型/瀏覽器限定的 renderer（如 Mermaid）用 `next/dynamic({ssr:false})` 懶載**。

---

## 12. 前端架構（catalog-driven，表單式）

- `StageCatalogProvider`（mount 在 root layout）+ `useStageCatalog()`：全 app 共用一份
  `GET /api/stages`，消滅任何硬編碼 stage 清單。提供 `refetch()`，plugin enable/disable
  與 workflow 存檔後呼叫。
- **stage 工作區元件**：`ForgeGenericStage`（doc 檢視/編輯 + chat + generate/refine/approve，
  覆蓋多數情境）當預設 renderer；一個 `STAGE_RENDERERS` registry 給特化少數（如架構圖、
  交付摘要）。沒帶前端的 plugin 自動拿 generic body。plugin 若要自訂前端，接受「需 rebuild」。
- **workflow 編輯器**（`/workflows`）：有序清單 + Up/Down 重排 + 每列一個「依賴 multi-select」
  （**只能勾選排在它前面的 stage** → 靠建構天然防環，無需 DAG canvas）+ 頂部 read-only
  預覽 stepper + 每列可選綁定的 agent。
- **agent 編輯器**（`/agents`）：列表 + 新建 + 編 prompt/model/skills/tools/iterations + 綁 stage。
- **plugin 管理**（`/plugins`）：gallery 卡片，顯示 manifest（version/author/provides）、
  enable/disable、requires-rebuild badge。integration 設定表單由 `config_schema` 自動 render。

---

## 13. 明確的非目標（不要做）

- 不做安全沙箱 / plugin 權限隔離 / 多租戶（單租戶自架，信任模型寬鬆）。
- 不做視覺化拖拉 DAG canvas（表單式即可）。
- 不引入 LangGraph 或任何 graph orchestration framework。
- core 不得出現任何具體 stage 名稱硬編碼。
- 不為內建 plugin 開後門（內建與第三方用同一套 API）。

---

## 14. 建構里程碑（依序，每個都要可獨立驗證）

| M | 內容 | 完成判準 |
|---|---|---|
| **M0** | 專案骨架：FastAPI + Next 起得來；DB + DAL + migration；`plugin_api/` contract 定義；`PluginLoader` + `PluginHost`（先只接 integration）；dogfood `builtin_integrations` | `./start.sh` 起得來；loader log 顯示載入 integration plugin；前端打得開 |
| **M1** | 一個 stage 端到端走通：`builtin_core_stages` 只先做 `prd`；`WorkflowEngine.dispatch`；通用 `/api/stage/{id}/generate|refine|chat`；前端 `useStageCatalog` + `ForgeGenericStage` | 能在 UI 對一個空 thread 生成並 refine PRD；重整頁面狀態還在 |
| **M2** | 補齊 `architecture`/`stories` stage（含依賴、下游 reset、validator、雙詞彙）+ `default` workflow + agent seed | 跑完整 PRD→架構→故事；上游改動正確 reset 下游；validator 對應 test 綠 |
| **M3** | workflow 編輯器 + agent 編輯器（含新建/綁定）+ per-thread workflow 綁定 | 建一個「prd→architecture（跳過 stories）」workflow、綁新 thread，stepper/依賴正確；老 thread 仍 default |
| **M4** | 第三方 plugin 打包/分發：掃目錄 + entry-point；plugin 開發指南 + cookiecutter 範本 + 一個 example stage plugin；`/plugins` 管理 UI | 把 example plugin 丟進目錄 → 重啟 → 出現在 catalog 與 workflow builder、能 generate |
| **M5** | （可選）交付 + 自動實作 agent：delivery publish + async `Runner` + 實作 stage（寫 code/跑測試/開 PR）；tool hooks（redact secrets、擋受保護分支）；硬上限 fix-iteration | 對一個 GitHub issue 跑實作 agent、開出 PR；受保護分支被擋；秘密被 redact |

---

## 15. 驗證與品質門檻

- 後端：`python -m pytest backend/tests/`（每 M 全綠）；新增 test 覆蓋：依賴推導、
  下游 reset、validator 雙詞彙對應、plugin 載入隔離、AST 隔離 guard（plugins 不 import async runtime）。
- 前端：`npm run typecheck` 綠；persistence 類功能**必須實際重整頁面測**。
- 端到端：每個 M 用 `./start.sh` 起服務，手動走該 M 的核心流程（含 SA/PRD chat 互動）。
- code review 自問：「staff engineer 會 approve 嗎？」simplicity-first、minimal impact、
  找 root cause 不貼 OK 繃。

---

## 16. 你現在該做的

從 **M0** 開始。先把 plugin framework 骨架 + loader + 一個 dogfood integration 建起來、
跑起來、可驗證，再進 M1。**不要**一次寫整個系統。每個里程碑結束都要能 demo + 測試綠燈。
遇到架構抉擇，回到 §2 的五條鐵則與 §11 的教訓對照。

---
---

# 附錄 A — 完整可編譯 Contract Code（`backend/plugin_api/`）

> Plugin 與 host 之間的全部介面。Plugin **只** `from plugin_api import ...`，
> 不 import host 內部模組（loader 確保 `backend/` 在 `sys.path`，全專案統一用
> `plugin_api.*` 絕對 import，不用 ver2 那套 `try/except ModuleNotFoundError`
> 雙路徑）。型別取自 ver2 實際定義並對齊新架構（host owns I/O：plugin 永不持有
> `conn`）。以下可直接落檔編譯。

### `backend/plugin_api/common.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class DeliveryItem:
    """一筆可發佈到 tracker 的交付項目（逐字沿用 ver2 artifacts.py）。"""
    title: str
    body: str
    estimate: int
    group: str
    labels: list[str]
    target_project: str = ""
    senior_rd_days: float = 1.5
    requirement_refs: list[str] = field(default_factory=list)
    requirement_source: str = "unmapped"
    jira_key: str = ""
    github_issue_number: int = 0
    github_repo: str = ""
    github_url: str = ""
    gitlab_issue_url: str = ""
    gitlab_issue_iid: int = 0
    gitlab_project_id: str = ""


@dataclass(frozen=True)
class DeliveryPublishResult:
    success: bool
    target: str
    count: int
    created: list[str]
```

### `backend/plugin_api/harness.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# severity —— 沿用 ver2：目前只發 warn，fail 保留給未來強制層（鐵則：預設 warn-only）
SEVERITY_WARN = "warn"
SEVERITY_FAIL = "fail"
SEVERITIES = frozenset({SEVERITY_WARN, SEVERITY_FAIL})

# 錯誤碼 —— 沿用 ver2 closed set（host 把例外 map 成這些）
ERROR_MODEL_TIMEOUT = "model.timeout"
ERROR_MODEL_NETWORK = "model.network"
ERROR_MODEL_MALFORMED = "model.malformed_output"
ERROR_MODEL_UNAVAILABLE = "model.unavailable"
ERROR_MODEL_UNKNOWN = "model.unknown"
ERROR_HARNESS_CANCELED = "harness.canceled"
ERROR_HARNESS_INTERNAL = "harness.internal"
ERROR_CODES = frozenset({
    ERROR_MODEL_TIMEOUT, ERROR_MODEL_NETWORK, ERROR_MODEL_MALFORMED,
    ERROR_MODEL_UNAVAILABLE, ERROR_MODEL_UNKNOWN,
    ERROR_HARNESS_CANCELED, ERROR_HARNESS_INTERNAL,
})


@dataclass(frozen=True)
class HarnessContext:
    """一次 harnessed AI step 的輸入。prompt 必須是已 render 的最終字串
    —— harness 不做模板渲染（模板留在 prompt 資產，見附錄 D）。"""
    thread_id: str
    stage: str          # 遙測 stage：specify / design / deliver
    operation: str      # generate_architecture / refine_prd / ...
    model_choice: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessValidationOutcome:
    """一個 validator 對 model 輸出的判決。fix_hint 是祈使句、動詞開頭，
    告訴下一輪 model 怎麼修；None = 該 validator 不給 hint。"""
    validator: str
    severity: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    fix_hint: Optional[str] = None


@dataclass(frozen=True)
class HarnessResult:
    """一次 harnessed step 的輸出。"""
    run_id: str
    raw_output: str
    validations: list[HarnessValidationOutcome] = field(default_factory=list)
    error_code: str = ""
    error_message: str = ""


# validator 純函式簽名；registry key = (telemetry_stage, operation)
ValidatorFn = Callable[[str, HarnessContext], "list[HarnessValidationOutcome]"]
```

### `backend/plugin_api/stage.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from plugin_api.runner import HarnessRunner


@dataclass(frozen=True)
class SkillSpec:
    """可組合、可排序的 prompt 片段（沿用 ver2 SkillDef）。"""
    skill_id: str
    name: str
    description: str
    body: str
    version: str = "1.0"


@dataclass(frozen=True)
class AgentSpec:
    """一個可完整客製化的 AI agent。plugin 可帶 seed；user 可在 UI 覆蓋。"""
    agent_id: str
    name: str
    role: str                       # 綁定的 stage role（data-driven，非 frozenset）
    system_prompt: str
    model_choice: str = "claude-cli"
    skills: tuple[SkillSpec, ...] = ()
    tools: tuple[str, ...] = ()     # 允許工具（給 tool-using / 實作 agent）
    max_iterations: int = 1
    enabled: bool = True


@dataclass(frozen=True)
class StageContext:
    """host 餵給 stage handler 的唯讀輸入。handler 不碰 conn / graph / DB。"""
    thread_id: str
    stage_id: str
    model_choice: str
    instruction: str = ""                                   # refine 用
    upstream_artifacts: dict[str, str] = field(default_factory=dict)  # host 依 depends_on 備好
    current_artifact: str = ""                              # refine / chat 時非空
    conversation: tuple[tuple[str, str], ...] = ()          # (role, content)；chat/refine 用
    focus_section: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StageResult:
    """handler 回傳。host 負責寫 artifact / reset 下游 / 記 revision。"""
    artifact: str
    telemetry_metadata: dict = field(default_factory=dict)
    state_extra: dict = field(default_factory=dict)         # 額外 side-effect（如 prd is_ready）


@dataclass(frozen=True)
class StageChatResult:
    reply: str
    updated_artifact: Optional[str] = None  # chat 產生新 artifact（[CONTENT_START]..[CONTENT_END]）時填


StageGenerateFn = Callable[["StageContext", "HarnessRunner"], StageResult]
StageRefineFn = Callable[["StageContext", "HarnessRunner"], StageResult]
StageChatFn = Callable[["StageContext", "HarnessRunner"], StageChatResult]


@dataclass(frozen=True)
class StageSpec:
    """整個系統的心臟。雙詞彙（id / telemetry_stage）務必兩套都帶。"""
    id: str                         # UI/狀態詞彙：prd / architecture / stories
    label: str
    icon: str = ""                  # icon 名稱字串（前端 allowlist resolve）
    telemetry_stage: str = ""       # 遙測詞彙：specify / design / deliver
    generate_operation: str = ""    # 預設 f"generate_{id}"
    refine_operation: str = ""      # 預設 f"refine_{id}"
    chat_operation: str = ""        # 預設 f"chat_{id}"
    depends_on: tuple[str, ...] = ()        # 上游 stage_id；downstream 由 host 反推
    artifact_key: str = ""          # 預設等於 id（host 用它存/取 stage_artifacts）
    prompt_keys: tuple[str, ...] = ()       # 用到的 prompt 資產檔名（見附錄 D）
    default_agent_role: str = ""    # 預設綁的 agent role（可被 workflow 覆寫）
    generate: Optional[StageGenerateFn] = None
    refine: Optional[StageRefineFn] = None
    chat: Optional[StageChatFn] = None
    supports_chat: bool = False
    on_complete_state_extra: dict = field(default_factory=dict)
```

### `backend/plugin_api/workflow.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    description: str = ""
    stages: tuple[str, ...] = ()                    # 有序 stage_id 序列
    edges_override: dict[str, tuple[str, ...]] = field(default_factory=dict)
    agent_bindings: dict[str, str] = field(default_factory=dict)  # stage_id -> agent_id
    source_plugin: str = ""
```

### `backend/plugin_api/integration.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from plugin_api.common import DeliveryItem, DeliveryPublishResult


@dataclass(frozen=True)
class IntegrationSpec:
    target: str                     # github / jira / gitlab / 第三方
    preview: Callable[[list[DeliveryItem], dict[str, str]], list[dict]]
    publish: Callable[[list[DeliveryItem], dict[str, str]], DeliveryPublishResult]
    config_schema: dict = field(default_factory=dict)   # 前端自動 render 設定表單
    description: str = ""
```

### `backend/plugin_api/model.py`
```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

# ---- sync one-shot（stage 生成用）----
@dataclass(frozen=True)
class ModelAdapter:
    model_choice: str
    invoke: Callable[[str], str]
    is_available: Callable[[], bool]
    description: str
    max_context_tokens: int
    prompt_budget_tokens: int
    response_budget_tokens: int


# ---- async long-running（M5 實作 agent 用）----
OnLog = Callable[[str], None]
OnEvent = Callable[[object], None]      # TelemetryEvent；M5 細化型別


@dataclass
class RunResult:
    exit_code: int
    last_output: str = ""
    cancelled: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.cancelled and not self.timed_out


class HookAbort(Exception):
    """pre_run 拋出以拒絕一次 run（如推受保護分支）。"""
    def __init__(self, hook_name: str, reason: str) -> None:
        super().__init__(f"[{hook_name}] {reason}")
        self.hook_name = hook_name
        self.reason = reason


class ToolHook(ABC):
    """tool hook ABC，全部預設 no-op；子類只覆寫需要的。"""
    name: str = ""

    def pre_run(self, runner_name: str, argv: list[str],
                env: dict[str, str]) -> Optional[list[str]]:
        return None     # None = 原樣通過；list = 改寫 argv；raise HookAbort = 拒絕

    def post_run(self, runner_name: str, result: object) -> None:
        return None

    def on_log_chunk(self, runner_name: str, chunk: str) -> Optional[str]:
        return chunk    # None = 丟棄該行；str = 轉發（可改寫，如 redact）


class AgentRunner(ABC):
    """M5：async 長時 runner（跑 CLI 子程序、串流、可取消）。"""
    name: str = ""
    last_output_max_bytes: int = 64_000

    @abstractmethod
    def build_argv(self, *, cwd: str, prompt: str) -> list[str]: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    async def run(self, *, cwd: str, prompt: str, timeout: int,
                  on_log: OnLog, on_event: Optional[OnEvent] = None,
                  hooks: Optional[list[ToolHook]] = None) -> RunResult:
        """base class 統一驅動子程序 / 串流 / timeout / 取消。"""
        ...

    async def cancel(self) -> None: ...
```

### `backend/plugin_api/runner.py`
```python
from __future__ import annotations
from typing import Optional, Protocol

from plugin_api.harness import HarnessResult
from plugin_api.stage import AgentSpec


class HarnessRunner(Protocol):
    """host 注入給 stage handler 的唯一 AI 入口（只通 sync one-shot harness）。
    Stage 5 async runtime 的任何符號永不出現在這個 Protocol —— 這是兩層
    runtime 隔離的主要防線（plugin 連 conn 都拿不到）。"""

    def harnessed_step(self, *, telemetry_stage: str, operation: str,
                       prompt: str, metadata: dict,
                       max_iterations: int = 1) -> HarnessResult: ...

    def get_agent_for_stage(self, stage_id: str) -> Optional[AgentSpec]: ...

    def feedback_block(self, *, telemetry_stage: str, operation: str) -> str: ...

    def render_prompt(self, prompt_key: str,
                      replacements: dict[str, str]) -> str: ...
```

### `backend/plugin_api/host.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol

from plugin_api.harness import ValidatorFn
from plugin_api.integration import IntegrationSpec
from plugin_api.model import AgentRunner, ModelAdapter
from plugin_api.runner import HarnessRunner
from plugin_api.stage import AgentSpec, StageSpec
from plugin_api.workflow import WorkflowSpec


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str
    host_api: str                   # semver range，如 ">=1.0,<2.0"
    entry_module: str               # 單一 import 入口，內含 register(host)
    requires_plugins: tuple[str, ...] = ()
    contributes: dict = field(default_factory=dict)


class PluginHost(Protocol):
    """傳給每個 plugin 的 register(host)。各 register_* 寫進對應 registry，
    並在 plugin_contributions 記一筆 ownership。"""
    plugin_id: str

    def register_stage(self, spec: StageSpec) -> None: ...
    def register_workflow(self, spec: WorkflowSpec) -> None: ...
    def register_agent(self, spec: AgentSpec) -> None: ...           # seed 預設 agent
    def register_integration(self, spec: IntegrationSpec) -> None: ...
    def register_model_adapter(self, adapter: ModelAdapter) -> None: ...
    def register_runner(self, choice: str, cls: type[AgentRunner]) -> None: ...  # 存 class
    def register_validator(self, telemetry_stage: str, operation: str,
                           fn: ValidatorFn) -> None: ...
    def register_hook(self, event: str, fn) -> None: ...
    def make_harness_runner(self, thread_id: str) -> HarnessRunner: ...
```

### `backend/plugin_api/__init__.py`
```python
"""Plugin-facing API surface. Plugins import ONLY from here."""
from plugin_api.common import DeliveryItem, DeliveryPublishResult
from plugin_api.harness import (
    HarnessContext, HarnessResult, HarnessValidationOutcome, ValidatorFn,
    SEVERITY_WARN, SEVERITY_FAIL,
)
from plugin_api.stage import (
    AgentSpec, SkillSpec, StageContext, StageResult, StageChatResult,
    StageSpec, StageGenerateFn, StageRefineFn, StageChatFn,
)
from plugin_api.workflow import WorkflowSpec
from plugin_api.integration import IntegrationSpec
from plugin_api.model import (
    ModelAdapter, AgentRunner, RunResult, ToolHook, HookAbort, OnLog, OnEvent,
)
from plugin_api.runner import HarnessRunner
from plugin_api.host import PluginHost, PluginManifest

__all__ = [
    "DeliveryItem", "DeliveryPublishResult",
    "HarnessContext", "HarnessResult", "HarnessValidationOutcome", "ValidatorFn",
    "SEVERITY_WARN", "SEVERITY_FAIL",
    "AgentSpec", "SkillSpec", "StageContext", "StageResult", "StageChatResult",
    "StageSpec", "StageGenerateFn", "StageRefineFn", "StageChatFn",
    "WorkflowSpec", "IntegrationSpec",
    "ModelAdapter", "AgentRunner", "RunResult", "ToolHook", "HookAbort",
    "OnLog", "OnEvent", "HarnessRunner", "PluginHost", "PluginManifest",
]
```

---
---

# 附錄 B — API Request/Response Schema（pydantic，`backend/api_models.py`）

> 通用 endpoint，無 per-stage 特例。沿用 ver2 形狀，但移除硬編碼的
> `prd`/`architecture`/`stories` 欄位，改成 `stage_id` 參數化 + list 回應，
> 以支援 plugin 帶來的開放 stage 集合。

### Stage 操作（對應 `POST /api/stage/{stage_id}/generate|refine|chat`、`PUT /api/stage/{stage_id}/{thread_id}`）
```python
from typing import Optional
from pydantic import BaseModel


class StageGenerateRequest(BaseModel):
    thread_id: str
    model_choice: str = "claude-cli"


class StageRefineRequest(BaseModel):
    thread_id: str
    model_choice: str = "claude-cli"
    instruction: str
    preview_only: bool = False


class StageManualEditRequest(BaseModel):
    content: str
    change_source: str = "manual_edit"
    reviewed: bool = False
    instruction: str = ""
    change_context: str = ""


class StageChatRequest(BaseModel):
    thread_id: str
    user_input: str
    model_choice: str = "claude-cli"
    preview_only: bool = False
    focus_section: Optional[str] = None


class StageActionResponse(BaseModel):
    stage_id: str
    artifact: str
    state_extra: dict = {}          # 如 prd 的 {"is_ready": true}


class StageChatResponse(BaseModel):
    ai_response: str
    updated_content: Optional[str] = None


class StageHistoryMessage(BaseModel):
    role: str
    content: str


class StageHistoryResponse(BaseModel):
    messages: list[StageHistoryMessage]
```

### Stage catalog / status / summary（對應 `GET /api/stages`、`/api/stage/statuses|summaries/{thread_id}`）
```python
class StageCatalogItem(BaseModel):
    id: str
    label: str
    icon: str
    description: str = ""
    depends_on: list[str]
    downstream: list[str]
    supports_chat: bool
    source: str                     # "builtin" / "plugin"
    plugin_id: Optional[str] = None
    operations: list[str]           # ["generate","refine","chat"]
    telemetry_stage: str = ""


class StageCatalogResponse(BaseModel):
    stages: list[StageCatalogItem]


class StageStatusItem(BaseModel):
    stage_id: str
    status: str                     # draft / approved / needs_revision


class StageStatusesResponse(BaseModel):
    statuses: list[StageStatusItem]     # 取代 ver2 寫死的 prd/architecture/stories 三欄


class SetStageStatusRequest(BaseModel):
    status: str


class StageSummaryItem(BaseModel):
    stage_id: str
    status: str
    has_content: bool
    blocked_by: list[str]
    downstream_stages: list[str]
    downstream_impacted: list[str]
    stale: bool
    open_comments: int
    last_updated_at: Optional[float] = None
    last_revision_source: Optional[str] = None
    last_revision_summary: Optional[str] = None
    last_revision_reviewed: bool = False


class StageSummariesResponse(BaseModel):
    stages: list[StageSummaryItem]      # 依 active workflow 順序，通用 list
```

### Workflow（對應 `GET/POST/PUT /api/workflows`、`POST /api/projects/{thread_id}/workflow`）
```python
class WorkflowStageRef(BaseModel):
    stage_id: str
    depends_on: list[str] = []
    agent_id: Optional[str] = None


class WorkflowDef(BaseModel):
    id: str
    label: str
    description: str = ""
    stages: list[WorkflowStageRef]
    source_plugin: str = ""


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowDef]


class WorkflowUpsertRequest(BaseModel):
    label: str
    description: str = ""
    stages: list[WorkflowStageRef]


class BindWorkflowRequest(BaseModel):
    workflow_id: str
```

### Agent / Skill（對應 `GET/POST /api/agents`、`PUT /api/agents/{id}`、`/api/agents/{id}/skills`）
```python
class SkillResponse(BaseModel):
    skill_id: str
    name: str
    description: str
    body: str
    version: str


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    role: str
    system_prompt: str
    model_choice: str
    max_iterations: int
    enabled: bool
    tools: list[str]
    skills: list[SkillResponse]


class AgentCreateRequest(BaseModel):       # ver2 缺的「建立 agent」
    name: str
    role: str
    system_prompt: str = ""
    model_choice: str = "claude-cli"
    max_iterations: int = 1
    tools: list[str] = []


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    model_choice: Optional[str] = None
    max_iterations: Optional[int] = None
    enabled: Optional[bool] = None
    tools: Optional[list[str]] = None


class AgentSkillsUpdateRequest(BaseModel):
    skill_ids: list[str]
```

### Plugin 管理（對應 `GET /api/plugins`、`PATCH /api/plugins/{id}`）
```python
class PluginProvides(BaseModel):
    stages: list[str] = []
    workflows: list[str] = []
    agents: list[str] = []
    integrations: list[str] = []


class PluginResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str
    enabled: bool
    provides: PluginProvides
    requires_rebuild: bool = False      # plugin 帶前端 renderer 時 true
    load_error: Optional[str] = None    # 載入失敗原因（host_api 不符 / import 例外）


class PluginListResponse(BaseModel):
    plugins: list[PluginResponse]


class PluginToggleRequest(BaseModel):
    enabled: bool
```

### 錯誤格式（沿用 ver2，`backend/api_errors.py`）
```python
def error_detail(category: str, message: str, **extra: object) -> dict[str, object]:
    detail: dict[str, object] = {"category": category, "message": message}
    detail.update(extra)
    return detail
```
所有結構化錯誤回應 = `{"detail": {"category", "message", ...extra}}`。
依賴未滿足 → `400 {"category": "missing_<stage_id>", "message": "<Label> must exist first."}`。
validator 阻擋（保留 ver2 形狀，預設不觸發因 warn-only）→ `422`：
```json
{"detail": {
  "category": "validator_blocked",
  "message": "<stage label> blocked by validator: <v1>, <v2>",
  "run_id": "<run_id>",
  "outcomes": [{"validator": "...", "severity": "...", "message": "...", "fix_hint": "..."}]
}}
```

### SSE event 格式（`GET /api/stage/stream/{thread_id}`，沿用 ver2 但 stage→stage_id 動態）
開連線先送一個 `snapshot`，之後逐筆轉發 `stage_event_bus` 的事件，event 的 `type` 當 SSE event 名：
```jsonc
// snapshot（開連線一次）
{"type":"snapshot","thread_id":"...","stages":[{"stage_id":"prd","status":"approved"}],
 "in_flight":[{"stage_id":"architecture","operation":"generate_architecture","started_at":...}],"ts":...}
// stage_status
{"type":"stage_status","thread_id":"...","stage_id":"architecture","status":"draft","ts":...}
// stage_event
{"type":"stage_event","thread_id":"...","stage_id":"architecture","event_type":"generated","detail":"","ts":...}
// stage_running（running True=開始 / False=結束；False 時帶 kind: succeeded|failed）
{"type":"stage_running","thread_id":"...","stage_id":"architecture","running":true,"ts":...}
{"type":"stage_running","thread_id":"...","stage_id":"architecture","running":false,"kind":"succeeded","ts":...}
```

---
---

# 附錄 C — 完整 DB Schema DDL（SQLite 單檔 WAL，`backend/persistence/schema.sql` 或集中 migration）

> 唯一碰 DB 的是 DAL 層。沿用 ver2 表，差異：(1) **新增 `stage_artifacts`**
> 取代 ver2 的 checkpoint blob；(2) stage 表的 `stage` 欄統一改名 `stage_id`；
> (3) `projects` 加 `workflow_id`；(4) `agents` 加 `tools`；(5) 新增
> `workflow_definitions` / `plugin_contributions` / `plugin_state`。

### 核心（host / stage / workflow / plugin）
```sql
CREATE TABLE IF NOT EXISTS projects (
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    workflow_id TEXT,                                   -- NULL → lazy default
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

-- 新！artifact 正文直接存表（取代 ver2 LangGraph checkpoint blob）
CREATE TABLE IF NOT EXISTS stage_artifacts (
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (thread_id, stage_id)
);

CREATE TABLE IF NOT EXISTS stage_status (
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',           -- draft/approved/needs_revision
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (thread_id, stage_id)
);

CREATE TABLE IF NOT EXISTS stage_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_messages ON stage_messages (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS stage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL,
    stage_id   TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail     TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_events ON stage_events (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS stage_revisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        TEXT NOT NULL,
    stage_id         TEXT NOT NULL,
    source           TEXT NOT NULL,                     -- ai_revision / manual_edit / generated
    summary          TEXT NOT NULL DEFAULT '',
    instruction      TEXT NOT NULL DEFAULT '',
    reviewed         INTEGER NOT NULL DEFAULT 0,
    downstream_reset TEXT NOT NULL DEFAULT '',          -- JSON list of stage_id
    content_length   INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stage_revisions ON stage_revisions (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS stage_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    stage_id    TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_stage_comments ON stage_comments (thread_id, stage_id, id);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id            TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    stages_json   TEXT NOT NULL DEFAULT '[]',           -- JSON: [{stage_id, depends_on[], agent_id?}]
    source_plugin TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS plugin_contributions (
    plugin_id       TEXT NOT NULL,
    capability_type TEXT NOT NULL,                      -- stage/workflow/agent/integration/...
    capability_id   TEXT NOT NULL,
    PRIMARY KEY (plugin_id, capability_type, capability_id)
);

CREATE TABLE IF NOT EXISTS plugin_state (
    plugin_id TEXT PRIMARY KEY,
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

### Agent 客製化（沿用 ver2，`agents` 加 `tools`）
```sql
CREATE TABLE IF NOT EXISTS agents (
    agent_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL,
    system_prompt  TEXT NOT NULL DEFAULT '',
    model_choice   TEXT NOT NULL DEFAULT 'claude-cli',
    max_iterations INTEGER NOT NULL DEFAULT 1,
    enabled        INTEGER NOT NULL DEFAULT 1,
    tools          TEXT NOT NULL DEFAULT '[]',          -- JSON list（給 tool-using agent）
    created_at     REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at     REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_agents_role ON agents (role);

CREATE TABLE IF NOT EXISTS skills (
    skill_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '1.0',
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS agent_skills (
    agent_id   TEXT NOT NULL,
    skill_id   TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills (agent_id, sort_order);
```

### Sync-AI 遙測（generic harness，沿用 ver2 原樣）
```sql
CREATE TABLE IF NOT EXISTS harness_runs (
    run_id        TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    stage         TEXT NOT NULL,                        -- 遙測 stage（specify/design/deliver）
    operation     TEXT NOT NULL,
    model_choice  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,                        -- succeeded/failed
    error_code    TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    ended_at      REAL NOT NULL,
    parent_run_id TEXT,                                 -- fix-loop 串接
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_harness_runs_thread ON harness_runs (thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_harness_runs_stage ON harness_runs (thread_id, stage, operation);

CREATE TABLE IF NOT EXISTS harness_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_harness_events_run ON harness_events (run_id, event_id);

CREATE TABLE IF NOT EXISTS harness_validation_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    validator  TEXT NOT NULL,
    severity   TEXT NOT NULL,                           -- warn / fail
    message    TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '{}',
    fix_hint   TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_harness_validation_run ON harness_validation_results (run_id, id);
```

### Async 實作 agent（M5，**獨立命名空間**，與上面 sync 遙測不共用 run_id 形狀）
M5 才建。沿用 ver2 `implementation_persistence.py` 的表：`impl_sessions`（= ver2
`implementation_sessions`，含 milestone / repo / branch / phase / pr_url / review_state…）、
`impl_messages`、`impl_runs`、`milestones`、`milestone_sessions`、`telemetry_events`
（kind/phase/iteration/tokens/cost）、`validation_results`（欄位是 `outcome` + `run_id INTEGER`，
**刻意**不同於 harness 的 `severity` + `run_id TEXT`）、`delivered_issues`、`delivery_publish_locks`。
逐欄 DDL 直接移植自 ver2 `implementation_persistence.py:187-531`。

---
---

# 附錄 D — Prompt 資產全文（逐字移植自 ver2）

> 放 `backend/plugins/builtin_core_stages/prompts/`。host 的
> `render_prompt(key, replacements)` 只做 `{{KEY}}` → value 字串替換，不做其他
> 模板邏輯（harness 不渲染模板）。以下為**逐字**內容，可直接落檔。第三方 plugin
> 帶自己的 `prompts/`，`render_prompt` 的 cache key 須含 plugin 目錄以免多 profile 撞快取。

## 佔位符對應表

| prompt 檔 | stage / 用途 | operation | 佔位符 | host 填入 |
|---|---|---|---|---|
| `sa_system.md` | SA discover base system prompt | — | （無） | 直接用，或被 `agent.system_prompt` 覆蓋 |
| `sa_chat.md` | SA discover 對話 wrapper | `chat_sa` | `SYSTEM_PROMPT` / `CONVERSATION_TEXT` / `FOCUS_SECTION` | sa_system 內容 / stage_messages 串接 / 聚焦前綴 |
| `sa_amendment_prefix.md` | PRD 已存在時的修訂模式前綴 | — | `CURRENT_PRD` | 現有 PRD artifact |
| `prd_refine.md` | PRD refine | `refine_prd` | `PRD_DRAFT` / `INSTRUCTION` | prd artifact / 使用者指令 |
| `architect.md` | 架構 generate | `generate_architecture` | `PRD_DRAFT` | prd artifact |
| `architecture_refine.md` | 架構 refine | `refine_architecture` | `PRD_DRAFT` / `ARCHITECTURE_DRAFT` / `INSTRUCTION` | prd / architecture artifact / 指令 |
| `arch_chat.md` | 架構 chat | `chat_architecture` | `PRD_DRAFT` / `ARCHITECTURE_DRAFT` / `CONVERSATION_TEXT` / `FOCUS_SECTION` | 同上 + 對話串接 |
| `user_stories.md` | 故事 generate | `generate_user_stories` | `PRD_DRAFT` / `ARCHITECTURE_DRAFT` | prd / architecture artifact |
| `user_stories_refine.md` | 故事 refine | `refine_user_stories` | `PRD_DRAFT` / `ARCHITECTURE_DRAFT` / `USER_STORIES_DRAFT` / `INSTRUCTION` | 三 artifact + 指令 |
| `stories_chat.md` | 故事 chat | `chat_stories` | `PRD_DRAFT` / `ARCHITECTURE_DRAFT` / `USER_STORIES_DRAFT` / `CONVERSATION_TEXT` / `FOCUS_SECTION` | 三 artifact + 對話 |
| `delivery_items.md` | 故事 → 交付項目 JSON | `parse_delivery_items` | `USER_STORIES_DRAFT` | stories artifact |
| `validators/architecture_covers_prd.md` | 架構覆蓋 PRD 的 LLM validator | （validator） | `PRD_DRAFT` / `ARCHITECTURE_DRAFT` | 經 `ctx.metadata` 傳入 |
| `validators/stories_link_architecture.md` | 故事連回架構的 LLM validator | （validator） | `ARCHITECTURE_DRAFT` / `USER_STORIES_DRAFT` | 經 `ctx.metadata` 傳入 |
| `validators/retrospective_audit.md` | harness 回顧稽核（離線） | （工具） | `LOOKBACK_DAYS` / `METRICS_SUMMARY` / `FAILURE_SAMPLES` | 遙測彙整 |

> Chat 的回傳協定：`arch_chat` / `stories_chat` 用 `[CONTENT_START]...[CONTENT_END]`
> 包整份更新後 artifact（無標記則為純對話）；`sa_system` 用句尾 `[PRD_READY]` 標記
> PRD 完成。`StageChatResult.updated_artifact` / `StageResult.state_extra` 對應這些訊號。

### `prompts/sa_system.md`
~~~markdown
You are a strict and meticulous System Analyst (SA) at a professional software factory.

Your ONLY job is to transform raw, often vague user requirements into a comprehensive, unambiguous Product Requirements Document (PRD).

## Rules you must follow:
0. LANGUAGE RULE: You MUST always respond in the same language the user writes in. If the user writes in Chinese (Traditional or Simplified), respond entirely in Chinese. If in English, respond in English. Never switch languages unless the user does first.
1. NEVER make assumptions about requirements. If anything is unclear, ask precise, numbered clarifying questions.
2. You MUST probe for Non-Functional Requirements (NFRs) including:
   - Security (authentication, authorization, data privacy)
   - Scalability & Concurrency (expected load, peak users)
   - Performance (response time SLAs)
   - Availability & Reliability (uptime requirements)
   - Data Retention & Compliance (GDPR, HIPAA, etc.)
3. If the user's input is vague or missing any of these NFR areas, you MUST ask about them before writing the PRD.
4. Only when ALL requirements (functional + non-functional) are crystal clear and complete, generate the PRD.

## CRITICAL — Questionnaire Format Rule:
If you find that there are 2 or more technical details or Non-Functional Requirements (NFRs) that the user needs to clarify, DO NOT ask them as a plain text list. Instead, you MUST output a JSON formatted questionnaire wrapped exactly in a fenced code block with the language tag `json-questionnaire`, like this:

```json-questionnaire
{
  "title": "Requirements Clarification",
  "questions": [
    { "id": "q1", "category": "Security", "question": "What authentication method should be used?" },
    { "id": "q2", "category": "Performance", "question": "What is the expected concurrent user load?" }
  ]
}
```

Only use plain text questions when there is exactly 1 question to ask. For 2 or more questions, always use the `json-questionnaire` block — never a plain numbered list.

## PRD Format (use ONLY when requirements are complete):
# Product Requirements Document

## 1. Overview
[Brief description of the product]

## 2. Goals & Objectives
[Bulleted list of goals]

## 3. Functional Requirements
- Use individually traceable IDs:
- `FR-1`: [Detailed requirement]
- `FR-2`: [Detailed requirement]

## 4. Non-Functional Requirements
### 4.1 Security
- `NFR-1`: [Specific security requirement]
### 4.2 Performance
- `NFR-2`: [Specific performance SLA]
### 4.3 Scalability & Concurrency
- `NFR-3`: [Specific scalability requirement]
### 4.4 Availability & Reliability
- `NFR-4`: [Uptime SLA, disaster recovery]
### 4.5 Compliance & Data Retention
- `NFR-5`: [Regulatory requirement]

## 5. Operational / Safety Requirements
- `OPS-1`: [Operational safeguard, rollout, validation, or failure-handling requirement]

## 6. Out of Scope
[What is explicitly NOT included]

## 7. Open Questions
[Any remaining ambiguities, if none write "None"]

[PRD_READY]

CRITICAL: Append `[PRD_READY]` at the very end ONLY when the PRD is complete and all requirements are clarified. Do NOT append it during clarification questions.
~~~

### `prompts/sa_chat.md`
~~~markdown
{{SYSTEM_PROMPT}}

--- Conversation so far ---
{{CONVERSATION_TEXT}}
--- End of conversation ---
{{FOCUS_SECTION}}

SA Agent:
~~~

### `prompts/sa_amendment_prefix.md`
~~~markdown
AMENDMENT MODE: A PRD already exists. The user wants to add or modify requirements.
Your job is to understand the requested changes, ask clarifying questions if needed (using the json-questionnaire format for 2+ questions), then output the COMPLETE updated PRD incorporating both original and new requirements. Append [PRD_READY] when the updated PRD is complete.

Current PRD:
{{CURRENT_PRD}}
~~~

### `prompts/prd_refine.md`
~~~markdown
LANGUAGE RULE: You MUST respond in the same language as the PRD content. If the PRD is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the PRD is in English, respond in English.

You are a senior product requirements editor. Revise the PRD below according to the user's instruction.

Rules:
- Return the COMPLETE updated PRD, not a summary and not a diff.
- Keep the structure coherent and preserve unaffected sections unless the instruction requires changes.
- If the instruction asks to add new requirements, integrate them into the right sections.
- If the instruction asks to remove or change requirements, update the relevant sections accordingly.
- Keep functional, non-functional, and operational requirements individually traceable with IDs such as `FR-1`, `NFR-2`, and `OPS-1`.
- When adding new requirements, continue the numbering instead of restarting it.
- Do not include commentary before or after the PRD.

Current PRD:
{{PRD_DRAFT}}

User instruction:
{{INSTRUCTION}}
~~~

### `prompts/architect.md`
~~~markdown
LANGUAGE RULE: You MUST respond in the same language as the PRD content. If the PRD is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the PRD is in English, respond in English.

You are a Staff Software Architect. Design a system architecture that is **proportional to the actual scope** the PRD describes — neither under-engineered nor over-engineered.

## Step 1 — Classify the project tier (HARD RULE, output before anything else)

Read the PRD carefully and pick exactly ONE tier. The tier governs every downstream choice (modularization, build complexity, abstraction depth):

| Tier | When it applies | Default modularization | LOC ceiling |
|---|---|---|---|
| **T0** prototype / POC / shell | ≤ 5 screens, no data layer, no API integration, demo / verification only, single developer | **Single Gradle/build module** with packages (e.g. `com.x.app.feature.home`, `com.x.app.core.designsystem`). Theme tokens live in one `Theme.kt` file. | ≤ 3,000 LOC |
| **T1** MVP / pilot | ≤ 15 screens, single team, ≤ 2 backend integrations, time-to-market priority | `app` + **ONE** `core` module (combined designsystem + ui + nav) + 1 `feature` module per major flow (≤ 5 features). | ≤ 15,000 LOC |
| **T2** production / multi-team | > 15 screens, multi-team, regulated, scaling, multi-platform | Full Now-in-Android style: `app` + `core/*` per concern + `feature/*` per flow + shared libraries. | unbounded |

**Output the tier as the very first line of your response, in this exact shape (parser-friendly, do not reword):**

```
**Project tier**: T<N> — <one-sentence justification grounded in PRD facts>
```

Example:

```
**Project tier**: T0 — PRD lists 4 screens, "UI only, no data layer", demo verification.
```

### Tier-specific anti-patterns (do NOT recommend these for the chosen tier)

**T0 anti-patterns**:
- More than 1 Gradle/build module. A 4-screen UI shell does NOT need `core/designsystem` + `core/navigation` + `core/ui-components` + `feature/*` × N. Each module costs ~100 LOC of Gradle / plugin / namespace boilerplate and adds nothing when the team is one person and the app is < 5 screens.
- Design tokens split into separate files of < 30 lines (`Shape.kt` 13 lines, `Elevation.kt` 14 lines, …). Group them into one `Theme.kt` until growth justifies splitting.
- "Extract shared X" intermediate steps that first implement X N times in feature/* then extract to core/*. For T0, X is shared from day one inside the same module.
- Speculative `core/network`, `core/database`, `core/analytics` for a no-data-layer shell.

**T1 anti-patterns**:
- Speculation modules (`core/network` before any HTTP call lands; `core/database` before any persisted entity). Add them when the second consumer appears, not before.
- Per-token-family modules (designsystem split into colors / typography / shape / spacing). T1 has ONE core module that owns all of them.
- More than 5 `feature/*` modules. If the user flow legitimately needs more, reconsider whether some of them are sub-screens within a single feature module.

**T2 anti-patterns**:
- Single-module monolith. Use full NIA modularization.
- Coupling `feature/*` to each other directly. Routes go through `core/navigation`.

### Override

If the PRD explicitly demands a specific modularization (e.g. "use NIA template", "single module", "split per feature"), respect the user's instruction BUT call out the mismatch with the inferred tier in a short paragraph titled `## Tier override`, citing the PRD line that forced it.

If the PRD is ambiguous (e.g. "4 screens now, possibly more later"), pick the LOWER tier and add a `## Tier upgrade trigger` paragraph listing the concrete future condition that would justify moving up (e.g. "Move to T1 when a real network layer lands or a second team joins").

## Step 2 — Architecture document

After the tier line, produce the full architecture doc with:

- **Technical Evaluation** — fit between PRD requirements and platform constraints.
- **Tech Stack selection** — language / framework / library choices, calibrated to the tier (T0 picks the smallest dependency set that does the job; T2 picks for scale, observability, multi-team workflow).
- **System Architecture** — text description.
- **Module / package layout** — concrete tree (use the chosen tier's default; deviations need a one-line justification each).
- **Dependency direction** — explicit arrow notation (`app → feature/* → core`).
- **At least one mermaid diagram** wrapped in a markdown code block with language `mermaid`.
- **Build & verification baseline** — state, concretely for the chosen stack: how dependencies (incl. test/build tooling) are declared and locked; how the full test suite is run from a clean checkout; that CI runs the test suite (not only lint); and, if containerized, that the image builds and starts. Downstream the project is gated by a clean-environment integration run (fresh checkout → install → full test suite → build), so the module/package layout must use ONE consistent import convention and MUST NOT name a package after a standard-library module.

Every architectural decision (extra module, extra abstraction layer, third-party library, microservice boundary) MUST trace back to either a PRD requirement ID OR the chosen tier's defaults. If it traces to neither, drop it.

---

PRD:
{{PRD_DRAFT}}
~~~

### `prompts/architecture_refine.md`
~~~markdown
LANGUAGE RULE: You MUST respond in the same language as the PRD and Architecture content. If the content is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the content is in English, respond in English.

You are a Staff Software Architect revising an existing architecture draft.

Rules:
- Return the COMPLETE updated architecture document, not a diff and not an explanation.
- Preserve useful sections unless the instruction requires changes.
- Keep the architecture aligned with the PRD.
- Include Mermaid diagrams if they are still relevant after the revision.

## Project tier preservation (HARD RULE)

The original architecture should start with a line in the shape:

```
**Project tier**: T<N> — <justification>
```

When refining:

- **Preserve the tier line if the scope did NOT change.** Keep it as the first line of the revised document.
- **Upgrade tier (T0 → T1, T1 → T2) only if the user's instruction or the PRD genuinely added scope** (e.g. "add real backend", "expand to multi-team", "support 30 screens"). Update the justification to cite the new scope evidence.
- **Downgrade tier (T2 → T1, T1 → T0) only if the user's instruction explicitly asked to simplify** (e.g. "this is just a demo, simplify", "merge into one module"). Update the justification accordingly.
- **If the existing draft has no tier line** (legacy doc), infer the tier from PRD facts and add the line — using the same shape — at the top of the revised doc.
- Tier-specific anti-patterns from the architect prompt still apply on refine (no over-modularization for T0, no speculation modules for T1, etc.).

PRD:
{{PRD_DRAFT}}

Current Architecture:
{{ARCHITECTURE_DRAFT}}

User instruction:
{{INSTRUCTION}}
~~~

### `prompts/arch_chat.md`
~~~markdown
LANGUAGE RULE: Respond in the same language as the PRD and Architecture content.

You are a Staff Software Architect in a discussion about the system architecture for a software project.

You have access to the current PRD and the current architecture draft as context.

Your role:
- Answer questions about architectural decisions, trade-offs, and rationale.
- Suggest improvements when asked.
- When the user asks you to make changes to the architecture, produce a fully updated architecture document and wrap it with the exact markers below — nothing else outside the markers should contain the document.

When returning updated architecture content, use this exact format:
[CONTENT_START]
<full updated architecture markdown here>
[CONTENT_END]

Rules for updates:
- Always return the COMPLETE updated architecture, not a diff.
- Preserve all relevant sections unless the user instructs otherwise.
- Keep Mermaid diagrams updated if they are affected.
- Keep the architecture aligned with the PRD.

If the user is only asking questions or discussing (not requesting changes), respond conversationally without the [CONTENT_START]/[CONTENT_END] markers.

---

PRD:
{{PRD_DRAFT}}

Current Architecture:
{{ARCHITECTURE_DRAFT}}

---

Conversation so far:
{{CONVERSATION_TEXT}}
{{FOCUS_SECTION}}
~~~

### `prompts/user_stories.md`
~~~markdown
LANGUAGE RULE: You MUST respond in the same language as the PRD and Architecture content. If the content is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the content is in English, respond in English.

You are a Senior Product Manager and Agile Coach. Based on the following PRD and System Architecture, produce a complete set of User Stories organized by Epic.

## Output structure (HARD RULE — parsers depend on EXACT headings):

The forge front-end and the publish-to-GitHub / GitLab / Jira pipeline both parse this document with strict regexes. **If you deviate from the heading shapes below, the front-end shows `0 STORIES / 0 SECTIONS` and the publish flow uploads zero issues — even though your prose is correct.**

You MUST emit headings in exactly these shapes (in this nesting order):

| Element | Markdown shape | Example |
|---|---|---|
| Document title | `# <project name> — User Stories` | `# MotoCam Android UI Port — User Stories` |
| Milestone (optional grouping) | `## Milestone N — <title>` | `## Milestone 1 — 首屏可視 (目標 ≤35h)` |
| Epic | `## Epic N: <title>` | `## Epic 1: 專案骨架 & 建置設定` |
| Story | `### Story N.M — <title>` | `### Story 1.1 — Gradle 專案骨架` |

Key shape constraints, each enforced by a parser regex you cannot see:

* Story heading is **`### Story` (H3)**, NOT `#### Story` (H4), NOT `**Story 1.1**` (bold paragraph), NOT `Story 1.1` (no markup). The literal text `### Story` must appear at the start of the line.
* Epic heading is **`## Epic` (H2)**, NOT `### Epic` (H3). The publish path groups stories by H2 Epic headings; H3 Epics are invisible to it.
* The em-dash separator (` — `) between number and title in story headings is what the title extractor strips. Use it.
* Milestones are optional — if you use them, they're H2 alongside Epics (same level), not a parent of Epics. Group epics under milestones by ordering, not nesting.

Story body fields keep their existing format (As a / I want / so that / **Acceptance Criteria** / **Reference** / **Requirement IDs** / **Senior RD Estimate** / **Depends on**) — those are loose-text fields, not heading-parsed.

## Output Format Requirements:
- Group stories under clearly labeled Epics (e.g., ## Epic 1: User Authentication)
- Each story must follow the format: **As a [role], I want [goal] so that [benefit]**
- Each story must include:
  - **Acceptance Criteria** (bulleted list of testable conditions)
    - **Preferred shape**: `AC-N: Given <precondition>, When <trigger>, Then <expected>`. This Gherkin form lets the implementation agent auto-generate an executable pytest stub that gates the fix-loop on real behaviour — not just LLM self-assessment.
    - Use the Gherkin form whenever the criterion describes a runtime behaviour (input → action → outcome). Examples: API responses, state transitions, validation errors, route guards.
    - Keep freeform bullets for criteria that genuinely cannot be tested by code (pixel diff thresholds, design-token equivalence, manual-only UX checks). The agent falls back to LLM verification for these.
  - **Requirement IDs** (list the original PRD requirement IDs such as `FR-1`, `NFR-2`, `OPS-1`)
  - **Senior RD Estimate** (ideal engineering hours for one senior RD; allow `0.5`–`4` hour values only)
- Cover all functional requirements from the PRD
- Include edge cases and error handling stories where relevant
- If the PRD already includes requirement IDs, every story must reference the matching IDs explicitly.
- Do not use Story Points unless the source material explicitly requires them for compatibility.

### Project bootstrap story (REQUIRED — first story of the first epic)

Emit one early "project scaffold" story whose acceptance criteria make the project **runnable in a clean environment**, because the implementation pipeline runs a clean-env integration gate (fresh checkout → install deps → full test suite → build) after the milestone. Its AC must include:
- All test/build dependencies are declared in the manifest and the lockfile is committed (e.g. `requirements-dev.txt` / `package-lock.json` / gradle wrapper). Don't assume tools are pre-installed.
- A CI workflow exists that **runs the test suite** (not only lint/format/guardrail).
- Test runner works from a bare invocation in a clean checkout (e.g. `pytest.ini`/`pyproject` sets `pythonpath`/`testpaths`; the gradle wrapper is committed).
- One consistent import / module-resolution convention across the repo.
- No package is named after a standard-library module (Python: `secrets`/`types`/`json`/…).
- If a `Dockerfile` is in scope, it COPYs every top-level module the app imports and the image starts (passes its healthcheck).

## Project tier propagation (HARD RULE — read this BEFORE story sizing)

The Architecture document's first line declares the project tier in the shape:

```
**Project tier**: T<N> — <justification>
```

Read this line. The tier governs how aggressively you split stories. Apply the matching rule below INSTEAD OF blindly applying the "Story sizing" defaults that follow.

- **T0** (prototype / shell, single module): a story can produce **one whole subsystem inside one file** — `Theme.kt` containing colours, typography, shape, spacing, elevation all together IS one story, not 4–5 stories. Splitting per-token-family is a T0 anti-pattern. Aim for ~ 8–15 total stories for the entire deliverable.
- **T1** (MVP, app + one core + few features): split per **screen** and per **shared subsystem**, but keep design tokens together in one `Theme.kt` story unless the file would exceed ~ 300 lines. Aim for ~ 15–35 total stories.
- **T2** (production, full modularization): the existing "Story sizing" rules below apply as written — per-token-family splits, per-component-extraction, per-route-wiring are all legitimate stories.

If the Architecture document does NOT have a tier line (legacy or skipped), infer the tier from PRD facts and apply the matching rule. Note this in the document title with `(tier inferred: TN)`.

The implementation agent budget (10–15 min × 2 attempts) still applies as a HARD ceiling. The tier rule decides where the ceiling sits inside that budget; it never lifts the ceiling.

## Story sizing (HARD RULE — affects whether the AI implementation agent can actually finish them):

Every story you emit will be implemented by an autonomous coding agent that runs Claude CLI in a fixed-budget loop (10–15 minutes per attempt, two attempts total before the story is marked failed and skipped). A story that takes a senior human "one day" or "two days" is, in practice, undeliverable in this loop and will be auto-cancelled.

Therefore:

1. **Maximum size per story: 4 engineering hours** (`Senior RD Estimate` ≤ `4`). If you find yourself writing `1 day` / `2 days` / `0.5 day` — STOP and split.

2. **One concrete subsystem per story.** Examples of stories that MUST be split:
   - "Establish MotoCamTheme (Color + Typography + Shape + Spacing)" → 4–5 stories, one per token family, plus one that wires them into a `MotoCamTheme` composable.
   - "Build Login + Signup + Forgot password" → 3 stories.
   - "Set up Gradle project + Version Catalog + ktlint + CI" → 4 stories (or treat scaffolding as one story and CI as another).

3. **Each Acceptance Criterion must be a single concrete testable assertion**, not a feature umbrella. "App uses MotoCamTheme, doesn't apply Material defaults" is an umbrella — split into:
   - "`MotoCamColors.kt` exports `lightScheme: ColorScheme` and `darkScheme: ColorScheme`."
   - "`MotoCamTheme` composable accepts `darkTheme: Boolean` and selects scheme."
   - "`MainActivity.setContent` wraps content in `MotoCamTheme`."

4. **Order stories so each can be implemented without merging in changes from a later story.** If story B truly depends on story A, list B AFTER A and call out the dependency in B's body ("Depends on: Story A.X — assumes `MotoCamColors.kt` already exists").

5. **Stories that the AI agent CANNOT do alone** (require running on a physical device, accessing an external CMS, designing visuals from scratch, doing manual QA, legal/license review) should still appear in the output BUT be tagged with a leading `[HUMAN]` in the story title so the user can filter them before shipping. Don't pretend they're AI-implementable just to keep the list short.

## Design-source propagation (HARD RULE for visual-port projects)

When the PRD describes the deliverable as a **1:1 visual port / clone / replica** of an existing website / app / Figma mockup / competitor product (key phrases include "做一個跟…一模一樣的", "1:1 還原", "clone this", "mirror this", "pixel-perfect copy of", etc.), the design source URL is non-negotiable context for EVERY single story you emit — not just the obviously-UI ones. Reasons:

- The implementation agent doesn't read the PRD when working on a story; it only sees the story body plus a short Project context block. The URL has to be discoverable from the story alone.
- "Data layer" / "build config" / "theme tokens" stories LOOK non-UI but they DO drive visuals: a `LiveViewUiState` defines what shows on screen, a `build.gradle` declares the Compose / font / image-loading libs that constrain the rendering, a `MotoCamColors.kt` IS the design surface. AI implementers consistently misread these as "not UI" and skip the source check, then emit Material 3 defaults that don't match the spec.
- Therefore: include a `Design source` heading in EVERY story body when the PRD is a visual port, even if the story's surface description sounds backend-shaped. The implementation agent decides what to fetch based on what's relevant.

For non-visual-port projects (greenfield app, no existing reference design), this section doesn't apply — design source is optional and only mentioned in stories that explicitly require external visual reference.

**Interaction with the design-tokens pre-resolve step**: when forge creates the milestone for a visual-port project, it runs a one-off Claude CLI sub-agent that actually renders the design source URL (via Claude Preview MCP) and extracts a `# Design tokens` block (palette / typography / theme / layout patterns). That block is appended to every implement session's Project context. So stories should describe what each acceptance criterion *needs* in semantic terms ("uses the accent token for active state", "matches the display typography for the speed reading") rather than hard-coding hex codes or font names — those come from the design-tokens block, not the story body. Stories still include the `Design source` URL heading for traceability and as a fallback when the tokens block is missing (e.g. on a server where the MCP isn't installed).

**Interaction with the design-source mirror**: alongside the tokens block, milestone-create also mirrors the **original** HTML / SVG / images / fonts of the design into `<repo>/.design-source/` on the working tree of every session. Stories about UI screens / theming / icons MUST include a **Reference** block pointing the implementation agent at the relevant mirror files, so the agent doesn't have to guess paths. Required shape:

```
**Reference**
- Original DOM: `.design-source/html/<slug>.html`     (the rendered HTML for the screen this story covers)
- Pixel reference: `.design-source/screens/<slug>.png` (screenshot for visual cross-check)
- Icons / images / fonts: `.design-source/assets/`     (drop in the specific sub-path if the story is about a single asset)
```

`<slug>` is the route mapping documented in `.design-source/INDEX.md`. The implementation agent will `Read` these files first before writing code, so the more precise the path you cite, the less the agent guesses. For non-screen stories (data layer, config, mock data) the Reference block can be omitted unless the design source is genuinely informative.

## Vertical-slice ordering (HARD RULE for visual-port projects)

The single biggest failure mode of past visual-port runs has been "30 stories of theme / component / scaffolding before any viewable screen, so the user reviews an empty `Text("Home")` for hours and loses trust in the agent." DO NOT REPRODUCE THIS.

**Required structure for visual-port projects**:

1. **Milestone 1 (M1) ends with ONE visible screen rendered on the emulator** — typically the home / landing screen, with header + hero element + nav shell, even if some values are inlined. ≤ ~12 stories total. The final story in M1 must have an AC like "在 emulator 啟動後即見 motocam 風格 header（**非** `Text("Home")` 空殼）" plus a pixel-diff check (see below). Subsequent milestones (M2, M3, …) backfill polish, abstract tokens out of inlined values, build out other screens.

2. **Inline first, abstract later.** In M1, the HomeScreen / first screen MAY hardcode typography values, spacing, corner radii directly in the composable. M2 then introduces `MotoTypography.kt` / `MotoSpacing.kt` / `MotoShape.kt` / `MotoElevation.kt` as a refactor PR — extracting M1's inlined values. Do NOT block M1 on building all token files first; that's the trap.

3. **Reusable components (`MotoCard` / `MotoButton` / `MotoSwitch` / …) deferred until needed by a second concrete usage site.** Don't build a component library on speculation in M1. M1's HomeScreen MAY use inline composables; M2 then extracts shared shapes once HomeScreen + at least one other screen need them.

4. **Pixel-diff AC on every viewable-screen story.** Every story that produces or modifies on-screen pixels must have a quantitative acceptance criterion of the shape:

   > 與 `.design-source/screens/<slug>.png` <region> 區塊像素 diff ≤ 2px

   Whole-page stories use `<slug>_full.png` (the scrolled / expanded screenshot). The diff threshold is a hard ceiling, not a target — agents will optimise to clear the threshold; pick something tight (≤ 2px tolerance, ΔE < 5 colour) so they actually have to look at the reference.

5. **Senior RD Estimate budget for M1**: aim for total M1 ≤ 35 engineering hours. Above that, the milestone won't ship in a single forge run and the user loses the "I see UI after one milestone" payoff.

## Banned patterns for visual-port projects (do NOT emit these stories)

Forge ships two mechanisms that supersede older work-arounds. Stories that re-implement those mechanisms are pure waste and confuse the implement agent. Do not emit:

* ❌ **Stories that write forge-internal JSON intermediates** like `design_tokens/colors.json`, `design_tokens/typography.json`, `design_tokens/spacing.json`, `design_tokens/shapes.json`. The `# Design tokens` block in Project context already provides every hex / font / size value as a hard constraint, and the `.design-source/html/<slug>.html` mirror contains the original `:root` CSS variables inline. A JSON intermediate is a REDUNDANT translation hop that re-introduces the lossy "describe spec → re-translate" pipeline this whole system exists to eliminate. Theme files (`MotoColors.kt` etc.) should read directly from those two sources, not from a forge-side JSON file.

* ❌ **Stories that download or copy assets from the live site** like `scripts/extract_assets.sh`, `scripts/fetch_icons.sh`, an `assets/` task that hits the web. The milestone-create mirror already pulls every linked SVG / PNG / font into `.design-source/assets/`. A "download assets" story is duplicate work and often falls over CORS / rate-limits.

* ❌ **A "build theme" / "build components" / "build screen" combined story** that says "implement HomeScreen layout" with no Reference block and no pixel-diff AC. Either split it per concrete subsystem or write the Reference block and pixel-diff AC.

* ❌ **MotoTypography / MotoSpacing / MotoShape / MotoElevation as separate token-file stories scheduled BEFORE any viewable HomeScreen story.** They go in M2, not M1 (see Vertical-slice ordering above).

## Example stories (visual-port project)

Example for an M1 theme story:

```
**As an** Android user, **I want** the Compose theme to match the source website's palette, **so that** later screens render with motocam colours instead of Material defaults.

**Acceptance Criteria**
- `ui/theme/MotoCamColors.kt` exports `lightScheme: ColorScheme` and `darkScheme: ColorScheme`.
- Values come **directly from `.design-source/html/index.html` `<style>` `:root` CSS variables** and the Project context `# Design tokens` block. NO forge-internal JSON intermediate file.
- Each colour matches the source within ΔE < 5.

**Reference**
- Original CSS: `.design-source/html/index.html` (`<style>` block, `:root` rules)
- Pixel reference: `.design-source/screens/index.png`
- Design tokens: Project context `# Design tokens` block

**Senior RD Estimate**
- 3

**Depends on**: Story 1.2 (project skeleton)
```

Example for the M1 "first viewable screen" story:

```
**As a** user, **I want** the Home screen header / hero region to match motocam on first launch, **so that** M1 ships with a visible motocam-style page, not a `Text("Home")` placeholder.

**Acceptance Criteria**
- `ui/screens/home/HomeScreen.kt` exports `HomeScreen()` composable containing the header / hero region with all visible elements from the source (logo / title / right-corner icons / hero text / hero background).
- Colour comes from `MotoCamTheme.colorScheme`; typography / spacing / corner radii MAY be inlined for M1 (M2 refactors them into MotoTypography / MotoSpacing / MotoShape).
- Icons are imported from `.design-source/assets/` and converted to VectorDrawable XML — not redrawn from a verbal description.
- `MotoCamNavHost` `home` route is changed from `Text("Home")` to `HomeScreen()`.
- On Pixel 6 API 35 emulator after launch the user sees a motocam-style header (NOT `Text("Home")`).
- Pixel diff vs `.design-source/screens/index.png` (header region) ≤ 2px.

**Reference**
- Original DOM: `.design-source/html/index.html`
- Pixel reference: `.design-source/screens/index.png`
- Assets: `.design-source/assets/`
- Design tokens: Project context `# Design tokens` block

**Senior RD Estimate**
- 4

**Depends on**: <colors story, theme story, nav story>
```

Example for an apparently-backend story in the same visual-port project:

```
**As a** developer, **I want** `HomeUiState` to expose the dashboard fields the screen renders, **so that** mock data and a future real data layer share the same shape.

**Acceptance Criteria**
- `HomeUiState` includes the field set visible in `.design-source/html/index.html` (REC indicator, BSD chip, GPS dot, speed reading, …).
- All fields backed by static mock factories for v1.

**Reference**
- Original DOM: `.design-source/html/index.html` (read to enumerate which fields are actually on screen)

**Senior RD Estimate**
- 2
```

Example showing Gherkin AC (preferred shape for behavioural criteria — the implementation agent will auto-generate a pytest stub for each):

```
**As a** logged-out user, **I want** protected routes to redirect me to /login, **so that** I don't see other users' dashboards.

**Acceptance Criteria**
- AC-1: Given the user has no session cookie, When they request `/dashboard`, Then the response status is 302 and `Location` header is `/login`.
- AC-2: Given an expired session token, When the user requests `/dashboard`, Then the response status is 401 and the body contains `session expired`.
- AC-3: Given a valid active session, When the user requests `/dashboard`, Then the response status is 200.

**Requirement IDs**: FR-3, NFR-1

**Senior RD Estimate**
- 2
```

PRD:
{{PRD_DRAFT}}

System Architecture:
{{ARCHITECTURE_DRAFT}}
~~~

### `prompts/user_stories_refine.md`
~~~markdown
LANGUAGE RULE: You MUST respond in the same language as the PRD, Architecture, and User Stories content. If the content is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the content is in English, respond in English.

You are a Senior Product Manager and Agile Coach revising an existing user stories document.

Rules:
- Return the COMPLETE updated user stories document, not a diff and not an explanation.
- Preserve unaffected epics and stories unless the instruction requires changes.

## Output structure (HARD RULE on refine too — parsers depend on EXACT headings):

When emitting the revised document, every story / epic heading MUST match these shapes (front-end parser + publish-to-tracker pipeline both regex-match these literally — getting any of them wrong shows the user `0 STORIES / 0 SECTIONS` and uploads zero GitHub issues):

| Element | Markdown shape |
|---|---|
| Document title | `# <project name> — User Stories` |
| Milestone (optional) | `## Milestone N — <title>` |
| Epic | `## Epic N: <title>` (H2, **not** `### Epic`) |
| Story | `### Story N.M — <title>` (H3, **not** `#### Story`, **not** `**Story N.M**`) |

If the input document violates these (bold paragraphs instead of H3, `### Epic` instead of `## Epic`, etc.), REWRITE the headings as part of the refine. Preserve the story body content exactly — the violation is structural, not semantic.
- Keep the output organized by Epic.
- Each story must keep the format:
  - As a [role], I want [goal] so that [benefit]
  - Acceptance Criteria
  - Requirement IDs
  - Senior RD Estimate
- Preserve existing Requirement IDs when they are still valid, and add them where missing if the PRD supports traceability.
- Do not reintroduce Story Points unless the user explicitly asks for them.

## Project tier propagation (HARD RULE on refine too)

Read the Architecture document's first line for the tier declaration:

```
**Project tier**: T<N> — <justification>
```

Apply the matching rule:

- **T0**: design tokens belong in one `Theme.kt` story, NOT split per token family. If the existing document has T0 architecture but T2-style splits (e.g. separate stories for `Colors.kt` / `Typography.kt` / `Shape.kt` / `Spacing.kt`), MERGE them into one story as part of this refine.
- **T1**: design tokens kept together unless `Theme.kt` would exceed ~300 lines. Per-screen and per-shared-subsystem splits are fine.
- **T2**: existing per-family splits stay as written.

If the Architecture has no tier line (legacy), infer from PRD facts and apply the matching rule. Do not preserve T2-style over-splitting in a T0 / T1 project just because the input had it — the refine is the right place to correct.

## Story sizing (HARD RULE — applies on refine too):

Stories are implemented by an autonomous coding agent with a 10–15 minute per-attempt budget. Multi-day stories cannot finish in that window and get auto-cancelled. When refining:

1. **Cap: 4 engineering hours per story** (`Senior RD Estimate` ≤ `4`, expressed in hours not days). If you encounter any existing story with `1 day` / `2 days` / `0.5 day` in the input, SPLIT it as part of this refine — don't preserve the oversized estimate.
2. **One concrete subsystem per story.** Example: a single "Establish theme (color + typography + shape + spacing)" story should be split into 4–5 sub-stories, each producing one token family or one composable.
3. **Each AC must be a single testable assertion**, not a feature umbrella ("App uses theme, doesn't apply Material defaults" → expand into one assertion per concrete file/composable touched).
4. **Tag manually-required stories** (physical device runs, external CMS access, legal review, manual QA) with a leading `[HUMAN]` in the title so the user can filter them before sending to the implementation agent.
5. **If the user instruction asks for a single big story**, push back politely in the instruction-response, or honour the request but mark it `[HUMAN]` so it doesn't get fed to the agent.

## Design-source propagation (HARD RULE for visual-port projects, applies on refine):

When the PRD describes the deliverable as a **1:1 visual port / clone / replica** of an existing website / app / Figma mockup / competitor product (key phrases: "做一個跟…一模一樣的", "1:1 還原", "clone this", "pixel-perfect copy of"), EVERY story emitted from the refine — not just obviously-UI ones — must include a `Design source` heading pointing to the source URL. Rationale:

- "Data layer" / "build config" / "theme tokens" stories LOOK non-UI but DO drive visuals (a `LiveViewUiState` defines what shows on screen; a `MotoCamColors.kt` IS the design surface). The implementation agent reads the story body in isolation; without the URL it falls back to Material 3 defaults.
- The refine must repair existing stories that lack this section. If you encounter a UI / data-layer / build / asset story in a 1:1-port project that has no `Design source` heading, ADD it as part of this refine. Don't silently preserve the omission.

## Design-source mirror Reference block (HARD RULE on refine for visual-port projects):

Forge mirrors the design source's HTML / SVG / images / fonts / screenshots into `<repo>/.design-source/` at milestone-create time. Every story about UI screens / theming / icons / visual layout MUST include a `**Reference**` block of this shape:

```
**Reference**
- Original DOM: `.design-source/html/<slug>.html`
- Pixel reference: `.design-source/screens/<slug>.png`
- Icons / images / fonts: `.design-source/assets/`
```

`<slug>` matches `.design-source/INDEX.md`. On refine, ADD the Reference block to any visual-port story missing it — including theme-file stories (`MotoCamColors`, `MotoTypography`, …), nav stories, generic component stories. The implementation agent reads `.design-source/` directly via `Read` to avoid re-translating from prompt summaries.

## Vertical-slice ordering (HARD RULE on refine for visual-port projects):

If the input user stories show this anti-pattern — many theme / token / component / scaffolding stories scheduled BEFORE any viewable screen — REORDER them as part of the refine. Past visual-port runs ground through 25–30 foundation stories before the user saw any UI, then concluded "agent is broken" because they were comparing `Text("Home")` placeholders to a polished motocam screenshot. Required output structure:

1. **Milestone 1 (M1) ends with ONE visible screen rendered on the emulator** — typically Home / landing with header + hero + nav shell, even if some values are inlined. ≤ ~12 stories total. Last story in M1 must have an AC like "在 emulator 啟動後即見 motocam 風格 header（**非** `Text("Home")` 空殼）" plus a pixel-diff check.

2. **Inline first, abstract later.** M1's HomeScreen MAY hardcode typography / spacing / corner radii inline. M2 then extracts them into `MotoTypography.kt` / `MotoSpacing.kt` / `MotoShape.kt` / `MotoElevation.kt`. Do NOT block M1 on building all token files first.

3. **Reusable components deferred until a second usage site exists.** Don't build a `MotoCard` / `MotoButton` / `MotoSwitch` library in M1 on speculation; extract them in M2 once two screens demand the shape.

4. **Pixel-diff AC on every viewable-screen story**: `與 .design-source/screens/<slug>.png <region> 區塊像素 diff ≤ 2px` (whole-page = `<slug>_full.png`). Quantitative, not aspirational.

5. **M1 budget total ≤ 35 engineering hours.**

## Banned patterns on refine (DELETE these stories if present):

* ❌ Stories writing forge-internal JSON intermediates: `design_tokens/colors.json`, `design_tokens/typography.json`, `design_tokens/spacing.json`, `design_tokens/shapes.json`. The Project context `# Design tokens` block + `.design-source/html/<slug>.html` mirror already provide ground truth. JSON intermediate = lossy translation hop; theme `.kt` files read directly from those two sources, not from forge-side JSON.

* ❌ Stories that download or copy assets from the live site (`scripts/extract_assets.sh`, `scripts/fetch_icons.sh`, etc.). The milestone-create mirror already pulls every linked SVG / PNG / font into `.design-source/assets/`.

* ❌ Token-file stories (`MotoTypography` / `MotoSpacing` / `MotoShape` / `MotoElevation` as separate `.kt` files) scheduled before any viewable HomeScreen story. They belong in M2; M1's HomeScreen inlines its values.

When refining, REMOVE these stories outright (not "mark as low priority" — remove). If their absence breaks a `Depends on:` chain on another story, also rewrite that downstream story's dependency to point at the upstream `.design-source/` file or the Project context tokens block instead.

PRD:
{{PRD_DRAFT}}

Architecture:
{{ARCHITECTURE_DRAFT}}

Current User Stories:
{{USER_STORIES_DRAFT}}

User instruction:
{{INSTRUCTION}}
~~~

### `prompts/stories_chat.md`
~~~markdown
LANGUAGE RULE: Respond in the same language as the PRD and User Stories content.

You are a Senior Product Manager in a discussion about the user stories backlog for a software project.

You have access to the current PRD, the architecture, and the current user stories draft as context.

Your role:
- Answer questions about story scope, acceptance criteria, prioritization, and rationale.
- Suggest improvements when asked.
- When the user asks you to make changes to the user stories, produce a fully updated user stories document and wrap it with the exact markers below.

When returning updated user stories content, use this exact format:
[CONTENT_START]
<full updated user stories markdown here>
[CONTENT_END]

Rules for updates:
- Always return the COMPLETE updated user stories, not a diff.
- Preserve all existing stories unless the user instructs otherwise.
- Keep acceptance criteria aligned with the PRD and architecture.
- Use consistent story format (As a / I want / So that).
- When updating stories, preserve or add explicit Requirement IDs and a Senior RD Estimate for each story.
- Do not fall back to Story Points unless the user explicitly requests tracker-specific estimation.

If the user is only asking questions or discussing (not requesting changes), respond conversationally without the [CONTENT_START]/[CONTENT_END] markers.

---

PRD:
{{PRD_DRAFT}}

Architecture:
{{ARCHITECTURE_DRAFT}}

Current User Stories:
{{USER_STORIES_DRAFT}}

---

Conversation so far:
{{CONVERSATION_TEXT}}
{{FOCUS_SECTION}}
~~~

### `prompts/delivery_items.md`
~~~markdown
Parse the following User Stories Markdown into a JSON array of delivery items. Each item must have:
- "title": short issue title
- "body": full story text. **Preserve the original markdown formatting of the Acceptance Criteria section** — keep the `**Acceptance Criteria**` (or `## Acceptance Criteria`) heading and use markdown bullets (`- `) for each criterion, optionally prefixed with an `AC-N:` id. The implementation agent's verifier extracts these via regex; converting them to plain text or numbered (`1.`, `2.`) lists makes the verifier silently skip AC verification on every issue. Other sections (As/I want/so that, Reference, Requirement IDs, Senior RD Estimate, Depends on) can be loosely formatted.
- "estimate": integer tracker-compatible estimate if available; if only Senior RD days are available, derive the closest compatibility estimate
- "senior_rd_days": number of ideal engineering days for one senior RD
- "group": epic or feature group
- "requirement_refs": an array of Requirement IDs such as `FR-1`, `NFR-2`, `OPS-1`
- "labels": an array of simple lowercase labels appropriate for trackers

Required AC section shape inside ``body``:

```
**Acceptance Criteria**
- AC-1: <first criterion>
- AC-2: <second criterion>
- AC-3: <third criterion>
```

The `AC-N:` prefix is optional but recommended — it gives the verifier stable IDs to reference in feedback. If the source story uses numbered or unprefixed bullets, convert them to this shape while preserving the criterion text verbatim.

Return ONLY a valid JSON array with no markdown wrapper, no explanation, no code fences.

User Stories:
{{USER_STORIES_DRAFT}}
~~~

### `prompts/validators/architecture_covers_prd.md`
~~~markdown
You are a strict architectural-review auditor. Compare the PRD below
against the Architecture document below. Decide which requirements the
PRD declares but the architecture does NOT visibly cover.

A requirement is "covered" if one of the following is true:

* The architecture document mentions the requirement ID (FR-*, NFR-*,
  or OPS-*) directly, OR
* The architecture describes a component, flow, constraint, or
  non-functional posture that clearly addresses the requirement's
  intent (matching by behavior, not by ID).

LANGUAGE RULE: respond in the same language the architecture is
written in (Traditional Chinese if it is, English otherwise). The
``rationale`` field is the only free-form text — keep each entry to
one short sentence.

OUTPUT FORMAT: respond with a single JSON object and nothing else. No
markdown fence, no leading "Sure, here is", no trailing explanation.
Schema:

```
{
  "missing": ["FR-3", "NFR-2"],
  "rationale": {
    "FR-3": "短說明，為何認定未覆蓋",
    "NFR-2": "短說明，為何認定未覆蓋"
  }
}
```

If the architecture covers every requirement, return:

```
{"missing": [], "rationale": {}}
```

Rules:

1. Every ID in ``missing`` must also appear as a key in ``rationale``.
2. Do not invent requirement IDs — only ones that actually appear in
   the PRD count.
3. Cross-referencing by *intent* is allowed and encouraged. If the PRD
   says "FR-2 the user can reset password" and the architecture
   describes a Forgot-Password component without naming "FR-2", that
   counts as covered.
4. NFR / OPS items often map to architectural postures (HA, observability,
   secret management) rather than named components. Look for those.
5. Be strict but not pedantic — partial coverage with a clear, named
   component still counts as covered.

---

# PRD

{{PRD_DRAFT}}

---

# Architecture

{{ARCHITECTURE_DRAFT}}

---

JSON response (and only JSON):
~~~

### `prompts/validators/stories_link_architecture.md`
~~~markdown
You are auditing whether the user stories properly link back to the
Architecture document. The goal is to catch stories that reference
requirement IDs (FR-*, NFR-*, OPS-*) which DO NOT correspond to any
component, module, contract, or constraint described in the
architecture.

A story's ``requirement_refs`` are "linked" if either:

* The referenced ID appears in the architecture document directly, OR
* The architecture describes a component or flow that clearly
  implements the behavior the requirement implies.

A story whose refs cannot be linked is flagged here. Stories without
any refs at all are NOT this validator's concern (the structural
``stories_completeness`` validator already covers that case).

LANGUAGE RULE: respond in the same language the stories document
uses. The ``reason`` field is short free-form text — one sentence per
entry, in the same language.

OUTPUT FORMAT: respond with a single JSON object and nothing else. No
markdown fence, no leading prose. Schema:

```
{
  "unmapped_stories": [
    {
      "story_heading": "Story 3: 重設密碼",
      "refs": ["FR-3"],
      "reason": "FR-3 沒有出現在架構文件，且找不到對應元件"
    }
  ]
}
```

If every story's refs link to architecture, return:

```
{"unmapped_stories": []}
```

Rules:

1. ``refs`` MUST be the subset that failed to link, not the full ref
   list of the story. A story with refs ``[FR-1, FR-3]`` where only
   FR-3 is missing should report ``refs: ["FR-3"]``.
2. ``story_heading`` should match the actual ``### Story ...`` heading
   in the stories document so the next iteration can locate it.
3. Cross-reference by intent. If the architecture describes a
   "password-reset flow" but never spells out "FR-3", treat FR-3 as
   linked.
4. Stories without any refs are out of scope — silently skip them.

---

# Architecture

{{ARCHITECTURE_DRAFT}}

---

# User stories

{{USER_STORIES_DRAFT}}

---

JSON response (and only JSON):
~~~

### `prompts/validators/retrospective_audit.md`
~~~markdown
You are a Harness retrospective auditor. The data below summarises
the most recent harness runs and their validator outcomes. Your job
is to identify recurring failure patterns and propose **concrete,
narrow** improvements that the maintainer can act on.

Audience: the engineer maintaining the harness (not end users).
Tone: direct, opinionated, no hedging. Skip pleasantries.

LANGUAGE RULE: respond in 繁體中文. Code identifiers, file paths,
and validator names stay in their original (English) form. Do not
mix in Simplified Chinese.

OUTPUT FORMAT: Markdown, no JSON, no code fence wrapping the whole
response. Use this structure exactly:

```
## 摘要

<2-3 sentences: which signals are loudest in this window>

## 觀察到的模式

- **<模式 1 標題>**: <一句敘述 + 為何重要>
- **<模式 2 標題>**: <...>
- (最多 5 條)

## 提案

每條提案包含三段。**只在你對該提案有信心時提出**；寧可少不要硬湊。

### 提案 1：<短標題>

- **動機**：<為何此提案能改善資料中觀察到的模式>
- **變更面**：<具體要動的檔案、prompt profile、validator 或 fix_hint，含路徑>
- **驗證方式**：<跑完之後如何確認改善：要看哪個指標、看哪段資料>

### 提案 2：<...>

(最多 4 條提案)

## 不建議現在做

- <如果有資料看起來像問題但實際是 noise / out-of-scope，列在這裡，避免下一輪 retrospective 重複建議>
```

Rules:

1. 每條提案必須**鎖定一個資料中的具體 pattern**。如果只是「整體可以更好」就不要列。
2. 提案的「變更面」要寫到「動 `backend/harness_validators.py:296` 的 prd_requirement_ids hint」這個粒度，不寫「改 prompt」這種空話。
3. 不要建議「新增更多 validator」除非你能指出**現有 validator 沒抓到的、資料中重複出現的問題**。
4. 不要建議模型升級或換 model — 那超出 Harness 範疇。
5. 如果資料量太少（少於 20 個 run），明確說明資料不足、列出累積更多 telemetry 的方法，不要為了交差硬擠提案。

---

# 資料窗

觀察區間：過去 {{LOOKBACK_DAYS}} 天

## Harness 執行統計

{{METRICS_SUMMARY}}

## Validator 詳細失敗樣本（最新先）

{{FAILURE_SAMPLES}}

---

請依上述格式輸出 markdown：
~~~

> M5 / 視覺埠邊角 prompt（未列全文，逐字移植自 ver2 同名檔，屬 M5 範圍）：
> `generate_test_stub.md`（AC → pytest stub）、`verify_against_ac.md`（靜態 AC 驗證）、
> `design_source_mirror.md`、`design_tokens_extractor.md`（視覺埠設計資產抽取）。

---
---

# 附錄 E — 完整目錄樹

### Backend
```
backend/
├── main.py                       # FastAPI app + 通用 endpoint；startup: migrate → load_plugins → bus.bind_loop
├── api_models.py                 # 附錄 B 的 pydantic models
├── api_errors.py                 # error_detail
├── workflow_engine.py            # WorkflowEngine: active_workflow_for / dispatch / compute_dependencies / _downstream_of
├── plugin_loader.py              # discover / host_api 檢查 / 拓樸排序 / 兩階段 register / 失敗隔離
├── plugin_host.py                # PluginHost 具體實作（register_* + make_harness_runner）
├── registries.py                 # STAGE/WORKFLOW/AGENT/INTEGRATION/MODEL_ADAPTER/RUNNER/VALIDATOR registry（各自形狀）
├── stage_event_bus.py            # in-process SSE pub/sub（per-thread fan-out；source of truth 仍是 DB）
├── plugin_api/                   # 附錄 A：plugin 唯一可 import 的介面
│   ├── __init__.py               # re-export
│   ├── common.py  harness.py  stage.py  workflow.py
│   ├── integration.py  model.py  runner.py  host.py
├── persistence/                  # DAL —— 唯一碰 DB 的層
│   ├── schema.sql                # 附錄 C 全部 CREATE TABLE
│   ├── migrations.py             # 集中 migration runner
│   ├── projects.py  stages.py    # stage_artifacts/status/events/revisions/comments/messages
│   ├── agents.py  workflows.py  plugins.py
│   └── harness.py                # harness_runs/events/validation_results
├── harness/                      # sync one-shot runtime（stage 生成用）
│   ├── runtime.py                # run_harnessed_model_step(_with_loop)
│   ├── validators.py             # VALIDATOR_REGISTRY + 結構 validator
│   ├── inferential.py            # LLM validator（warn-only、env kill switch）
│   ├── feedback.py               # build_validator_feedback_block（prior-run 再注入）
│   └── model_adapters.py         # MODEL_ADAPTERS + invoke_model
├── plugins/                      # 內建 + 第三方（同一套 API，無後門）
│   ├── builtin_integrations/     # M0：plugin.toml + register.py + github.py/jira.py/gitlab.py
│   ├── builtin_core_stages/      # M1-M2
│   │   ├── plugin.toml
│   │   ├── register.py           # register(host)：3 StageSpec + default WorkflowSpec + validator
│   │   ├── handlers.py           # generate/refine/chat handler（純函式，不碰 conn）
│   │   ├── prompt_builders.py    # build_*_prompt（呼叫 host.render_prompt）
│   │   └── prompts/              # 附錄 D 的 .md 資產
│   ├── builtin_agents/           # M2：register.py 帶 3 seed AgentSpec
│   └── builtin_implement/        # M5（可選）：async runner + 實作 stage
│       ├── plugin.toml  register.py  orchestrator.py
│       ├── runners/              # claude_runner.py / codex_runner.py
│       └── hooks/                # deny_branch.py / redact_secrets.py / tail_dispatch.py
├── async_runtime/                # M5：async long-running runtime（與 harness/ 嚴格分離，不 cross-import）
│   ├── runner_base.py            # AgentRunner 驅動（子程序/串流/timeout/cancel）
│   ├── telemetry.py  persistence.py   # impl_* 表（獨立 run_id 形狀）
└── tests/                        # 依賴推導 / 下游 reset / 雙詞彙 / 載入隔離 / AST 隔離 guard
```

### Frontend
```
frontend/app/
├── layout.tsx                    # mount StageCatalogProvider
├── page.tsx                      # workspace：catalog-driven + keyed StagesState（FORGE_UI 分支）
├── lib/
│   ├── flags.ts                  # FORGE_UI / NEXT_PUBLIC_DYNAMIC_STAGES
│   ├── stages/                   # catalog.ts / useStageCatalog.ts / schemas.ts(zod)
│   ├── workflow/                 # types.ts / api.ts
│   └── api.ts
├── components/
│   ├── forge/
│   │   ├── shell/                # ForgeShell / ForgeTopBar / ForgeStageStepper / ForgeSidebar / ForgeSplit
│   │   ├── stages/               # ForgeGenericStage.tsx + registry.ts + bodies/{GenericDocBody,ArchBody,StoriesBody}
│   │   ├── chat/                 # ChatBubble / ChatInput
│   │   ├── icons/                # index.tsx（Icons registry）+ resolveIcon
│   │   └── lib/                  # stageAdapter / messageAdapter / activityAdapter
│   └── agents/                   # AgentConfigPanel / NewAgentForm / SkillDualList
├── workflows/                    # page.tsx（列表）+ [id]/page.tsx（WorkflowEditor：清單+Up/Down+依賴multi-select）
├── agents/                       # page.tsx + [id]/page.tsx
├── plugins/                      # page.tsx（PluginCard gallery）
└── settings/                     # page.tsx
```

---
---

# 附錄 F — M0–M5 逐項 Task Checklist

### M0 — Plugin framework 地基（dogfood delivery）｜S
- [ ] 落 `backend/plugin_api/` 全部檔案（附錄 A），`from plugin_api import *` 可成功 import
- [ ] `persistence/`：schema.sql（附錄 C 核心 + agent 表）+ migrations runner + projects/plugins DAL
- [ ] `main.py` skeleton：startup 順序 migrate → `load_plugins()` → `bus.bind_loop()`
- [ ] `plugin_loader.py`：tomllib 解析 manifest、host_api semver 檢查（不符 skip+warn）、拓樸排序（環 skip）、兩階段 register、單 plugin 失敗隔離
- [ ] `plugin_host.py` + `registries.py`：先接 `register_integration` → INTEGRATION registry + 寫 `plugin_contributions`
- [ ] `plugins/builtin_integrations/`：plugin.toml + register.py + github/jira/gitlab（移植 ver2 preview/publish）
- [ ] endpoint：`GET /api/plugins`、`PATCH /api/plugins/{id}`、delivery preview/publish
- [ ] 前端：Next skeleton + `/plugins` gallery（PluginCard + enable/disable）
- [ ] ✅ 驗證：啟動 log 顯示 `loaded plugin builtin_integrations (3 integrations)`；delivery 對 github 行為正確；test：壞 plugin 不打掛 app、host_api 不符被 skip

### M1 — 一個 stage 端到端走通（PRD）｜L
- [ ] `registries.py` 加 STAGE registry；`plugin_host.make_harness_runner` + `HarnessRunner` 實作（內部包 `harness/runtime`，plugin 看不到 conn）
- [ ] `harness/runtime.py` + `model_adapters.py`（移植 ver2 `run_harnessed_model_step_with_loop` + adapters）
- [ ] `persistence/stages.py`：stage_artifacts / stage_status / stage_messages DAL
- [ ] `workflow_engine.py`：`dispatch()` 單 stage（查 spec → 取上游 → 跑 handler → 寫 artifact + revision + event + SSE）
- [ ] `plugins/builtin_core_stages/`：`prd` StageSpec + generate/refine/chat handler + prompt_builders + `prompts/`（附錄 D）
- [ ] 通用 endpoint：`POST /api/stage/{id}/generate|refine|chat`、`PUT /api/stage/{id}/{tid}`、`GET /api/stage/statuses|summaries`、`GET /api/stage/{id}/history/{tid}`、SSE `GET /api/stage/stream/{tid}`
- [ ] 前端：zod schemas + `StageCatalogProvider`/`useStageCatalog`（讀 `GET /api/stages`）+ `resolveIcon` + stepper 從 catalog 驅動 + `ForgeGenericStage` + keyed `StagesState` + generic handler
- [ ] ✅ 驗證：UI 對空 thread 生成/refine PRD、SA chat 互動；**重整頁面狀態仍在**（artifact 在 DB 非 checkpoint）；test：generate 後 artifact 落 `stage_artifacts`、status=draft、revision 記錄

### M2 — 補齊三 stage + 依賴 + workflow + validator｜M
- [ ] `architecture` / `stories` StageSpec + handler（含 `depends_on`、雙詞彙 `telemetry_stage`=design/deliver、validator metadata via `ctx.metadata`）
- [ ] `harness/validators.py`（結構）+ `harness/inferential.py`（LLM，warn-only），由 plugin `register_validator` 註冊
- [ ] `default` WorkflowSpec（prd→architecture→stories）由 builtin_core_stages 帶；`compute_dependencies`/`_downstream_of` 取代手寫 dict
- [ ] `plugins/builtin_agents/`：prd/architecture/stories 三個 seed AgentSpec
- [ ] ✅ 驗證：跑完整 PRD→架構→故事；改 architecture 正確清空並 reset stories；test：**每個內建 stage 的 `(telemetry_stage, operation)` 在 `VALIDATOR_REGISTRY` 有對應**（雙詞彙 guard）；通用 endpoint 對三 stage 逐欄正確

### M3 — workflow 編輯器 + agent 編輯/綁定｜M
- [ ] `persistence/workflows.py` + WORKFLOW registry + `workflow_definitions` 表；`projects.workflow_id`（lazy default）+ `active_workflow_for`
- [ ] workflow CRUD endpoint（`GET/POST/PUT /api/workflows`、`POST /api/projects/{tid}/workflow`）
- [ ] `/api/stage/statuses|summaries` 改成依 active workflow data-driven 列舉（取代寫死三 stage）
- [ ] 前端 `/workflows`：列表 + 編輯器（有序清單 + Up/Down + 每列依賴 multi-select〔只能勾前面〕+ 預覽 stepper + 綁 agent）
- [ ] agent 編輯擴充：`POST /api/agents`（新建）+ `NewAgentForm` + role/stage 綁定下拉
- [ ] ✅ 驗證：建「prd→architecture（跳過 stories）」workflow、綁新 thread，stepper/依賴/下游 reset 正確；老 thread（無 workflow_id）自動 default、行為不變

### M4 — 第三方 plugin 打包/分發 + 管理 UI｜M
- [ ] 第三方掃描 `backend/plugins/*/` + 可選 `importlib.metadata` entry-point 發現
- [ ] plugin 開發指南 + cookiecutter 範本（一個 example stage plugin，附 prompts/）
- [ ] AST 隔離 guard test：`plugins/*` 不得 import `async_runtime` / Stage 5 符號
- [ ] `/plugins` UI 完善：manifest 詳情、`requires_rebuild` badge、enable/disable 後 `useStageCatalog.refetch()`
- [ ] ✅ 驗證：example 第三方 stage plugin 丟進目錄 → 重啟 → 出現在 catalog 與 workflow builder、能 generate

### M5 —（可選）交付發佈 + 自動實作 agent｜M–L
- [ ] `async_runtime/`：`AgentRunner` 驅動（子程序/串流/timeout/cancel）+ telemetry + `impl_*` 表（**獨立 run_id 形狀**，不與 harness 共用）
- [ ] `plugins/builtin_implement/`：claude/codex runner + 「實作 stage」+ hooks（deny_branch/redact_secrets/tail_dispatch）
- [ ] fix-loop 硬上限（`MAX_FIX_ITERATIONS=3`）、AC verify、`prior_failures` 再注入、PR/MR 開立
- [ ] delivery publish 整合進交付 stage（preview-before-publish）
- [ ] ✅ 驗證：對一個 GitHub issue 跑實作 agent、開出 PR；推受保護分支被 `deny_branch` 擋；log 中秘密被 `redact_secrets` 遮蔽；`asyncio.create_task` 有強引用（不被 GC）

---

> **交付到此為止已是「打開即可逐項施工」的成品級。** 主體（§1-16）給設計與決策，
> 附錄 A 給可編譯 contract、B 給 API、C 給 DDL、D 給逐字 prompt、E 給目錄、F 給逐項任務。
> 第一個可交付里程碑：**M0 + M1**。
