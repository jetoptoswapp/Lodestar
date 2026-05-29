"use client";

// AgentEditorModal —— 新建 / 編輯 user-defined agent。
//
// 同一 modal 共用 create / edit；caller 傳 initial 為 null = 新建。

import { useEffect, useRef, useState } from "react";

import type { Agent } from "@/lib/api";

const ROLE_OPTIONS = ["prd", "architecture", "stories", "implement", "custom"] as const;
const MODEL_OPTIONS = ["claude-cli"] as const;   // M2.5：M3 後可從 /api/models 拉

export type AgentDraft = {
  agent_id: string;
  name: string;
  role: string;
  system_prompt: string;
  model_choice: string;
  max_iterations: number;
  enabled: boolean;
  tools: string[];
};

export function AgentEditorModal({
  open, initial, onSubmit, onCancel,
}: {
  open: boolean;
  initial: Agent | null;          // null = 新建
  onSubmit: (draft: AgentDraft) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<AgentDraft>(() => emptyDraft());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const idInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaving(false);
    if (initial) {
      setDraft({
        agent_id: initial.agent_id,
        name: initial.name,
        role: initial.role,
        system_prompt: initial.system_prompt,
        model_choice: initial.model_choice,
        max_iterations: initial.max_iterations,
        enabled: initial.enabled,
        tools: [...initial.tools],
      });
    } else {
      setDraft(emptyDraft());
      setTimeout(() => idInputRef.current?.focus(), 50);
    }
  }, [open, initial]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open, onCancel]);

  if (!open) return null;

  const isEdit = !!initial;
  const idLocked = isEdit;          // 編輯不能改 id

  const save = async () => {
    setError(null);
    if (!draft.agent_id.trim()) return setError("agent_id 不可為空");
    if (!/^[a-z0-9_]+$/i.test(draft.agent_id.trim())) return setError("agent_id 只允許英數 / 底線");
    if (!draft.name.trim()) return setError("name 不可為空");
    if (!draft.role.trim()) return setError("role 不可為空");
    if (draft.max_iterations < 1) return setError("max_iterations 必須 ≥ 1");
    setSaving(true);
    try {
      await onSubmit({
        ...draft,
        agent_id: draft.agent_id.trim(),
        name: draft.name.trim(),
        role: draft.role.trim(),
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
      role="dialog"
      aria-modal="true"
    >
      <div className="shadow-anvil paper-texture relative w-full max-w-xl border border-[var(--paper-edge)] bg-[var(--paper)]">
        <div className="flex items-start justify-between border-b border-[var(--rule)] px-6 py-4">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]">
              {isEdit ? `編輯 agent · ${initial?.agent_id}` : "新建 agent"}
            </h2>
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              {isEdit
                ? `PUT /api/agents/${initial?.agent_id}`
                : "POST /api/agents · 同 id 覆寫 builtin seed"}
            </p>
          </div>
          <button onClick={onCancel} aria-label="close" className="grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#cdd4df]">
            ×
          </button>
        </div>

        <div className="max-h-[68vh] overflow-y-auto px-6 py-5 space-y-4">
          {error && (
            <div className="border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f47171]">
              {error}
            </div>
          )}

          <Field label="agent_id" required>
            <input
              ref={idInputRef}
              type="text"
              value={draft.agent_id}
              onChange={(e) => setDraft({ ...draft, agent_id: e.target.value })}
              disabled={idLocked}
              placeholder="e.g. ecommerce_sa"
              className={fieldClass + (idLocked ? " opacity-60" : "")}
            />
            <p className="mt-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
              {idLocked ? "編輯時不能改 id" : "英數 / 底線；建立後不能改"}
            </p>
          </Field>

          <Field label="顯示名稱（name）" required>
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="e.g. E-commerce SA"
              className={fieldClass}
            />
          </Field>

          <Field label="role（綁的 stage_id）" required>
            <div className="flex flex-wrap gap-1.5">
              {ROLE_OPTIONS.map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setDraft({ ...draft, role: r === "custom" ? draft.role : r })}
                  className={`border px-2.5 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] transition ${
                    draft.role === r
                      ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_18%,transparent)] text-[var(--polaris)]"
                      : "border-[var(--rule-dark)] text-[#cdd4df] hover:border-[#4a5468]"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={draft.role}
              onChange={(e) => setDraft({ ...draft, role: e.target.value })}
              placeholder="或直接輸入自訂 stage_id"
              className={fieldClass + " mt-2"}
            />
          </Field>

          <Field label="System prompt（agent 的指令）">
            <textarea
              rows={6}
              value={draft.system_prompt}
              onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
              placeholder="e.g. You are a strict SA focused on e-commerce…"
              className={fieldClass + " resize-y"}
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="model">
              <select
                value={draft.model_choice}
                onChange={(e) => setDraft({ ...draft, model_choice: e.target.value })}
                className={fieldClass}
              >
                {MODEL_OPTIONS.map((m) => (<option key={m} value={m}>{m}</option>))}
              </select>
            </Field>
            <Field label="max iterations">
              <input
                type="number"
                min={1}
                max={10}
                value={draft.max_iterations}
                onChange={(e) => setDraft({ ...draft, max_iterations: parseInt(e.target.value || "1", 10) })}
                className={fieldClass}
              />
            </Field>
          </div>

          <label className="flex items-center gap-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              className="h-4 w-4 accent-[var(--polaris)]"
            />
            <span className="font-[family-name:var(--font-sans)] text-[13px] text-[#cdd4df]">
              啟用（enabled）
            </span>
          </label>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
          <button
            onClick={onCancel}
            className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]"
          >
            取消
          </button>
          <button
            onClick={save}
            disabled={saving}
            className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-60"
          >
            {saving ? "saving…" : (isEdit ? "儲存變更" : "建立 agent")}
          </button>
        </div>
      </div>
    </div>
  );
}

function emptyDraft(): AgentDraft {
  return {
    agent_id: "",
    name: "",
    role: "prd",
    system_prompt: "",
    model_choice: "claude-cli",
    max_iterations: 1,
    enabled: true,
    tools: [],
  };
}

const fieldClass =
  "w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-mono)] text-[12.5px] text-[#e6ecf5] outline-none placeholder:text-[var(--ink-muted)] focus:border-[var(--polaris)]";

function Field({ label, required, children }: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        {label}
        {required && <span className="ml-1 text-[#f47171]">*</span>}
      </label>
      {children}
    </div>
  );
}
