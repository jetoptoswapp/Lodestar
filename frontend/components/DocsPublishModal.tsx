"use client";

// DocsPublishModal —— 把 PRD + Architecture + UI 設計（若已生成）發到 Wiki（不進 code）。
//   github → /wiki；gitlab → /-/wikis。兩者都 push {repo}.wiki.git，介面直接 render
//   markdown，push 完即時可看（免 build / pipeline）。
// 流程：開啟時讀專案 delivery_target 決定文案 → 按「發佈」POST /api/docs/{thread}/publish →
//   顯示結果（url + note）或錯誤。手動觸發、可重發（覆寫）。

import { useEffect, useState } from "react";

type DocsResult = { ok: boolean; target: string; repo: string; url: string; note: string };
type Step = "confirm" | "publishing" | "result";

export function DocsPublishModal({
  open, thread, apiBase, onClose,
}: {
  open: boolean;
  thread: string | null;
  apiBase: string;
  onClose: () => void;
}) {
  const [target, setTarget] = useState<string>("");
  const [step, setStep] = useState<Step>("confirm");
  const [result, setResult] = useState<DocsResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setStep("confirm"); setResult(null); setError(null); setTarget("");
    if (thread) {
      fetch(`${apiBase}/api/projects/${thread}`)
        .then((r) => r.json())
        .then((p) => setTarget(p.delivery_target ?? ""))
        .catch(() => setTarget(""));
    }
  }, [open, thread, apiBase]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey, true);
    return () => { document.body.style.overflow = prev; window.removeEventListener("keydown", onKey, true); };
  }, [open, onClose]);

  const dest = target === "gitlab" ? "GitLab Wiki" : target === "github" ? "GitHub Wiki" : "Wiki";

  const publish = async () => {
    if (!thread) return;
    setStep("publishing"); setError(null);
    try {
      const r = await fetch(`${apiBase}/api/docs/${thread}/publish`, { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.detail?.message ?? r.statusText);
      setResult(data as DocsResult);
      setStep("result");
    } catch (e) {
      setError((e as Error).message);
      setStep("confirm");
    }
  };

  if (!open) return null;

  return (
    <div className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} role="dialog" aria-modal="true">
      <div className="shadow-anvil paper-texture relative w-full max-w-lg border border-[var(--paper-edge)] bg-[var(--paper)]">
        <div className="flex items-start justify-between border-b border-[var(--rule)] px-6 py-4">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
              發佈文件到 {dest}
            </h2>
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              POST /api/docs/{thread ?? ""}/publish
            </p>
          </div>
          <button onClick={onClose} aria-label="close" className="grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#cdd4df]">×</button>
        </div>

        <div className="max-h-[60vh] space-y-4 overflow-y-auto px-6 py-5">
          {error && (
            <div className="border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f47171]">
              {error}
            </div>
          )}

          {step === "confirm" && (
            <>
              <p className="font-[family-name:var(--font-sans)] text-[13px] leading-[1.7] text-[#cdd4df]">
                把 <span className="text-[var(--polaris)]">PRD</span>、
                <span className="text-[var(--polaris)]">Architecture</span> 與
                <span className="text-[var(--polaris)]"> UI 設計</span>（若已生成）發佈到 {dest}
                （不進 code）。可重複發佈，會覆寫先前內容。
              </p>
              {target && (
                <div className="border-l-2 border-[var(--polaris)]/50 bg-[color-mix(in_oklab,var(--polaris)_8%,transparent)] px-3 py-2 font-[family-name:var(--font-mono)] text-[10.5px] leading-[1.7] text-[var(--ink-muted)]">
                  ⓘ 發到該 repo 的 Wiki（獨立於 code），介面直接 render markdown，push 完即時可看。
                  需該 repo 已啟用 Wiki。
                </div>
              )}
              {!target && (
                <div className="border-l-2 border-[#f59e0b]/60 bg-[#f59e0b]/8 px-3 py-2 font-[family-name:var(--font-mono)] text-[10.5px] leading-[1.7] text-[#f59e0b]">
                  ⚠ 此專案尚未設定 delivery target（github / gitlab），請先到專案設定指定 repo。
                </div>
              )}
            </>
          )}

          {step === "publishing" && (
            <div className="flex flex-col items-center gap-4 py-10">
              <div className="grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">↗</div>
              <div className="text-center font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
                發佈到 {dest}…
              </div>
            </div>
          )}

          {step === "result" && result && (
            <div className="space-y-4">
              <div className={`border-l-4 px-4 py-3 ${result.ok ? "border-[var(--approved)] bg-[color-mix(in_oklab,var(--approved)_8%,transparent)]" : "border-[#f47171] bg-[color-mix(in_oklab,#f47171_10%,transparent)]"}`}>
                <div className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
                  {result.ok ? "✓ 已發佈" : "✗ 發佈失敗"}
                </div>
                <p className="mt-1 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                  {result.target} · {result.repo}
                </p>
              </div>
              {result.url && (
                <div className="flex items-center gap-2 border border-[var(--rule-dark)] bg-[var(--bg-elev)]/30 px-3 py-2">
                  <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">link →</span>
                  <a href={result.url} target="_blank" rel="noreferrer noopener" className="truncate font-[family-name:var(--font-mono)] text-[11.5px] text-[var(--polaris)] hover:underline">
                    {result.url}
                  </a>
                </div>
              )}
              {result.note && (
                <p className="font-[family-name:var(--font-sans)] text-[12.5px] leading-[1.7] text-[var(--ink-muted)]">
                  {result.note}
                </p>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
          <button onClick={onClose} className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]">
            {step === "result" ? "完成" : "取消"}
          </button>
          {step === "confirm" && (
            <button onClick={publish} disabled={!thread || !target}
              className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-50">
              發佈
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
