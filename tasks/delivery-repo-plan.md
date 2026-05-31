# Token-only credential + 每專案綁 delivery repo

## Context
現況把 repo 跟 token 綁在同一份 integration credential（config_schema 有 repo+token）= 一把憑證綁死一個 repo。delivery 用 config["repo"]、implement 用 req.target_repo 手填，都假設 repo 已存在、未跟專案綁定。

目標：credential 只存 token；repo 是 per-project（thread）設定，可「開新 repo（名稱+public/private/internal）」或「指向既有 owner/repo」，lazy 在要交付時才建 repo。GitHub + GitLab 都做到底（含發 issue + 開 PR/MR）。使用者決策：建專案可設 + 事後可改；GitHub 與 GitLab 都完整。

## 已驗證現況（行號）
- schema：migrate() 純 executescript(schema.sql) + CREATE TABLE IF NOT EXISTS → 加欄位不套用既有 DB，需在 migrate() 補 ALTER TABLE try/except fallback。
- projects 表（schema.sql:6）：thread_id/name/workflow_id/created_at。DAL dal.py:133-173 回 dict。
- IntegrationSpec（plugin_api/integration.py:8-14）：target/preview/publish/config_schema/description（frozen）。
- _publish_github（builtin_integrations/register.py:48-125）：urllib+Bearer PAT POST /issues 用 config["repo"]。_publish_stub（gitlab/jira）success=False。
- delivery endpoint（app.py:883-960）：cfg=_effective_config → parse_stories_to_delivery_items(target_project=cfg["repo"]) → preview/publish。
- implement（app.py:996-1027 / orchestrator.start_session / github_pr.make_github_pr_opener）：target_repo 來自 ImplementStartRequest.target_repo 手填。
- 前端：建專案/rename 用 PromptDialog；NewWorkflowModal（page.tsx:2939）多欄位表單範本；selStyle（page.tsx:2839）；ThreadRow（page.tsx:1373）有 rename/delete 按鈕；project CRUD 散在 page.tsx 用 apiFetch。

## 設計
### projects 加欄（schema.sql + migrate ALTER fallback）
delivery_target(github/gitlab) / repo_mode(new/existing) / repo_full_name / repo_owner(org/group，空=個人) / repo_visibility(public/private/internal) / repo_created(0/1，lazy 旗標)

### IntegrationSpec 擴展（加法）
create_repo: Optional[Callable[[dict,str,str,str],str]] = None  # (config, name, visibility, owner) -> repo_full_name

### builtin_integrations
- _create_github_repo：owner 空→POST /user/repos；否則 POST /orgs/{owner}/repos；payload {name, private: visibility!="public", auto_init: true}（auto_init 讓 repo 有初始 main，worktree clone 才有 base）→ 回 full_name。
- _create_gitlab_repo：POST /projects {name, visibility, namespace_id?}（PRIVATE-TOKEN header）→ 回 path_with_namespace。
- _publish_gitlab（取代 stub）：POST /projects/{url-encoded path}/issues。
- token-only config_schema：github→只 token；gitlab→token + base_url(opt)。jira 不動。

### resolve_project_repo（新 backend/delivery_repo.py，host 層）
resolve_project_repo(thread_id) -> (target, repo_full_name)：existing 直接回；new 且 repo_created=0 → keystore creds 呼 integ.create_repo → 回填 repo_full_name+created=1 → 回；new 已建 → 回；無設定 → raise。

### 接線
delivery preview/publish 用 resolve_project_repo 取代 config["repo"]；implement_start 的 target_repo 空時 resolve；GitHub 用既有 github_pr，GitLab 新增 gitlab_mr.py（git push + POST /merge_requests），implement 依 delivery_target 選 opener。

### API/DAL
ProjectResponse/CreateProjectRequest/UpdateProjectRequest 加 delivery 欄位（皆 optional）；dal 加 update_project_delivery + set_project_repo_created；PATCH /api/projects/{id} 擴成可改 delivery。

### 前端
建專案：NewProjectModal（取代 PromptDialog）= name + target(select) + repo mode + 開新(name+visibility)/既有(owner/repo)，可跳過。設定：ThreadRow 加 ⚙ → ProjectDeliveryModal。選 target 但未設 token → 提示先到 ⚙ INTEGRATIONS。lib/api.ts 補 project CRUD helper。

## 階段（每階段獨立驗證）
- P1 後端資料層：schema 加欄 + migrate ALTER fallback + dal + Project model 欄位 + endpoint。測 migration 套用既有 DB、CRUD round-trip。
- P2 建 repo 能力（GitHub）：IntegrationSpec.create_repo + _create_github_repo + github token-only。測 mock urllib（/user/repos vs /orgs、public/private）。
- P3 resolve + 接線（GitHub）：resolve_project_repo（lazy+冪等）+ delivery/implement 改用專案 repo。測四路徑 + e2e（mock）。
- P4 GitLab 到底：_create_gitlab_repo + _publish_gitlab + gitlab_mr.py + gitlab token-only + opener 分派。測 mock。
- P5 前端：NewProjectModal + ProjectDeliveryModal + api helper + 未設 token 提示。tsc + preview。

## 驗證
每階段 pytest（tmp_db + 假 adapter / monkeypatch urllib，不碰真實網路）。回歸：test_delivery_publish + test_implement_* 不破；既有 github credential 殘留 repo key 被忽略。全域 pytest + tsc；真實 e2e 待使用者 repo+token。

## 鐵則
加法優先、向後相容（新欄帶預設、新 capability optional、config 仍可覆寫專案 repo）；migration ALTER fallback try/except OperationalError 冪等；lazy 建 repo 用 repo_created 旗標防重複、失敗不寫 created；外部 API payload 以實機為準。
