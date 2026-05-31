"use client";

// SkillEditorModal —— 新建 / 編輯 user-defined skill（仿 AgentEditorModal）。
// 同一 modal 共用 create / edit；caller 傳 initial 為 null = 新建。

import { useEffect, useRef, useState } from "react";

import { type Skill } from "@/lib/api";

export type SkillDraft = {
  skill_id: string;
  name: string;
  description: string;
  body: string;
  version: string;
};

export function SkillEditorModal({
  open, initial, onSubmit, onCancel,
}: {
  open: boolean;
  initial: Skill | null;          // null = 新建
  onSubmit: (draft: SkillDraft) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<SkillDraft>(() => emptyDraft());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const idInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaving(false);
    if (initial) {
      setDraft({
        skill_id: initial.skill_id, name: initial.name,
        description: initial.description, body: initial.body, version: initial.version,
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
    if (!draft.skill_id.trim()) return setError("skill_id 不可為空");
    if (!/^[a-z0-9_]+$/i.test(draft.skill_id.trim())) return setError("skill_id 只允許英數 / 底線");
    if (!draft.name.trim()) return setError("name 不可為空");
    setSaving(true);
    try {
      await onSubmit({
        ...draft,
        skill_id: draft.skill_id.trim(),
        name: draft.name.trim(),
        version: draft.version.trim() || "1.0",
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
              {isEdit ? `編輯 skill · ${initial?.skill_id}` : "新建 skill"}
            </h2>
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              {isEdit
                ? `PUT /api/skills/${initial?.skill_id}`
                : "POST /api/skills · 同 id 覆寫 builtin seed"}
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

          <Field label="skill_id" required>
            <input
              ref={idInputRef}
              type="text"
              value={draft.skill_id}
              onChange={(e) => setDraft({ ...draft, skill_id: e.target.value })}
              disabled={idLocked}
              placeholder="e.g. nfr_extraction"
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
              placeholder="e.g. NFR 抽取"
              className={fieldClass}
            />
          </Field>

          <Field label="描述（description）">
            <input
              type="text"
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              placeholder="一句話說明這個 skill 的用途"
              className={fieldClass}
            />
          </Field>

          <Field label="Body（會注入 AI prompt 的技能內容）">
            <textarea
              rows={12}
              value={draft.body}
              onChange={(e) => setDraft({ ...draft, body: e.target.value })}
              placeholder="e.g. When gathering requirements, always probe for non-functional requirements (security, performance, scalability)…"
              className={fieldClass + " resize-y"}
            />
          </Field>

          <Field label="version">
            <input
              type="text"
              value={draft.version}
              onChange={(e) => setDraft({ ...draft, version: e.target.value })}
              placeholder="1.0"
              className={fieldClass}
            />
          </Field>
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
            {saving ? "saving…" : (isEdit ? "儲存變更" : "建立 skill")}
          </button>
        </div>
      </div>
    </div>
  );
}

function emptyDraft(): SkillDraft {
  return { skill_id: "", name: "", description: "", body: "", version: "1.0" };
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
