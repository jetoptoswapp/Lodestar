"use client";

// SpecSyncModal —— 把 PRD + 架構 + UI 設計 + CLAUDE.md 規則 commit 進 code repo 根目錄，
//   給自動實作 agent（claude-cli 在 clone 內工作）讀。與「發佈文件到 Wiki」並存：
//   wiki 給人看、code repo 給 agent 讀。
// 流程：開啟時讀專案 delivery_target 決定文案 → 按「同步」POST /api/specs/{thread}/sync →
//   顯示寫入的檔清單 + repo 連結，或錯誤。手動觸發、可重發（CLAUDE.md 用 managed block 併入）。

import { useEffect, useState } from "react";
import { validateStagesMermaid, type DocMermaidResult } from "@/lib/mermaid";

type SyncResult = { ok: boolean; target: string; repo: string; url: string; note: string; files: string[] };
type Step = "confirm" | "syncing" | "result";

// 同步前要驗 mermaid 的文件（含圖的）。
const MERMAID_DOCS = [{ id: "architecture", label: "Architecture" }, { id: "prd", label: "PRD" }];

export function SpecSyncModal({
  open, thread, apiBase, onClose,
}: {
  open: boolean;
  thread: string | null;
  apiBase: string;
  onClose: () => void;
}) {
  const [target, setTarget] = useState<string>("");
  const [step, setStep] = useState<Step>("confirm");
  const [result, setResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mermaidBad, setMermaidBad] = useState<DocMermaidResult[]>([]);
  const [override, setOverride] = useState(false);

  useEffect(() => {
    if (!open) return;
    setStep("confirm"); setResult(null); setError(null); setTarget("");
    setMermaidBad([]); setOverride(false);
    if (thread) {
      fetch(`${apiBase}/api/projects/${thread}`)
        .then((r) => r.json())
        .then((p) => setTarget(p.delivery_target ?? ""))
        .catch(() => setTarget(""));
      // 真 parser 守門：同步前驗 mermaid，有壞圖就擋下（規格進 repo 給實作 agent 讀，更不該帶壞圖）。
      validateStagesMermaid(apiBase, thread, MERMAID_DOCS).then(setMermaidBad).catch(() => setMermaidBad([]));
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

  const dest = target === "gitlab" ? "GitLab" : target === "github" ? "GitHub" : "code repo";

  const sync = async () => {
    if (!thread) return;
    setStep("syncing"); setError(null);
    try {
      const r = await fetch(`${apiBase}/api/specs/${thread}/sync`, { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.detail?.message ?? r.statusText);
      setResult(data as SyncResult);
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
              同步規格到 {dest}
            </h2>
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              POST /api/specs/{thread ?? ""}/sync
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
                <span className="text-[var(--polaris)]">架構</span>、
                <span className="text-[var(--polaris)]">UI 設計</span>（若有）寫進 code repo 的
                <code className="mx-1 text-[var(--polaris)]">.lodestar/</code>，並在根目錄
                <code className="mx-1 text-[var(--polaris)]">CLAUDE.md</code> 寫入實作規矩與專案記憶。
              </p>
              {target && (
                <div className="border-l-2 border-[var(--polaris)]/50 bg-[color-mix(in_oklab,var(--polaris)_8%,transparent)] px-3 py-2 font-[family-name:var(--font-mono)] text-[10.5px] leading-[1.7] text-[var(--ink-muted)]">
                  ⓘ commit 進 code repo 的 default branch（非 Wiki）。之後自動實作 agent clone
                  該 repo 時，claude-cli 會自動讀 CLAUDE.md，依規格與規矩實作。
                  既有 CLAUDE.md 只更新 Lodestar 區塊、不蓋你的內容。
                </div>
              )}
              {!target && (
                <div className="border-l-2 border-[#f59e0b]/60 bg-[#f59e0b]/8 px-3 py-2 font-[family-name:var(--font-mono)] text-[10.5px] leading-[1.7] text-[#f59e0b]">
                  ⚠ 此專案尚未設定 delivery target（github / gitlab），請先到專案設定指定 repo。
                </div>
              )}
              {mermaidBad.length > 0 && (
                <div className="border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2.5 font-[family-name:var(--font-mono)] text-[10.5px] leading-[1.7] text-[#f47171]">
                  <div className="font-semibold uppercase tracking-[0.18em]">⚠ Mermaid 語法錯誤——同步已擋下</div>
                  <ul className="mt-1.5 space-y-1">
                    {mermaidBad.flatMap((d) =>
                      d.issues.map((iss) => (
                        <li key={`${d.stage}-${iss.index}`}>
                          {d.label} · diagram {iss.index}：{iss.message}
                        </li>
                      )),
                    )}
                  </ul>
                  <p className="mt-2 text-[var(--ink-muted)]">
                    請先回該 stage 修好圖再同步（避免壞圖進 repo 給實作 agent 讀）。
                    <button onClick={() => setOverride(true)} className="ml-1 text-[#f59e0b] underline hover:text-[#f59e0b]/80">
                      仍要同步（忽略警告）
                    </button>
                  </p>
                </div>
              )}
            </>
          )}

          {step === "syncing" && (
            <div className="flex flex-col items-center gap-4 py-10">
              <div className="grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">↗</div>
              <div className="text-center font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
                同步到 {dest}…
              </div>
            </div>
          )}

          {step === "result" && result && (
            <div className="space-y-4">
              <div className={`border-l-4 px-4 py-3 ${result.ok ? "border-[var(--approved)] bg-[color-mix(in_oklab,var(--approved)_8%,transparent)]" : "border-[#f47171] bg-[color-mix(in_oklab,#f47171_10%,transparent)]"}`}>
                <div className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
                  {result.ok ? "✓ 已同步" : "✗ 同步失敗"}
                </div>
                <p className="mt-1 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                  {result.target} · {result.repo}
                </p>
              </div>
              {result.files?.length > 0 && (
                <div className="border border-[var(--rule-dark)] bg-[var(--bg-elev)]/30 px-3 py-2">
                  <div className="mb-1.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">files →</div>
                  <ul className="space-y-1">
                    {result.files.map((f) => (
                      <li key={f} className="font-[family-name:var(--font-mono)] text-[11.5px] text-[#cdd4df]">{f}</li>
                    ))}
                  </ul>
                </div>
              )}
              {result.url && (
                <div className="flex items-center gap-2 border border-[var(--rule-dark)] bg-[var(--bg-elev)]/30 px-3 py-2">
                  <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">repo →</span>
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
            <button onClick={sync} disabled={!thread || !target || (mermaidBad.length > 0 && !override)}
              className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-50">
              同步
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
