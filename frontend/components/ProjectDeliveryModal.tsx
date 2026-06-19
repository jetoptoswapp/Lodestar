"use client";

// ProjectDeliveryModal —— 建新專案（含 delivery repo 設定）或事後編輯既有專案的 delivery。
//   thread=null → 建新（POST /api/projects）；thread=tid → 編輯（PATCH /api/projects/{tid}）。
// delivery 可留「先不設」（之後再設）。選了 target 但該 integration 未設 token → 提示前往 INTEGRATIONS。
// 開新 repo 採 lazy：真正在 publish / implement 時才建（見後端 resolve_project_repo）。

import { useEffect, useState } from "react";

type Mode = "new" | "existing";

// workflow 選項（建新時可選）；只需 id/label。modify_existing 需要既有 repo。
type WorkflowOption = { id: string; label: string };
const MODIFY_EXISTING_ID = "modify_existing";

export function ProjectDeliveryModal({
  open, thread, apiBase, onClose, onSaved, onOpenIntegrations, workflows = [],
}: {
  open: boolean;
  thread: string | null;              // null = 建新
  apiBase: string;
  onClose: () => void;
  onSaved: (threadId: string) => void;
  onOpenIntegrations?: () => void;
  workflows?: WorkflowOption[];        // 建新時的 workflow 選單（空 → 不顯示，沿用後端 default）
}) {
  const isNew = thread === null;
  const [name, setName] = useState("");
  const [workflowId, setWorkflowId] = useState("default");
  const [target, setTarget] = useState("");          // "" / github / gitlab
  const [repoMode, setRepoMode] = useState<Mode>("new");
  const [repoFullName, setRepoFullName] = useState("");
  const [repoOwner, setRepoOwner] = useState("");
  const [repoVisibility, setRepoVisibility] = useState("private");
  const [localPath, setLocalPath] = useState("");    // repo_mode=local：本機資料夾絕對路徑
  const [buildCommand, setBuildCommand] = useState("");      // build_verify stage 編譯指令
  const [buildEnvScript, setBuildEnvScript] = useState("");  // build 前 source 的 env script
  const [tokenSet, setTokenSet] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isLocal = target === "local";

  const modifyExisting = workflowId === MODIFY_EXISTING_ID;

  useEffect(() => {
    if (!open) return;
    setError(null);
    setBusy(false);
    if (isNew) {
      setName("新需求"); setWorkflowId("default"); setTarget(""); setRepoMode("new");
      setRepoFullName(""); setRepoOwner(""); setRepoVisibility("private"); setLocalPath("");
      setBuildCommand(""); setBuildEnvScript("");
    } else {
      fetch(`${apiBase}/api/projects/${thread}`)
        .then((r) => r.json())
        .then((p) => {
          setName(p.name ?? ""); setTarget(p.delivery_target ?? "");
          setRepoMode(((p.repo_mode || "new") as Mode));
          setRepoFullName(p.repo_full_name ?? ""); setRepoOwner(p.repo_owner ?? "");
          setRepoVisibility(p.repo_visibility || "private");
          setLocalPath(p.local_path ?? "");
          setBuildCommand(p.build_command ?? ""); setBuildEnvScript(p.build_env_script ?? "");
        })
        .catch((e) => setError(`讀取專案失敗：${e.message}`));
    }
  }, [open, thread, apiBase, isNew]);

  // modify_existing 需要既有 repo：選它時自動把 repo 模式切到 existing、target 預設 github
  //（在 select onChange 處理，避免 set-state-in-effect）。
  const onPickWorkflow = (id: string) => {
    setWorkflowId(id);
    if (id === MODIFY_EXISTING_ID) {
      setRepoMode("existing");
      setTarget((t) => t || "github");
    }
  };

  useEffect(() => {
    if (!open || !target || target === "local") { setTokenSet(null); return; }  // local 無需 token
    let alive = true;
    fetch(`${apiBase}/api/integrations/${target}/credentials`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setTokenSet(d ? !!d.has_credentials : false); })
      .catch(() => { if (alive) setTokenSet(null); });
    return () => { alive = false; };
  }, [open, target, apiBase]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey, true);
    return () => { document.body.style.overflow = prev; window.removeEventListener("keydown", onKey, true); };
  }, [open, onClose]);

  const submit = async () => {
    if (!name.trim()) { setError("專案名稱不可為空"); return; }
    if (isNew && modifyExisting && !(target && (isLocal ? localPath.trim() : repoMode === "existing" && repoFullName.trim()))) {
      setError("「修改既有專案」需指定既有 repo（owner/repo）或本機資料夾路徑");
      return;
    }
    if (isLocal && !localPath.trim()) { setError("本機資料夾路徑不可為空"); return; }
    if (isLocal && !localPath.trim().startsWith("/")) { setError("本機資料夾請填絕對路徑（以 / 開頭）"); return; }
    setBusy(true); setError(null);
    const buildCfg = { build_command: buildCommand.trim(), build_env_script: buildEnvScript.trim() };
    const delivery = !target
      ? { delivery_target: "" }
      : isLocal
      ? { delivery_target: "local", repo_mode: "local", local_path: localPath.trim(), ...buildCfg }
      : { delivery_target: target, repo_mode: repoMode, repo_full_name: repoFullName.trim(),
          repo_owner: repoOwner.trim(), repo_visibility: repoVisibility, ...buildCfg };
    try {
      let tid = thread;
      if (isNew) {
        const r = await fetch(`${apiBase}/api/projects`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim(), workflow_id: workflowId, ...delivery }),
        });
        if (!r.ok) throw new Error((await r.json())?.detail?.message ?? r.statusText);
        tid = (await r.json()).thread_id;
      } else {
        const r = await fetch(`${apiBase}/api/projects/${thread}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim(), ...delivery }),
        });
        if (!r.ok) throw new Error((await r.json())?.detail?.message ?? r.statusText);
      }
      onSaved(tid as string);
      onClose();
    } catch (e) {
      setError(`儲存失敗：${(e as Error).message}`);
      setBusy(false);
    }
  };

  if (!open) return null;
  const fc = "w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-mono)] text-[12.5px] text-[#e6ecf5] outline-none placeholder:text-[var(--ink-muted)] focus:border-[var(--polaris)]";
  const lbl = "mb-1 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]";

  return (
    <div className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} role="dialog" aria-modal="true">
      <div className="shadow-anvil paper-texture relative w-full max-w-lg border border-[var(--paper-edge)] bg-[var(--paper)]">
        <div className="flex items-start justify-between border-b border-[var(--rule)] px-6 py-4">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
              {isNew ? "新專案" : "專案設定"}
            </h2>
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              {isNew ? "POST /api/projects" : `PATCH /api/projects/${thread}`}
            </p>
          </div>
          <button onClick={onClose} aria-label="close" className="grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#cdd4df]">×</button>
        </div>

        <div className="max-h-[62vh] space-y-4 overflow-y-auto px-6 py-5">
          {error && <div className="border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f47171]">{error}</div>}

          <div>
            <label className={lbl}>專案名稱</label>
            <input className={fc} value={name} onChange={(e) => setName(e.target.value)} autoFocus spellCheck={false} />
          </div>

          {isNew && workflows.length > 0 && (
            <div>
              <label className={lbl}>Workflow（流程）</label>
              <select className={fc} value={workflowId} onChange={(e) => onPickWorkflow(e.target.value)}>
                {workflows.map((w) => (
                  <option key={w.id} value={w.id}>{w.label}（{w.id}）</option>
                ))}
              </select>
              {modifyExisting && (
                <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase leading-[1.6] tracking-[0.16em] text-[var(--ink-muted)]">
                  ⓘ 修改既有專案：clone 既有 repo → AI 讀碼 → 談變更/解 bug → implement 開 PR。下方請指定既有 repo。
                </p>
              )}
            </div>
          )}

          <div>
            <label className={lbl}>Delivery target（交付到哪）</label>
            <select className={fc} value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">（先不設，之後再設）</option>
              <option value="github">github</option>
              <option value="gitlab">gitlab</option>
              <option value="local">local（本機資料夾）</option>
            </select>
          </div>

          {target && !isLocal && (
            <>
              {tokenSet === false && (
                <div className="flex items-center justify-between gap-2 border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f59e0b]">
                  <span>⚠ {target} 尚未設定 token</span>
                  {onOpenIntegrations && (
                    <button onClick={onOpenIntegrations} className="whitespace-nowrap underline">前往 ⚙ INTEGRATIONS</button>
                  )}
                </div>
              )}
              <div>
                <label className={lbl}>Repo 模式</label>
                <div className="flex gap-5 font-[family-name:var(--font-sans)] text-[13px] text-[#cdd4df]">
                  <label className="flex items-center gap-1.5"><input type="radio" checked={repoMode === "new"} onChange={() => setRepoMode("new")} />開新 repo</label>
                  <label className="flex items-center gap-1.5"><input type="radio" checked={repoMode === "existing"} onChange={() => setRepoMode("existing")} />指向既有</label>
                </div>
              </div>
              {repoMode === "new" ? (
                <>
                  <div><label className={lbl}>新 repo 名稱（留空＝用專案名）</label><input className={fc} value={repoFullName} onChange={(e) => setRepoFullName(e.target.value)} placeholder="my-project" spellCheck={false} /></div>
                  <div><label className={lbl}>Owner / org（留空＝個人帳號）</label><input className={fc} value={repoOwner} onChange={(e) => setRepoOwner(e.target.value)} placeholder="（個人帳號）" spellCheck={false} /></div>
                  <div><label className={lbl}>可見性</label>
                    <select className={fc} value={repoVisibility} onChange={(e) => setRepoVisibility(e.target.value)}>
                      <option value="private">private</option>
                      <option value="public">public</option>
                      {target === "gitlab" && <option value="internal">internal</option>}
                    </select>
                  </div>
                </>
              ) : (
                <div><label className={lbl}>既有 repo（owner/repo 或 group/project）</label><input className={fc} value={repoFullName} onChange={(e) => setRepoFullName(e.target.value)} placeholder="owner/repo" spellCheck={false} /></div>
              )}
              <p className="font-[family-name:var(--font-mono)] text-[10px] uppercase leading-[1.6] tracking-[0.16em] text-[var(--ink-muted)]">
                ⓘ 開新 repo 採 lazy：在你要交付（發 issue／自動實作開 PR）時才真正建立。token 於 ⚙ INTEGRATIONS 設定。
              </p>
            </>
          )}

          {isLocal && (
            <div>
              <label className={lbl}>本機資料夾路徑（絕對路徑）</label>
              <input className={fc} value={localPath} onChange={(e) => setLocalPath(e.target.value)}
                placeholder="/Users/you/project" spellCheck={false} />
              <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase leading-[1.6] tracking-[0.16em] text-[var(--ink-muted)]">
                ⓘ 指向本機既有資料夾。談變更時 AI 唯讀讀取此資料夾（看得到未 commit 的 WIP）；實作時複製一份快照動工、產出 branch + diff，不開 PR、原始資料夾不受影響。
              </p>
            </div>
          )}

          {target && (
            <div className="space-y-2 border-t border-[var(--rule-dark)] pt-3">
              <label className={lbl}>Build 驗證（build_verify stage 用 · 選填）</label>
              <input className={fc} value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)}
                placeholder="cmake --build . --target flash_nn" spellCheck={false} />
              <input className={fc} value={buildEnvScript} onChange={(e) => setBuildEnvScript(e.target.value)}
                placeholder="（選填）build 前 source 的 env script，如 /path/to/sdk/env.sh" spellCheck={false} />
              <p className="font-[family-name:var(--font-mono)] text-[10px] uppercase leading-[1.6] tracking-[0.16em] text-[var(--ink-muted)]">
                ⓘ build_verify stage 會在 implement 的快照上跑「build 指令」驗證編譯；env script 讓 toolchain（如 arm-none-eabi-gcc）上 PATH。可留空，之後再補。
              </p>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
          <button onClick={onClose} className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]">取消</button>
          <button onClick={submit} disabled={busy} className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-50">
            {busy ? "儲存中…" : (isNew ? "建立" : "儲存")}
          </button>
        </div>
      </div>
    </div>
  );
}
