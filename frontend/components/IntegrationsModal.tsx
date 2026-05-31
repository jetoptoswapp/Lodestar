"use client";

// IntegrationsModal —— 獨立的 integration 憑證設定（不綁 stories publish 流程）。
//
// 選 integration（github / jira / gitlab）→ 依 config_schema render 欄位 → 儲存到 server-side keystore。
// 機密（token）走 keystore（Fernet 加密），明文不留瀏覽器；已存只顯示「✓ 已儲存」、留空沿用。
// 與 PublishModal 共用同一把 keystore 憑證：target=github 的 token 同時供「發佈 issue」與「自動實作開 PR」。

import { useEffect, useState } from "react";

type CredentialsStatus = {
  target: string;
  has_credentials: boolean;
  secret_fields_set: string[];          // 已設定的機密欄 key（不含明文）
  values: Record<string, string>;       // 非機密欄（如 repo）回填值
};

type IntegrationField = {
  key: string;
  label: string;
  type: "text" | "password";
  required?: boolean;
};

type IntegrationInfo = {
  target: string;
  description: string;
  config_schema: { fields?: IntegrationField[] };
};

type SaveStatus = "idle" | "saving" | "saved" | "error";

export function IntegrationsModal({
  open, apiBase, onClose,
}: {
  open: boolean;
  apiBase: string;
  onClose: () => void;
}) {
  const [integrations, setIntegrations] = useState<IntegrationInfo[]>([]);
  const [selectedTarget, setSelectedTarget] = useState("github");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [cred, setCred] = useState<CredentialsStatus | null>(null);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  // 開啟：列出已註冊 integration
  useEffect(() => {
    if (!open) return;
    setError(null);
    setStatus("idle");
    fetch(`${apiBase}/api/integrations`)
      .then((r) => r.json())
      .then((d: { integrations: IntegrationInfo[] }) => {
        setIntegrations(d.integrations);
        if (d.integrations.length > 0 && !d.integrations.find((i) => i.target === selectedTarget)) {
          setSelectedTarget(d.integrations[0].target);
        }
      })
      .catch((e) => setError(`讀取 integrations 失敗：${e.message}`));
  }, [open, apiBase]); // eslint-disable-line react-hooks/exhaustive-deps

  // 切 target / 開啟：載已存憑證狀態（非機密欄回填、機密欄只知是否已設定）
  useEffect(() => {
    if (!open || !selectedTarget) return;
    let alive = true;
    setStatus("idle");
    fetch(`${apiBase}/api/integrations/${selectedTarget}/credentials`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: CredentialsStatus | null) => { if (alive) { setCred(d); setConfig(d?.values ?? {}); } })
      .catch(() => { if (alive) { setCred(null); setConfig({}); } });
    return () => { alive = false; };
  }, [open, selectedTarget, apiBase]);

  // Esc 關閉 + lock body scroll
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open, onClose]);

  const current = integrations.find((i) => i.target === selectedTarget);
  const fields = current?.config_schema?.fields ?? [];
  const savedSecret = (key: string) => cred?.secret_fields_set?.includes(key) ?? false;

  // 把含機密的 config 加密存到 keystore；空字串欄位後端不覆寫既有值。
  const save = async () => {
    setStatus("saving");
    setError(null);
    try {
      const r = await fetch(`${apiBase}/api/integrations/${selectedTarget}/credentials`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.detail?.message ?? r.statusText);
      }
      const d: CredentialsStatus = await r.json();
      setCred(d);
      setConfig(d.values ?? {});       // 清掉機密輸入（已存）、非機密回填
      setStatus("saved");
    } catch (e) {
      setError(`儲存失敗：${(e as Error).message}`);
      setStatus("error");
    }
  };

  if (!open) return null;

  return (
    <div
      className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
    >
      <div className="shadow-anvil paper-texture relative w-full max-w-xl border border-[var(--paper-edge)] bg-[var(--paper)]">
        <div className="flex items-start justify-between border-b border-[var(--rule)] px-6 py-4">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]">
              Integrations · 憑證設定
            </h2>
            <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              PUT /api/integrations/{selectedTarget}/credentials
            </p>
          </div>
          <button onClick={onClose} aria-label="close" className="grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#cdd4df]">
            ×
          </button>
        </div>

        <div className="max-h-[60vh] space-y-5 overflow-y-auto px-6 py-5">
          {error && (
            <div className="border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f47171]">
              {error}
            </div>
          )}

          <div>
            <label className="mb-2 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              target tracker
            </label>
            <div className="flex flex-wrap gap-2">
              {integrations.map((i) => {
                const selected = i.target === selectedTarget;
                return (
                  <button
                    key={i.target}
                    onClick={() => setSelectedTarget(i.target)}
                    className={`flex flex-col items-start gap-1 border px-3 py-2 text-left transition ${
                      selected
                        ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_14%,transparent)]"
                        : "border-[var(--rule-dark)] bg-transparent hover:border-[#404a5b] hover:bg-[var(--bg-elev)]"
                    }`}
                  >
                    <code className={`font-[family-name:var(--font-mono)] text-[12px] uppercase tracking-[0.18em] ${selected ? "text-[var(--polaris)]" : "text-[#e6ecf5]"}`}>
                      {i.target}
                    </code>
                    <span className="font-[family-name:var(--font-sans)] text-[11px] leading-[1.4] text-[var(--ink-muted)]">
                      {i.description}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="mb-2 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              設定欄位
            </label>
            <div className="space-y-3">
              {fields.map((f) => (
                <div key={f.key}>
                  <label className="mb-1 flex items-center gap-2 font-[family-name:var(--font-sans)] text-[13px] text-[#cdd4df]">
                    {f.label}
                    {f.required && !savedSecret(f.key) && <span className="text-[#f47171]">*</span>}
                    {savedSecret(f.key) && (
                      <span className="border border-[var(--approved)]/40 px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[var(--approved)]">
                        ✓ 已儲存
                      </span>
                    )}
                  </label>
                  <input
                    type={f.type}
                    value={config[f.key] ?? ""}
                    onChange={(e) => { setConfig({ ...config, [f.key]: e.target.value }); setStatus("idle"); }}
                    className="w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-mono)] text-[12.5px] text-[#e6ecf5] outline-none placeholder:text-[var(--ink-muted)] focus:border-[var(--polaris)]"
                    placeholder={savedSecret(f.key) ? "•••••• 已儲存（留空沿用）" : f.type === "password" ? "••••••" : ""}
                    autoComplete={f.type === "password" ? "new-password" : "off"}
                    spellCheck={false}
                  />
                </div>
              ))}
              {fields.length === 0 && (
                <p className="font-[family-name:var(--font-sans)] text-[12px] text-[var(--ink-muted)]">此 integration 無需設定欄位。</p>
              )}
            </div>
            <p className="mt-2 font-[family-name:var(--font-mono)] text-[10px] uppercase leading-[1.6] tracking-[0.16em] text-[var(--ink-muted)]">
              ⓘ 機密（token）以 server-side keystore（Fernet）加密儲存，明文不留瀏覽器。同一把 github token 供「發佈 issue」與「自動實作開 PR」共用；開 PR 需 token 含 repo（push ＋ pull request）權限。
            </p>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
          {status === "saved" && (
            <span className="mr-auto font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--approved)]">
              ✓ 已儲存到 keystore
            </span>
          )}
          <button onClick={onClose} className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]">
            關閉
          </button>
          <button
            onClick={save}
            disabled={status === "saving"}
            className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-50"
          >
            {status === "saving" ? "儲存中…" : "儲存"}
          </button>
        </div>
      </div>
    </div>
  );
}
