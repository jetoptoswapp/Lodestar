"use client";

// PublishModal —— stories → DeliveryItem[] → IntegrationSpec.preview / publish。
//
// 流程（multi-step state machine in single dialog）：
//   1. config   ：列已註冊 integration（github / jira / gitlab）+ render config_schema 填欄位
//   2. preview  ：呼 /preview-delivery，列要建立的 issue title / labels（依 group 分區）
//   3. publishing：呼 /publish；spinner（GitHub 大批 issue 可能 ~30s）
//   4. result   ：顯示成功 / 失敗 + 已建立的 URL（外連到 issue）
//
// 憑證儲存：機密（token）走 server-side keystore（Fernet 加密），明文不進瀏覽器、不存 localStorage。
//   開啟時 GET 憑證狀態（非機密欄回填、機密欄只知是否已設定）；預覽前 PUT 儲存。
// 取消任何 step 都會關 modal 並 reset 內部 state。

import { useEffect, useMemo, useState } from "react";

type CredentialsStatus = {
  target: string;
  has_credentials: boolean;
  secret_fields_set: string[];   // 已設定的機密欄 key（不含明文值）
  values: Record<string, string>;  // 非機密欄（如 repo）的回填值
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

type DeliveryItemPreview = {
  target: string;
  destination: string;
  title: string;
  labels: string[];
  estimate: number;
  group: string;
  body_preview: string;
};

type PublishResult = {
  success: boolean;
  target: string;
  count: number;
  created: string[];
};

type Step =
  | { kind: "config" }
  | { kind: "preview"; items: DeliveryItemPreview[]; itemCount: number }
  | { kind: "publishing" }
  | { kind: "result"; result: PublishResult };

export function PublishModal({
  open, thread, apiBase, onClose,
}: {
  open: boolean;
  thread: string | null;
  apiBase: string;
  onClose: () => void;
}) {
  const [integrations, setIntegrations] = useState<IntegrationInfo[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<string>("github");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [step, setStep] = useState<Step>({ kind: "config" });
  const [error, setError] = useState<string | null>(null);
  const [cred, setCred] = useState<CredentialsStatus | null>(null);

  // 升級遷移：一次性清除舊版殘留在 localStorage 的憑證（機密已改存 server-side keystore）。
  useEffect(() => {
    try {
      Object.keys(window.localStorage)
        .filter((k) => k.startsWith("lodestar.publish"))
        .forEach((k) => window.localStorage.removeItem(k));
    } catch {
      /* localStorage 不可用時忽略 */
    }
  }, []);

  // 開 modal：fetch integrations + restore cached config
  useEffect(() => {
    if (!open) return;
    setError(null);
    setStep({ kind: "config" });
    fetch(`${apiBase}/api/integrations`)
      .then((r) => r.json())
      .then((d: { integrations: IntegrationInfo[] }) => {
        setIntegrations(d.integrations);
        if (d.integrations.length > 0 && !d.integrations.find((i) => i.target === selectedTarget)) {
          setSelectedTarget(d.integrations[0].target);
        }
      })
      .catch((e) => setError(`讀取 integrations 失敗：${e.message}`));
  }, [open, apiBase]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 切 target / 開啟：從 server-side keystore 載已存憑證狀態。
  // 非機密欄（如 repo）回填值；機密欄（token）只知是否已設定、留空待輸入。
  useEffect(() => {
    if (!open || !selectedTarget) return;
    let alive = true;
    fetch(`${apiBase}/api/integrations/${selectedTarget}/credentials`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: CredentialsStatus | null) => {
        if (!alive) return;
        setCred(d);
        setConfig(d?.values ?? {});
      })
      .catch(() => { if (alive) { setCred(null); setConfig({}); } });
    return () => { alive = false; };
  }, [open, selectedTarget, apiBase]);

  // Esc 關閉 + lock body scroll
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open, onClose]);

  const currentIntegration = integrations.find((i) => i.target === selectedTarget);
  const fields = currentIntegration?.config_schema?.fields ?? [];

  // 把目前 config（含機密）加密存到 server-side keystore；空字串欄位後端不覆寫既有值。
  const saveCredentials = async () => {
    const r = await fetch(`${apiBase}/api/integrations/${selectedTarget}/credentials`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (r.ok) setCred(await r.json());
  };

  const onPreview = async () => {
    if (!thread) return;
    setError(null);
    try {
      // 先把含機密的 config 存進 keystore；後續 preview/publish 只送非機密、機密由後端合併。
      await saveCredentials();
      const r = await fetch(`${apiBase}/api/stage/stories/${thread}/preview-delivery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: selectedTarget, config }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.detail?.message ?? r.statusText);
      }
      const data = await r.json();
      setStep({ kind: "preview", items: data.items, itemCount: data.item_count });
    } catch (e) {
      setError(`預覽失敗：${(e as Error).message}`);
    }
  };

  const onConfirmPublish = async () => {
    if (!thread) return;
    setError(null);
    setStep({ kind: "publishing" });
    try {
      const r = await fetch(`${apiBase}/api/stage/stories/${thread}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: selectedTarget, config }),
      });
      const data: PublishResult = await r.json();
      setStep({ kind: "result", result: data });
    } catch (e) {
      setError(`發佈失敗：${(e as Error).message}`);
      setStep({ kind: "config" });
    }
  };

  const close = () => {
    onClose();
    setStep({ kind: "config" });
    setError(null);
  };

  if (!open) return null;

  return (
    <div
      className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) close(); }}
      role="dialog"
      aria-modal="true"
    >
      <div className="shadow-anvil paper-texture relative w-full max-w-2xl border border-[var(--paper-edge)] bg-[var(--paper)]">
        <Header step={step} target={selectedTarget} thread={thread} onClose={close} />

        <div className="max-h-[60vh] overflow-y-auto px-6 py-5">
          {error && (
            <div className="mb-4 border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f47171]">
              {error}
            </div>
          )}

          {step.kind === "config" && (
            <ConfigStep
              integrations={integrations}
              selectedTarget={selectedTarget}
              onSelectTarget={setSelectedTarget}
              fields={fields}
              config={config}
              onUpdateConfig={setConfig}
              cred={cred}
            />
          )}

          {step.kind === "preview" && (
            <PreviewStep items={step.items} itemCount={step.itemCount} target={selectedTarget} />
          )}

          {step.kind === "publishing" && <PublishingStep target={selectedTarget} />}

          {step.kind === "result" && <ResultStep result={step.result} />}
        </div>

        <Footer step={step} hasThread={!!thread} onPreview={onPreview} onConfirm={onConfirmPublish} onBack={() => setStep({ kind: "config" })} onClose={close} />
      </div>
    </div>
  );
}

// ============================================================
//  Header / Footer
// ============================================================
function Header({ step, target, thread, onClose }: { step: Step; target: string; thread: string | null; onClose: () => void }) {
  const titles: Record<Step["kind"], string> = {
    config: "發佈到 tracker — 設定",
    preview: "預覽即將建立的 issue",
    publishing: "發佈中…",
    result: "發佈結果",
  };
  const subtitle: Record<Step["kind"], string> = {
    config: "POST /api/integrations · /preview-delivery",
    preview: `POST /api/stage/stories/${thread ?? ""}/preview-delivery`,
    publishing: `POST /api/stage/stories/${thread ?? ""}/publish`,
    result: `target: ${target}`,
  };
  return (
    <div className="flex items-start justify-between border-b border-[var(--rule)] px-6 py-4">
      <div>
        <h2 className="font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]">
          {titles[step.kind]}
        </h2>
        <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          {subtitle[step.kind]}
        </p>
      </div>
      <button onClick={onClose} aria-label="close" className="grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#cdd4df]">
        ×
      </button>
    </div>
  );
}

function Footer({ step, hasThread, onPreview, onConfirm, onBack, onClose }: {
  step: Step; hasThread: boolean;
  onPreview: () => void; onConfirm: () => void; onBack: () => void; onClose: () => void;
}) {
  return (
    <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
      <button onClick={onClose} className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]">
        取消
      </button>
      {step.kind === "config" && (
        <button
          onClick={onPreview}
          disabled={!hasThread}
          className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-50"
        >
          下一步：預覽
        </button>
      )}
      {step.kind === "preview" && (
        <>
          <button onClick={onBack} className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]">
            ← 改設定
          </button>
          <button
            onClick={onConfirm}
            className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)]"
          >
            確認發佈
          </button>
        </>
      )}
      {step.kind === "result" && (
        <button onClick={onClose} className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)]">
          完成
        </button>
      )}
    </div>
  );
}

// ============================================================
//  Config / Preview / Publishing / Result steps
// ============================================================
function ConfigStep({ integrations, selectedTarget, onSelectTarget, fields, config, onUpdateConfig, cred }: {
  integrations: IntegrationInfo[];
  selectedTarget: string;
  onSelectTarget: (t: string) => void;
  fields: IntegrationField[];
  config: Record<string, string>;
  onUpdateConfig: (c: Record<string, string>) => void;
  cred: CredentialsStatus | null;
}) {
  const savedSecret = (key: string) => cred?.secret_fields_set?.includes(key) ?? false;
  return (
    <div className="space-y-5">
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
                onClick={() => onSelectTarget(i.target)}
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
                onChange={(e) => onUpdateConfig({ ...config, [f.key]: e.target.value })}
                className="w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-mono)] text-[12.5px] text-[#e6ecf5] outline-none placeholder:text-[var(--ink-muted)] focus:border-[var(--polaris)]"
                placeholder={savedSecret(f.key) ? "•••••• 已儲存（留空沿用）" : f.type === "password" ? "••••••" : ""}
                autoComplete={f.type === "password" ? "new-password" : "off"}
                spellCheck={false}
              />
            </div>
          ))}
        </div>
        <p className="mt-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          ⓘ 機密（token）以 server-side keystore（Fernet）加密儲存，明文不留在瀏覽器；留空表示沿用已儲存值。
        </p>
      </div>
    </div>
  );
}

function PreviewStep({ items, itemCount, target }: { items: DeliveryItemPreview[]; itemCount: number; target: string }) {
  const grouped = useMemo(() => {
    const m: Record<string, DeliveryItemPreview[]> = {};
    items.forEach((it) => {
      const k = it.group || "(no epic)";
      if (!m[k]) m[k] = [];
      m[k].push(it);
    });
    return m;
  }, [items]);
  const totalEstimate = items.reduce((acc, it) => acc + it.estimate, 0);

  return (
    <div className="space-y-4">
      <div className="border border-[var(--rule)] bg-[var(--bg-elev)]/40 px-4 py-3">
        <div className="flex items-baseline gap-3">
          <span className="font-[family-name:var(--font-display)] text-[26px] font-semibold leading-none text-[#e6ecf5]">
            {itemCount}
          </span>
          <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            issues 將建立到 <span className="text-[var(--polaris)]">{target}</span> · 總估點 {totalEstimate}
          </span>
        </div>
      </div>
      {Object.entries(grouped).map(([group, list]) => (
        <div key={group}>
          <div className="mb-2 flex items-baseline gap-3">
            <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--polaris)]">
              {group}
            </span>
            <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
              {list.length} stories
            </span>
            <span className="h-px flex-1 bg-[var(--rule)]" />
          </div>
          <ul className="space-y-1.5">
            {list.map((it) => (
              <li key={it.title} className="flex items-baseline gap-2 border-l border-[var(--rule-dark)] pl-3">
                <span className="font-[family-name:var(--font-mono)] text-[11px] text-[#cdd4df] truncate">
                  {it.title}
                </span>
                <span className="border border-[var(--rule-dark)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[#7a8499]">
                  {it.estimate}h
                </span>
                {it.labels.slice(0, 4).map((l) => (
                  <code key={l} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] tracking-wider text-[var(--polaris)]">
                    {l}
                  </code>
                ))}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function PublishingStep({ target }: { target: string }) {
  return (
    <div className="flex flex-col items-center gap-4 py-10">
      <div className="grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">
        ↗
      </div>
      <div className="text-center">
        <div className="font-[family-name:var(--font-display)] text-[20px] font-semibold text-[#e6ecf5]">
          發佈到 {target}…
        </div>
        <p className="mt-2 font-[family-name:var(--font-sans)] text-[12.5px] leading-[1.6] text-[var(--ink-muted)]">
          每筆 issue 串行建立，請勿關閉視窗。
        </p>
      </div>
    </div>
  );
}

function ResultStep({ result }: { result: PublishResult }) {
  const failed = result.count - result.created.length;
  return (
    <div className="space-y-4">
      <div
        className={`border-l-4 px-4 py-3 ${
          result.success
            ? "border-[var(--approved)] bg-[color-mix(in_oklab,var(--approved)_8%,transparent)]"
            : "border-[#f47171] bg-[color-mix(in_oklab,#f47171_10%,transparent)]"
        }`}
      >
        <div className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">
          {result.success ? "✓ 發佈完成" : failed > 0 ? "⚠ 部分失敗" : "✗ 發佈失敗"}
        </div>
        <p className="mt-1 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          target {result.target} · 預計 {result.count} · 已建立 {result.created.length}
          {failed > 0 && <span className="text-[#f47171]"> · 失敗 {failed}</span>}
        </p>
      </div>
      {result.created.length > 0 ? (
        <div>
          <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            已建立的 issue
          </div>
          <ul className="max-h-[40vh] overflow-y-auto space-y-1">
            {result.created.map((url, i) => (
              <li key={url + i} className="flex items-center gap-2 border border-[var(--rule-dark)] bg-[var(--bg-elev)]/30 px-3 py-1.5">
                <span className="font-[family-name:var(--font-mono)] text-[10px] text-[var(--ink-muted)]">{i + 1}</span>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="truncate font-[family-name:var(--font-mono)] text-[11.5px] text-[var(--polaris)] hover:underline"
                >
                  {url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="font-[family-name:var(--font-sans)] text-[13px] text-[var(--ink-muted)]">
          沒有 issue 被建立。檢查 token / repo 設定是否正確，或看 backend log。
        </p>
      )}
    </div>
  );
}
