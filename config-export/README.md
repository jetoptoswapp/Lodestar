# config-export — 自訂設定快照（版控用）

這裡是 Lodestar **執行期資料庫**（`backend/data/app.db`，被 gitignore）中「使用者自訂設定」的 JSON 快照，用來放進 git 做**備份 / 移轉 / 分享**。

> Lodestar 是「流程即資料」：agent / workflow 的**定義存在 DB**，不是程式碼。內建（seed）定義在 `backend/plugins/builtin_*`（在 git）；你透過 UI 新建或編輯過的，只在 DB（不在 git）。本目錄就是把後者匯出成可版控的形式。

## 內容

| 檔案 | 內容 |
|---|---|
| `agents.json` | `agents` 表（agent 定義：system_prompt / model / tools…）＋ `agent_skills`（技能綁定） |
| `workflows.json` | `workflow_definitions`（含開機 seed 進 DB 的 builtin，皆可編輯；`source_plugin=user` 者為純自訂） |
| `skills.json` | `skills` 表（自訂 skill；目前為空＝你的 skill 都是程式碼內建的） |

## 刻意排除（安全）

- ❌ **`integration_secrets`**（GitHub/GitLab token 密文）—— 不匯出。
- ❌ **`.keystore.key`**（解密金鑰）—— 不匯出。
- ❌ 專案內容（PRD/Stories/架構/實作 log）、附件、impl_work —— 不在此快照內（那屬於專案資料，不是「設定」）。

> 因為不含任何憑證，這份快照可安全進公開 repo。

## 如何更新此快照

```bash
backend/.venv/bin/python - <<'PY'
import sqlite3, json, os
con = sqlite3.connect("file:backend/data/app.db?mode=ro", uri=True); con.row_factory = sqlite3.Row
r = lambda q: [dict(x) for x in con.execute(q)]
ag = r("SELECT * FROM agents ORDER BY role, agent_id")
for a in ag:
    a["tools"] = json.loads(a.get("tools") or "[]")
wf = r("SELECT * FROM workflow_definitions ORDER BY created_at")
for w in wf:
    w["stages"] = json.loads(w.pop("stages_json") or "[]")
d = lambda n,o: open(f"config-export/{n}","w",encoding="utf-8").write(json.dumps(o,ensure_ascii=False,indent=2)+"\n")
d("agents.json", {"agents": ag, "agent_skills": r("SELECT * FROM agent_skills ORDER BY agent_id, sort_order")})
d("workflows.json", {"workflows": wf})
d("skills.json", {"skills": r("SELECT * FROM skills ORDER BY skill_id")})
print("re-exported")
PY
```

## 如何還原

目前沒有自動匯入端點，請以這份 JSON 為**真實來源**，在 UI 重建：

- **AGENTS 頁**：依 `agents.json` 重新填 agent（system_prompt / model / tools），並依 `agent_skills` 設定技能綁定。
- **WORKFLOWS 頁**：依 `workflows.json` 重建 workflow（`source_plugin=user` 的 `SWF` / `modify_existing` 是純自訂，務必先重建）；`stages` 陣列即各步驟與 `depends_on`。

> 真實設定永遠以 `backend/data/app.db` 為準；本目錄是某個時間點的快照，請在改動設定後重新匯出並 commit。
