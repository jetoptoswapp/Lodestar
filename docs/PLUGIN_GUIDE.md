# Lodestar Plugin 開發指南

Lodestar 的每個能力（stage / workflow / agent / integration / model adapter）都是 plugin。
內建與第三方走**同一套 API**——你寫的 plugin 跟 `builtin_core_stages` 沒有任何特權差別。

## 五條鐵則（plugin 作者必讀）

1. **只 import `plugin_api`**：plugin 永遠不准 import host 內部模組（`workflow_engine` / `plugin_host` / `persistence` …），也不可碰 async runtime。
2. **host owns all I/O**：plugin 拿不到 DB connection / 檔案系統。需要的資料由 host 透過 `StageContext` 餵進來；要寫的東西透過回傳值交給 host。
3. **data-driven**：core 不認得你的 stage 名稱。你的 `StageSpec.depends_on` 決定依賴鏈，host 自動推導下游 reset。
4. **失敗隔離**：你的 plugin 出錯（import / register 例外）只會被 skip + warn，不會打掛整個 app。
5. **雙詞彙對齊**：`StageSpec` 的 `telemetry_stage` / `*_operation` 要跟你註冊的 validator key 對得上，否則 validator 靜默不跑。

---

## Plugin 結構

最小一個 stage plugin：

```
my_plugin/
├── plugin.toml          # manifest（必須）
├── __init__.py
├── register.py          # register(host) 進入點
└── prompts/             # 你的 prompt 資產（render_prompt 從這裡讀）
    └── my_prompt.md
```

### plugin.toml

```toml
[plugin]
id = "my_plugin"                       # 全域唯一；英數 / 底線
name = "My Plugin"
version = "1.0.0"
description = "一句話描述"
host_api = ">=1.0,<2.0"                # 相容的 host API 範圍（semver）
entry_module = "plugins.my_plugin.register"   # 含 register(host) 的模組
requires_plugins = []                  # 依賴的其他 plugin id（拓樸排序用）

# 宣告貢獻（供 loader 預檢 + UI 預覽；真正的 spec 在 register() 建構）
[[contributes.stage]]
id = "my_stage"
label = "我的階段"
```

### register.py

```python
from plugin_api import PluginHost, StageContext, StageResult, StageSpec

def _generate(ctx: StageContext, run) -> StageResult:
    # ctx 帶 thread_id / upstream_artifacts / conversation / metadata.attachments…
    prompt = run.render_prompt("my_prompt.md", {
        "UPSTREAM": ctx.upstream_artifacts.get("prd", ""),
    })
    result = run.harnessed_step(
        telemetry_stage="my_telemetry", operation="generate_my_stage",
        prompt=prompt, metadata={"thread_id": ctx.thread_id}, max_iterations=1,
    )
    return StageResult(artifact=result.raw_output.strip())

MY_STAGE = StageSpec(
    id="my_stage", label="我的階段",
    telemetry_stage="my_telemetry",
    generate_operation="generate_my_stage",
    depends_on=("prd",),               # 依賴 PRD（host 自動擋上游缺失）
    artifact_key="my_stage",
    prompt_keys=("my_prompt.md",),
    generate=_generate,
)

def register(host: PluginHost) -> None:
    host.register_stage(MY_STAGE)
    # 可選：host.register_validator(telemetry_stage, operation, fn)
    # 可選：host.register_workflow(...) / register_agent(...) / register_integration(...)
```

`host` 提供的註冊方法：`register_stage` / `register_workflow` / `register_agent` /
`register_integration` / `register_model_adapter` / `register_runner` / `register_validator` / `register_hook`。

---

## 兩種安裝方式

### A. 丟進目錄（最簡單）

把 plugin 目錄複製進 `backend/plugins/`，重啟 backend。loader 啟動時掃
`backend/plugins/*/plugin.toml`，自動發現。

### B. pip install（分發給別人）

在你的套件 `pyproject.toml` 宣告 entry-point，指向含 `plugin.toml` 的 package：

```toml
[project.entry-points."lodestar.plugins"]
my_plugin = "my_pkg.plugin_dir"        # import 後該 package 目錄需含 plugin.toml
```

`pip install your-package` 後重啟 backend，loader 透過 `importlib.metadata` 掃 `lodestar.plugins`
entry-point group 自動發現。`/api/plugins` 會標 `discovery: "entry_point"`。

---

## 啟用 / 停用

- 所有 discovered plugin 預設啟用。
- 在 `/plugins` 頁面或 `PATCH /api/plugins/{id} {"enabled": false}` 停用。
- 停用 → 該 plugin 不 register，其 stage / workflow / agent 從 catalog 消失（hot-reload，免重啟）。
- 內建 plugin（`builtin_*`）不可停用。

---

## 完整範例

`backend/plugins/example_notes/` 是一個可運作的最小 stage plugin（「筆記整理」），
可直接複製改造。`plugin-template/` 是空白骨架（cookiecutter 風格佔位符）。

## 檢查清單

- [ ] `plugin.toml` 的 `id` 全域唯一
- [ ] `entry_module` 指向實際的 `register(host)` 函式
- [ ] 只 import `plugin_api`，無 host 內部模組
- [ ] prompt 放 `prompts/`，用 `run.render_prompt(key, replacements)` 讀
- [ ] 若有 validator：`telemetry_stage` / `operation` 與 `StageSpec` 對齊
- [ ] `depends_on` 只列真實上游（host 會自動擋缺失 + reset 下游）
