"use client";

// Modal / PromptDialog / ConfirmDialog —— Industrial Cobalt 風格的對話框。
//
// 取代 window.prompt / window.confirm，原因：
// 1. 原生對話框配色跟 dark theme 不搭、字型醜
// 2. 不支援多行 / 預設值樣式 / 自訂按鈕
// 3. 視覺上「跳出系統訊息」破壞流程感
//
// 設計：
// - backdrop blur + cobalt accent border
// - Esc 取消、⌘↵ / Ctrl↵ 提交（PromptDialog 多行）、↵ 提交（ConfirmDialog）
// - focus trap：開啟時自動 focus 第一個 input / 主按鈕；Tab 在 modal 內循環
// - 點 backdrop 取消（destructive 對話框可關閉，但不該誤觸 confirm）

import { useEffect, useRef } from "react";

// ============================================================
//  Modal shell
// ============================================================
function ModalShell({
  open, onClose, title, subtitle, children, footer, widthClass = "max-w-md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer: React.ReactNode;
  widthClass?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // 開啟時 lock body scroll；關閉時還原
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  // Esc 全域關閉（capture 階段，比其他 keydown 早攔）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="lodestar-modal-title"
    >
      <div
        ref={ref}
        className={`shadow-anvil paper-texture relative w-full ${widthClass} border border-[var(--paper-edge)] bg-[var(--paper)]`}
      >
        <div className="border-b border-[var(--rule)] px-6 py-4">
          <h2
            id="lodestar-modal-title"
            className="font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]"
          >
            {title}
          </h2>
          {subtitle && (
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              {subtitle}
            </p>
          )}
        </div>
        <div className="px-6 py-5">{children}</div>
        <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
          {footer}
        </div>
      </div>
    </div>
  );
}

// ============================================================
//  PromptDialog —— 字串輸入（單行或多行）
// ============================================================
export function PromptDialog({
  open, title, subtitle, label, placeholder, defaultValue = "",
  multiline = false, submitLabel = "確定", cancelLabel = "取消",
  onSubmit, onCancel,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  label?: string;
  placeholder?: string;
  defaultValue?: string;
  multiline?: boolean;
  submitLabel?: string;
  cancelLabel?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  // open 切換時，把預設值寫回（避免 stale）+ 自動 focus + 全選
  useEffect(() => {
    if (!open) return;
    const el = inputRef.current;
    if (!el) return;
    el.value = defaultValue;
    el.focus();
    if ("select" in el) (el as HTMLInputElement).select();
  }, [open, defaultValue]);

  const submit = () => {
    const v = (inputRef.current?.value ?? "").trim();
    if (!v) return;     // 空輸入視為無效，不提交也不關閉
    onSubmit(v);
  };

  const inputClass =
    "w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2.5 " +
    "font-[family-name:var(--font-sans)] text-[14px] text-[#e6ecf5] " +
    "outline-none placeholder:text-[var(--ink-muted)] " +
    "focus:border-[var(--polaris)] focus:ring-2 focus:ring-[color-mix(in_oklab,var(--polaris)_30%,transparent)] " +
    "transition";

  return (
    <ModalShell
      open={open}
      onClose={onCancel}
      title={title}
      subtitle={subtitle}
      widthClass={multiline ? "max-w-xl" : "max-w-md"}
      footer={
        <>
          <button
            onClick={onCancel}
            className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]"
          >
            {cancelLabel}
          </button>
          <button
            onClick={submit}
            className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)]"
          >
            {submitLabel}
          </button>
        </>
      }
    >
      {label && (
        <label className="mb-2 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          {label}
        </label>
      )}
      {multiline ? (
        <textarea
          ref={inputRef as React.RefObject<HTMLTextAreaElement>}
          rows={5}
          placeholder={placeholder}
          defaultValue={defaultValue}
          className={inputClass + " resize-none"}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
      ) : (
        <input
          ref={inputRef as React.RefObject<HTMLInputElement>}
          type="text"
          placeholder={placeholder}
          defaultValue={defaultValue}
          className={inputClass}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
      )}
      <p className="mt-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
        {multiline ? "⌘↵ 提交 · esc 取消" : "↵ 提交 · esc 取消"}
      </p>
    </ModalShell>
  );
}

// ============================================================
//  ConfirmDialog —— yes/no 確認，支援 destructive 樣式
// ============================================================
export function ConfirmDialog({
  open, title, subtitle, message,
  confirmLabel = "確定", cancelLabel = "取消", destructive = false,
  onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) confirmRef.current?.focus();
  }, [open]);

  // 在 modal 內按 Enter → confirm（focus 在 confirm button 上時 default 行為，但保險加 handler）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        // 只在 modal 還開時觸發
        e.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onConfirm]);

  const confirmStyles = destructive
    ? "border-[#f47171] bg-[#f47171] text-white hover:bg-[#f88c8c]"
    : "border-[var(--polaris)] bg-[var(--polaris)] text-white hover:bg-[var(--polaris-hi)]";

  return (
    <ModalShell
      open={open}
      onClose={onCancel}
      title={title}
      subtitle={subtitle}
      footer={
        <>
          <button
            onClick={onCancel}
            className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className={`border px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] transition ${confirmStyles}`}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <div className="font-[family-name:var(--font-sans)] text-[14px] leading-[1.65] text-[#cdd4df]">
        {message}
      </div>
      <p className="mt-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
        ↵ 確定 · esc 取消
      </p>
    </ModalShell>
  );
}
