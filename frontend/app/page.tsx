"use client";

// M0 mock — 靜態假資料；M1 起 PRD 改吃 /api/stages（catalog-driven）+ /api/stage/{id}/generate|refine|chat。
// M2.2 mock review：Architecture / Stories 用 M2.1 E2E 真實 claude-cli 生成內容當靜態假資料，
//   先給看 UI 結構與排版；M2.3 才 wire 真實 API。
// Aesthetic：Industrial Cobalt × Drafting Dusk。

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  MOCK_ARCH_MARKDOWN,
  MOCK_STORIES_MARKDOWN,
} from "@/lib/mocks";
import {
  countRequirements,
  countStoriesAndEstimate,
  parseArchitecture,
  parseStories,
  type Story as ParsedStory,
} from "@/lib/parse";

// next/dynamic({ssr:false}) —— mermaid 套件依賴 window / document，不能 SSR
const MermaidDiagram = dynamic(() => import("@/components/MermaidDiagram"), {
  ssr: false,
  loading: () => (
    <div className="grid place-items-center py-8 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
      loading mermaid…
    </div>
  ),
});

type AttachmentInfo = {
  file_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  has_parsed_text: boolean;
  parse_error: string | null;
  created_at: number | null;
};

// ============================== M1：接後端 API ==============================
// 後端 base URL（同主機 8723）；前端 mock-only view 不依賴此。
const API_BASE =
  (typeof window !== "undefined" && (window as Window & { __LODESTAR_API__?: string }).__LODESTAR_API__) ||
  "http://localhost:8723";

type PrdBusy = false | "generate" | "refine" | "chat";

async function apiFetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let msg: string = r.statusText;
    try {
      const body = await r.json();
      msg = body?.detail?.message ?? body?.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return r.json();
}

// ============================== Mock data ==============================
type StageStatus = "approved" | "draft" | "locked";

const STAGES = [
  { id: "prd",          n: "01", label: "PRD",            caption: "PRODUCT REQUIREMENTS", status: "approved" as StageStatus, badge: "CHARTED",   agent: "3 agents · discussion" },
  { id: "architecture", n: "02", label: "Architecture",   caption: "SYSTEM DESIGN",        status: "draft" as StageStatus,    badge: "CHARTING", agent: "software_architect"     },
  { id: "stories",      n: "03", label: "Stories",        caption: "DELIVERABLE STORIES",  status: "draft" as StageStatus,    badge: "DRAFTED",  agent: "product_owner"          },
  { id: "implement",    n: "04", label: "Implementation", caption: "AUTO-CODE · M5",       status: "draft" as StageStatus,    badge: "DISPATCH", agent: "3 agents · dispatch"    },
];

const THREADS = [
  { id: "t1", name: "電商結帳重構",       workflow: "default",  glyph: "C" },
  { id: "t2", name: "行動 App 登入升級", workflow: "default",  glyph: "M" },
  { id: "t3", name: "內部營運儀表板",     workflow: "prd→arch", glyph: "O" },
];

const NAV = [
  { id: "workspace", label: "WORKSPACE" },
  { id: "workflows", label: "WORKFLOWS" },
  { id: "agents",    label: "AGENTS" },
  { id: "plugins",   label: "PLUGINS" },
];

// ---------- Workflows / Agents / Plugins ----------
type Binding = { agent_id: string; role: Collab };
type CollabMode = "single" | "discussion" | "dispatch";

const WORKFLOWS: Array<{
  id: string; label: string; desc: string; stages: string[];
  source: string; builtin: boolean; threads: number;
  agent_bindings: Record<string, Binding[]>;
  collab_mode: Record<string, CollabMode>;
}> = [
  {
    id: "default", label: "Standard Pipeline", desc: "PRD → Architecture → Stories → (M5) Implementation",
    stages: ["prd", "architecture", "stories", "implement"],
    source: "builtin_core_stages", builtin: true, threads: 4,
    agent_bindings: {
      prd: [
        { agent_id: "sales_voice",     role: "peer" },
        { agent_id: "product_manager", role: "peer" },
        { agent_id: "system_analyst",  role: "lead" },
      ],
      architecture: [{ agent_id: "software_architect", role: "lead" }],
      stories:      [{ agent_id: "product_owner",      role: "lead" }],
      implement: [
        { agent_id: "implementation_lead", role: "lead" },
        { agent_id: "frontend_engineer",   role: "subagent" },
        { agent_id: "backend_engineer",    role: "subagent" },
      ],
    },
    collab_mode: { prd: "discussion", architecture: "single", stories: "single", implement: "dispatch" },
  },
  {
    id: "prd-only", label: "PRD Only", desc: "只產出 PRD，不展開下游",
    stages: ["prd"], source: "user", builtin: false, threads: 1,
    agent_bindings: { prd: [{ agent_id: "system_analyst", role: "lead" }] },
    collab_mode: { prd: "single" },
  },
  {
    id: "lite", label: "Lite Flow", desc: "跳過架構，從 PRD 直接到 Stories",
    stages: ["prd", "stories"], source: "user", builtin: false, threads: 1,
    agent_bindings: {
      prd:     [{ agent_id: "system_analyst", role: "lead" }],
      stories: [{ agent_id: "product_owner",  role: "lead" }],
    },
    collab_mode: { prd: "single", stories: "single" },
  },
];

// agent_bindings 從 1:1 擴展成 1:N + 協作角色
// - lead     ：主導 stage 的 agent，最後負責合成 artifact
// - peer     ：平行 agent，跟 lead 一起在 chat 內討論（PRD 加 Sales/PM 就是這型）
// - subagent ：被 lead 分派任務的下手（M5 前端/後端 engineer 屬這型）
type Collab = "lead" | "peer" | "subagent";

const AGENTS: Array<{
  id: string; name: string; stage: string; collab: Collab; subagentOf: string | null;
  model: string; iter: number; enabled: boolean; seed: string; prompt: string; skills: string[]; tools: string[];
}> = [
  { id: "system_analyst",      name: "System Analyst",      stage: "prd",          collab: "lead",     subagentOf: null,                  model: "claude-cli", iter: 2, enabled: true,  seed: "builtin_agents",    prompt: "資深系統分析師，把模糊的想法收斂成完整的 PRD…",                skills: ["NFR 抽取", "並發發掘", "合規檢查"], tools: [] },
  { id: "sales_voice",         name: "Sales Voice",         stage: "prd",          collab: "peer",     subagentOf: null,                  model: "claude-cli", iter: 1, enabled: true,  seed: "user",              prompt: "業務／銷售視角：競品、客戶聲音、業績壓力、市場時機…",       skills: ["競品分析", "客戶洞察"],               tools: [] },
  { id: "product_manager",     name: "Product Manager",     stage: "prd",          collab: "peer",     subagentOf: null,                  model: "claude-cli", iter: 1, enabled: true,  seed: "user",              prompt: "PM 視角：roadmap、優先級、OKR、利益關係人…",                  skills: ["優先級", "風險評估"],                 tools: [] },
  { id: "software_architect",  name: "Software Architect",  stage: "architecture", collab: "lead",     subagentOf: null,                  model: "claude-cli", iter: 1, enabled: true,  seed: "builtin_agents",    prompt: "資深軟體架構師，根據 PRD 設計可實作的系統架構…",             skills: ["分層設計", "容量規劃", "權衡分析"],  tools: [] },
  { id: "product_owner",       name: "Product Owner",       stage: "stories",      collab: "lead",     subagentOf: null,                  model: "claude-cli", iter: 1, enabled: true,  seed: "builtin_agents",    prompt: "PO：將 PRD + 架構切成可交付故事，含 AC、估點、分組…",        skills: ["故事點估", "AC 撰寫"],                tools: [] },
  { id: "implementation_lead", name: "Implementation Lead", stage: "implement",    collab: "lead",     subagentOf: null,                  model: "claude-cli", iter: 1, enabled: false, seed: "builtin_implement", prompt: "（M5）拆 story 為前端/後端任務 → 分派 subagents → 合併 PR",  skills: ["task split", "dispatch", "PR review"], tools: ["bash", "git"] },
  { id: "frontend_engineer",   name: "Frontend Engineer",   stage: "implement",    collab: "subagent", subagentOf: "implementation_lead", model: "claude-cli", iter: 3, enabled: false, seed: "builtin_implement", prompt: "（M5 subagent）前端：UI、state、accessibility、TDD…",         skills: ["React/Next", "Tailwind", "TDD"],     tools: ["bash", "file-edit"] },
  { id: "backend_engineer",    name: "Backend Engineer",    stage: "implement",    collab: "subagent", subagentOf: "implementation_lead", model: "claude-cli", iter: 3, enabled: false, seed: "builtin_implement", prompt: "（M5 subagent）後端：API、DB、migrations、pytest…",            skills: ["FastAPI", "SQL", "pytest"],          tools: ["bash", "file-edit"] },
];

const PLUGINS = [
  { id: "builtin_integrations", name: "Built-in Delivery Integrations", version: "1.0.0", desc: "GitHub / Jira / GitLab delivery targets（preview-before-publish）", provides: { stages: [], workflows: [], agents: [], integrations: ["github", "jira", "gitlab"] }, enabled: true,  builtin: true, requiresRebuild: false, loadError: null as string | null },
  { id: "builtin_core_stages",  name: "Core Requirement Stages",        version: "(planned · M1)", desc: "PRD / Architecture / Stories 內建 stage", provides: { stages: ["prd", "architecture", "stories"], workflows: ["default"], agents: [], integrations: [] }, enabled: false, builtin: true, requiresRebuild: false, loadError: "尚未實作（M1 milestone）" },
  { id: "builtin_agents",       name: "Built-in Stage Agents",          version: "(planned · M2)", desc: "PRD / Architecture / Stories 對應 seed agents", provides: { stages: [], workflows: [], agents: ["system_analyst", "software_architect", "product_owner"], integrations: [] }, enabled: false, builtin: true, requiresRebuild: false, loadError: "尚未實作（M2 milestone）" },
];

// ---------- Chat（multi-agent discussion）----------
type SpeakerId = "system_analyst" | "sales_voice" | "product_manager";
type Chip = { label: string; selected?: boolean };
type ChatMsg =
  | { role: "assistant"; speaker: SpeakerId; content: string; chips?: Chip[]; multi?: boolean }
  | { role: "user"; content: string; toAgent?: SpeakerId };

// Speaker 視覺識別：peer / lead 用色彩區分，user reply 可指定 ↳ to <agent>
const SPEAKER_STYLES: Record<SpeakerId, { abbr: string; color: string; tier: string }> = {
  system_analyst:  { abbr: "SA", color: "#5b8cff", tier: "LEAD" },  // cobalt
  sales_voice:     { abbr: "SV", color: "#f59e0b", tier: "PEER" },  // amber
  product_manager: { abbr: "PM", color: "#a78bfa", tier: "PEER" },  // violet
};

const CHAT: ChatMsg[] = [
  { role: "assistant", speaker: "sales_voice",     content: "從業務面切入：競品的 LINE Pay 已上線 2 季，Apple Pay 對 iOS 用戶轉換尤其關鍵——這兩個建議列 must-have。" },
  { role: "assistant", speaker: "product_manager", content: "我加進來：本季 OKR 列了 PCI-DSS L1，這次重構正好一併處理；Apple Pay 同意，但要 PM 跟工程確認導入成本。" },
  { role: "assistant", speaker: "system_analyst",  content: "好，先把要素收斂：\n· 多金流 must：信用卡 / Apple Pay / LINE Pay\n· 合規：PCI-DSS L1\n\n先確認尖峰並發等級（每秒結帳請求）？",
    chips: [{ label: "< 500" }, { label: "500–2k" }, { label: "2k–5k", selected: true }, { label: "5k+" }] },
  { role: "user", content: "2k–5k", toAgent: "system_analyst" },
  { role: "assistant", speaker: "sales_voice",     content: "大促常見短暫衝到 5k+，建議留 30% headroom——架構同學要能接得住。" },
  { role: "assistant", speaker: "product_manager", content: "headroom 我支持。卡資料處理方式選哪個？影響合規工程量。",
    chips: [{ label: "Tokenization · 不入庫", selected: true }, { label: "入庫（PCI-DSS L1）" }] },
  { role: "user", content: "Tokenization；PCI-DSS L1", toAgent: "product_manager" },
  { role: "assistant", speaker: "system_analyst",  content: "整合三方意見：FR-1..4 ＋ NFR-1..4 ＋ OPS-1..2。請審閱核准；下游架構 stage 才能展開。" },
];

// ============================== Page ==============================
export default function Page() {
  const [nav, setNav] = useState<string>("workspace");
  const [selected, setSelected] = useState<string>("prd");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [docFs, setDocFs] = useState(false);

  // ===== M1：thread + PRD 真實 state =====
  const [thread, setThread] = useState<string | null>(null);
  const [prdArtifact, setPrdArtifact] = useState<string>("");
  const [prdStatus, setPrdStatus] = useState<string>("draft");
  const [busy, setBusy] = useState<PrdBusy>(false);
  const [err, setErr] = useState<string | null>(null);
  // ===== M1.1：PRD attachments =====
  const [attachments, setAttachments] = useState<AttachmentInfo[]>([]);
  const [uploading, setUploading] = useState<boolean>(false);

  // bootstrap thread：localStorage 取舊；無則建新（setAndPersist pattern，spec §11 陷阱）
  useEffect(() => {
    let mounted = true;
    const stored = typeof window !== "undefined" ? window.localStorage.getItem("lodestar.thread") : null;
    if (stored) {
      setThread(stored);
      return;
    }
    apiFetch<{ thread_id: string }>("/api/projects", {
      method: "POST", body: JSON.stringify({ name: "新需求" }),
    })
      .then((p) => {
        if (!mounted) return;
        window.localStorage.setItem("lodestar.thread", p.thread_id);
        setThread(p.thread_id);
      })
      .catch((e: Error) => mounted && setErr(`建立 thread 失敗：${e.message}`));
    return () => { mounted = false; };
  }, []);

  // 拿 PRD state（artifact + status）
  const refreshPrd = useCallback(async (tid: string) => {
    try {
      const s = await apiFetch<{ artifact: string; status: string }>(`/api/stage/prd/${tid}`);
      setPrdArtifact(s.artifact || "");
      setPrdStatus(s.status);
    } catch (e) {
      setErr(`讀取 PRD 失敗：${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    if (!thread) return;
    refreshPrd(thread);
  }, [thread, refreshPrd]);

  // 拿 attachments
  const refreshAttachments = useCallback(async (tid: string) => {
    try {
      const r = await apiFetch<{ attachments: AttachmentInfo[] }>(`/api/stage/prd/${tid}/attachments`);
      setAttachments(r.attachments);
    } catch {
      /* silent: 端點未啟動或暫時失敗時不影響主流程 */
    }
  }, []);

  useEffect(() => {
    if (!thread) return;
    refreshAttachments(thread);
  }, [thread, refreshAttachments]);

  // upload 附件（不走 apiFetch，因為 multipart FormData）
  const onUploadAttachment = useCallback(async (file: File) => {
    if (!thread || uploading) return;
    setErr(null);
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API_BASE}/api/stage/prd/${thread}/attachments`, {
        method: "POST",
        body: fd,
      });
      if (!r.ok) {
        let msg = r.statusText;
        try {
          const body = await r.json();
          msg = body?.detail?.message ?? body?.detail ?? JSON.stringify(body);
        } catch { /* ignore */ }
        throw new Error(msg);
      }
      await refreshAttachments(thread);
    } catch (e) {
      setErr(`上傳附件失敗：${(e as Error).message}`);
    } finally {
      setUploading(false);
    }
  }, [thread, uploading, refreshAttachments]);

  // delete 附件
  const onDeleteAttachment = useCallback(async (fileId: string) => {
    if (!thread) return;
    try {
      await apiFetch(`/api/stage/prd/${thread}/attachments/${fileId}`, { method: "DELETE" });
      setAttachments((prev) => prev.filter((a) => a.file_id !== fileId));
    } catch (e) {
      setErr(`刪除附件失敗：${(e as Error).message}`);
    }
  }, [thread]);

  // actions
  const onGenerate = useCallback(async () => {
    if (!thread || busy) return;
    setErr(null);
    setBusy("generate");
    try {
      const data = await apiFetch<{ artifact: string }>("/api/stage/prd/generate", {
        method: "POST",
        body: JSON.stringify({ thread_id: thread, model_choice: "claude-cli" }),
      });
      setPrdArtifact(data.artifact || "");
      setPrdStatus("draft");
    } catch (e) {
      setErr(`生成 PRD 失敗：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [thread, busy]);

  const onRefine = useCallback(async () => {
    if (!thread || busy) return;
    const instruction = window.prompt("輸入修訂指令（例：加上 OAuth 登入、提高並發到 10k）：");
    if (!instruction || !instruction.trim()) return;
    setErr(null);
    setBusy("refine");
    try {
      const data = await apiFetch<{ artifact: string }>("/api/stage/prd/refine", {
        method: "POST",
        body: JSON.stringify({ thread_id: thread, model_choice: "claude-cli", instruction }),
      });
      setPrdArtifact(data.artifact || "");
      setPrdStatus("draft");
    } catch (e) {
      setErr(`修訂 PRD 失敗：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [thread, busy]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDocFs(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const showSidebar = nav === "workspace";

  return (
    <>
      <div className="relative z-10 flex h-full flex-col overflow-hidden">
        <TopBar nav={nav} onNav={setNav} thread={thread} />
        {err && (
          <div className="border-b border-[color-mix(in_oklab,#f59e0b_40%,transparent)] bg-[color-mix(in_oklab,#f59e0b_12%,transparent)] px-6 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[#f59e0b]">
            ⚠ {err}
            <button onClick={() => setErr(null)} className="ml-3 underline">關閉</button>
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          {showSidebar && <Sidebar open={sidebarOpen} onToggle={() => setSidebarOpen((o) => !o)} />}
          <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
            {nav === "workspace" && (
              <>
                <StageHeader selected={selected} onSelect={setSelected} />
                <div className="flex min-h-0 flex-1">
                  {selected === "prd" && (
                    <PrdWorkspace
                      onOpenFs={() => setDocFs(true)}
                      thread={thread}
                      artifact={prdArtifact}
                      status={prdStatus}
                      busy={busy}
                      onGenerate={onGenerate}
                      onRefine={onRefine}
                      attachments={attachments}
                      uploading={uploading}
                      onUploadAttachment={onUploadAttachment}
                      onDeleteAttachment={onDeleteAttachment}
                    />
                  )}
                  {selected === "architecture" && <ArchWorkspace />}
                  {selected === "stories"      && <StoriesWorkspace />}
                  {selected === "implement"    && <ImplementWorkspace />}
                </div>
              </>
            )}
            {nav === "workflows" && <WorkflowsView />}
            {nav === "agents"    && <AgentsView />}
            {nav === "plugins"   && <PluginsView />}
            <BuildSeal />
          </main>
        </div>
      </div>
      {docFs && <DocFullscreen onClose={() => setDocFs(false)} prdArtifact={prdArtifact} />}
    </>
  );
}

// ============================== TopBar ==============================
function TopBar({ nav, onNav, thread }: { nav: string; onNav: (n: string) => void; thread: string | null }) {
  return (
    <header className="rise-1 relative flex h-14 shrink-0 items-center justify-between border-b border-[var(--rule-dark)] px-6">
      <div className="flex items-center gap-10">
        <LodestarBrand />
        <nav className="relative flex items-center gap-7">
          {NAV.map((n) => {
            const active = n.id === nav;
            return (
              <button
                key={n.id}
                onClick={() => onNav(n.id)}
                className={`relative font-[family-name:var(--font-mono)] text-[11px] tracking-[0.2em] transition ${
                  active ? "text-[#e6ecf5]" : "text-[#5e6878] hover:text-[#b8c0cf]"
                }`}
              >
                {n.label}
                {active && (
                  <span className="glow-star absolute -bottom-[19px] left-0 right-0 h-[2px] bg-[var(--polaris)]" />
                )}
              </button>
            );
          })}
        </nav>
      </div>
      <div className="flex items-center gap-4 font-[family-name:var(--font-mono)] text-[11px]">
        <div className="flex items-center gap-1.5">
          <span className="text-[var(--ink-muted)]">THREAD</span>
          <code className="text-[#cdd4df]">{thread ?? "—"}</code>
        </div>
        <div className="h-3 w-px bg-[var(--rule-dark)]" />
        <div className="flex items-center gap-1.5">
          <span className="text-[var(--ink-muted)]">MODEL</span>
          <span className="text-[#b8c0cf]">claude-cli</span>
        </div>
        <div className="h-3 w-px bg-[var(--rule-dark)]" />
        <div className="flex items-center gap-2">
          <span className="glow-approved relative inline-block h-1.5 w-1.5 rounded-full bg-[var(--approved)]" />
          <span className="text-[#b8c0cf]">3</span>
          <span className="text-[var(--ink-muted)]">PLUGINS LOADED</span>
        </div>
      </div>
    </header>
  );
}

function LodestarBrand() {
  return (
    <div className="flex min-w-0 items-center gap-3" aria-label="Lodestar requirement charting">
      <span className="lodestar-logo-mark grid h-9 w-9 shrink-0 place-items-center" aria-hidden="true">
        <svg viewBox="0 0 44 44" fill="none" focusable="false">
          <circle className="lodestar-logo-ring" cx="22" cy="22" r="18" />
          <circle className="lodestar-logo-orbit" cx="22" cy="22" r="15.5" />
          <path className="lodestar-logo-bearing" d="M22 4.75v5.5M22 33.75v5.5M4.75 22h5.5M33.75 22h5.5" />
          <path className="lodestar-logo-course" d="M11 29.5C15.5 20.5 19.5 19 22 22s6.5-3.5 11-11" />
          <path className="lodestar-logo-star" d="M22 7.5l3.35 10.15L36 22l-10.65 4.35L22 36.5l-3.35-10.15L8 22l10.65-4.35L22 7.5Z" />
          <circle className="lodestar-logo-core" cx="22" cy="22" r="2.4" />
          <circle className="lodestar-logo-fix" cx="33" cy="11" r="1.55" />
        </svg>
      </span>
      <div className="flex items-baseline gap-2.5">
        <span className="font-[family-name:var(--font-display)] text-[22px] font-semibold leading-none text-[#e6ecf5]">
          Lodestar
        </span>
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          requirement charting
        </span>
      </div>
    </div>
  );
}

// ============================== Sidebar ==============================
function Sidebar({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  if (!open) {
    return (
      <aside className="rise-2 flex w-14 shrink-0 flex-col items-center border-r border-[var(--rule-dark)] bg-[var(--bg-elev)]/40 py-3">
        <button onClick={onToggle} title="展開側欄"
          className="mb-3 grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#b8c0cf]">
          <ChevronDouble dir="right" />
        </button>
        <div className="mb-3 h-px w-6 bg-[var(--rule-dark)]" />
        {THREADS.map((t, i) => {
          const active = i === 0;
          return (
            <button key={t.id} title={t.name}
              className={`mb-1.5 grid h-9 w-9 place-items-center border font-[family-name:var(--font-display)] text-[15px] transition ${
                active ? "glow-star border-[var(--polaris)] text-[var(--polaris)]"
                       : "border-[var(--rule-dark)] text-[#7a8499] hover:border-[#404a5b] hover:text-[#b8c0cf]"
              }`}>
              {t.glyph}
            </button>
          );
        })}
      </aside>
    );
  }
  return (
    <aside className="rise-2 flex w-72 shrink-0 flex-col border-r border-[var(--rule-dark)] bg-[var(--bg-elev)]/40">
      <div className="flex items-center justify-between border-b border-[var(--rule-dark)] px-5 py-3.5">
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          / threads · {THREADS.length}
        </span>
        <button onClick={onToggle} title="收合側欄"
          className="grid h-6 w-6 place-items-center text-[var(--ink-muted)] transition hover:text-[#b8c0cf]">
          <ChevronDouble dir="left" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {THREADS.map((t, i) => {
          const active = i === 0;
          return (
            <button key={t.id}
              className={`group relative flex w-full items-center gap-3 px-5 py-3 text-left transition ${
                active ? "bg-[var(--anvil)]/60" : "hover:bg-[var(--bg-elev)]"
              }`}>
              {active && <span className="absolute top-3 bottom-3 left-0 w-[3px] bg-[var(--polaris)]" />}
              <span className={`grid h-9 w-9 shrink-0 place-items-center border font-[family-name:var(--font-display)] text-[15px] ${
                  active ? "border-[var(--polaris)] text-[var(--polaris)]" : "border-[var(--rule-dark)] text-[#7a8499] group-hover:border-[#404a5b]"
                }`}>
                {t.glyph}
              </span>
              <div className="min-w-0 flex-1">
                <div className={`truncate text-[13px] ${active ? "text-[#e6ecf5]" : "text-[#97a0b3]"}`}>{t.name}</div>
                <div className="mt-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{t.workflow}</div>
              </div>
            </button>
          );
        })}
      </div>
      <button className="m-3 flex items-center justify-between border border-dashed border-[var(--rule-dark)] px-4 py-2.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[#5e6878] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">
        <span>＋ new thread</span>
        <span className="text-[var(--ink-muted)]">⌘N</span>
      </button>
    </aside>
  );
}

// ============================== Stage header ==============================
function StageHeader({ selected, onSelect }: { selected: string; onSelect: (s: string) => void }) {
  return (
    <div className="rise-3 border-b border-[var(--rule-dark)] px-10 pt-6 pb-4">
      <div className="mb-3 flex items-center gap-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        <span>thread</span><span className="text-[#2a3041]">/</span>
        <span className="text-[#b8c0cf]">電商結帳重構</span><span className="text-[#2a3041]">·</span>
        <span>workflow</span><span className="text-[#2a3041]">/</span><span className="text-[var(--polaris)]">default</span>
        <span className="ml-auto">stages by <span className="text-[#b8c0cf]">builtin_core_stages</span></span>
      </div>
      <h1 className="mb-5 font-[family-name:var(--font-display)] text-[32px] font-semibold leading-none tracking-tight text-[#e6ecf5]">
        Requirement <em className="font-[family-name:var(--font-display)] italic text-[var(--polaris)]">chart</em>
      </h1>
      <ol className="flex items-stretch">
        {STAGES.map((s, i) => {
          const isSelected = s.id === selected;
          const isLocked = s.status === "locked";
          const topBorder = isSelected ? "border-t-[var(--polaris)]" : s.status === "approved" ? "border-t-[var(--approved)]" : isLocked ? "border-t-[var(--locked)]" : "border-t-[var(--rule-dark)]";
          const badgeColor = s.status === "approved" ? "text-[var(--approved)]" : isLocked ? "text-[var(--locked)]" : isSelected ? "text-[var(--polaris)]" : "text-[var(--ink-muted)]";
          return (
            <li key={s.id} className="flex flex-1 items-stretch">
              <button disabled={isLocked} onClick={() => !isLocked && onSelect(s.id)}
                className={`group relative w-full border-t-[2px] ${topBorder} py-3 pr-6 text-left transition ${isLocked ? "cursor-not-allowed opacity-55" : ""} ${isSelected ? "" : "hover:border-t-[#2e3441]"}`}>
                <div className="flex items-baseline gap-3">
                  <span className={`font-[family-name:var(--font-display)] text-[26px] font-semibold leading-none ${isLocked ? "text-[#404a5b]" : "text-[#e6ecf5]"}`}>{s.n}</span>
                  <span className={`font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] ${badgeColor} ${s.status === "draft" && s.id !== selected ? "pulse-star" : ""}`}>{s.badge}</span>
                </div>
                <div className={`mt-1.5 font-[family-name:var(--font-display)] text-[16px] ${isLocked ? "text-[#5e6878]" : "text-[#cdd4df]"}`}>{s.label}</div>
                <div className="mt-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{s.caption} · {s.agent}</div>
              </button>
              {i < STAGES.length - 1 && <div className="my-3 w-px self-stretch bg-[var(--rule-dark)]" />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ============================== PRD workspace (M1：真實 API) ==============================
function PrdWorkspace({
  onOpenFs, thread, artifact, status, busy, onGenerate, onRefine,
  attachments, uploading, onUploadAttachment, onDeleteAttachment,
}: {
  onOpenFs: () => void;
  thread: string | null;
  artifact: string;
  status: string;
  busy: PrdBusy;
  onGenerate: () => void;
  onRefine: () => void;
  attachments: AttachmentInfo[];
  uploading: boolean;
  onUploadAttachment: (f: File) => void;
  onDeleteAttachment: (fileId: string) => void;
}) {
  const hasContent = artifact.trim().length > 0;
  const isApproved = status === "approved";
  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="prd" stage="specify" op="generate_prd" right={
          <>
            {hasContent && (isApproved ? <ApprovedSeal /> : <DraftPill />)}
            <IconBtn onClick={onOpenFs} title="全螢幕閱讀"><ExpandIcon /></IconBtn>
          </>
        } />
        {hasContent && (
          <AttachmentStrip
            thread={thread}
            attachments={attachments}
            uploading={uploading}
            onUpload={onUploadAttachment}
            onDelete={onDeleteAttachment}
          />
        )}
        <article className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)] text-[var(--ink)]">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {hasContent ? (
              <PrdArtifactView artifact={artifact} />
            ) : (
              <PrdEmptyState
                busy={busy}
                thread={thread}
                onGenerate={onGenerate}
                attachments={attachments}
                uploading={uploading}
                onUpload={onUploadAttachment}
                onDelete={onDeleteAttachment}
              />
            )}
          </div>
        </article>
        <BottomMeta
          left={
            <>
              {hasContent
                ? <>{Array.from(artifact.matchAll(/`?FR-\d+/gi)).length} FR · {Array.from(artifact.matchAll(/`?NFR-\d+/gi)).length} NFR · {artifact.length} chars · charted by system_analyst</>
                : <>empty · awaiting generation</>}
            </>
          }
          right={<>thread <code className="text-[#cdd4df]">{thread ?? "(bootstrapping…)"}</code> · depends_on <span className="text-[#5e6878]">(root)</span></>}
        />
        <div className="mt-4 flex items-center justify-end gap-2">
          <ToolBtn onClick={onRefine} disabled={!thread || !hasContent || !!busy}>
            {busy === "refine" ? "Refining…" : "Refine…"}
          </ToolBtn>
          <ToolBtn onClick={onGenerate} disabled={!thread || !!busy}>
            {busy === "generate" ? "Generating…" : hasContent ? "重新生成" : "生成 PRD"}
          </ToolBtn>
          <ToolBtn primary disabled={!hasContent || !!busy}>
            {isApproved ? "已核准 ✓" : "核准"}
          </ToolBtn>
        </div>
      </section>
      <ChatPanel />
    </div>
  );
}

function PrdEmptyState({
  busy, thread, onGenerate,
  attachments, uploading, onUpload, onDelete,
}: {
  busy: PrdBusy;
  thread: string | null;
  onGenerate: () => void;
  attachments: AttachmentInfo[];
  uploading: boolean;
  onUpload: (f: File) => void;
  onDelete: (fileId: string) => void;
}) {
  return (
    <div className="flex h-full items-start justify-center overflow-y-auto px-10 py-10">
      <div className="w-full max-w-xl">
        {/* Heading */}
        <div className="text-center">
          <div className="mx-auto mb-5 grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">
            01
          </div>
          <h3 className="font-[family-name:var(--font-display)] text-[26px] font-semibold text-[#e6ecf5]">
            尚未標繪
          </h3>
          <p className="mt-2 text-[13px] leading-6 text-[#7a8499]">
            PRD 是 pipeline 的起點。上傳既有需求文件作為 SA 的參考，
            <br />
            或直接點下方按鈕讓 SA 與你對話收斂。
          </p>
        </div>

        {/* Attachments section（大型 drop zone）*/}
        <div className="mt-8">
          <div className="mb-3 flex items-baseline justify-between">
            <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-[#e6ecf5]">
              參考文件
              <span className="ml-2 font-[family-name:var(--font-mono)] text-[11px] font-normal text-[var(--ink-muted)]">
                （可選）
              </span>
            </h4>
            {attachments.length > 0 && (
              <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--polaris)]">
                {attachments.length} 個檔案
              </span>
            )}
          </div>
          <AttachmentDropZone
            thread={thread}
            uploading={uploading}
            onUpload={onUpload}
            hasAttachments={attachments.length > 0}
          />
          {attachments.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {attachments.map((a) => (
                <AttachmentChip key={a.file_id} a={a} onDelete={() => onDelete(a.file_id)} />
              ))}
            </div>
          )}
        </div>

        {/* Generate CTA */}
        <div className="mt-10 text-center">
          <div className="mx-auto mb-5 h-px w-24 bg-[var(--rule-dark)]" />
          <div className="mb-4 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            agent: system_analyst · model: claude-cli
          </div>
          <button
            onClick={onGenerate}
            disabled={!thread || !!busy}
            className="border-2 border-[var(--polaris)] bg-[var(--polaris)] px-8 py-3 font-[family-name:var(--font-mono)] text-[12px] uppercase tracking-[0.22em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-50"
          >
            {busy === "generate" ? "★  charting…（30–60s）" : "✦  chart PRD"}
          </button>
          {attachments.length > 0 && (
            <p className="mt-3 font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">
              SA 會參考上述 {attachments.length} 個文件生成 PRD
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function PrdArtifactView({ artifact }: { artifact: string }) {
  // M1：直接渲染原始 markdown（preserve whitespace）。
  // M2+：可換成 react-markdown 或自製 lightweight renderer。
  return (
    <div className="mx-auto px-10 py-10 max-w-none">
      <pre className="whitespace-pre-wrap font-[family-name:var(--font-mono)] text-[13px] leading-[1.8] text-[#cdd4df]">
        {artifact}
      </pre>
    </div>
  );
}

// ============================== Attachments：strip（compact）+ drop zone（大型）==============================
const ATTACH_ACCEPT = ".md,.markdown,.txt,.csv,.tsv,.json,.xml,.yaml,.yml,.html,.log,.pdf,.docx,.png,.jpg,.jpeg,.webp,.gif,.bmp";

/** 已有 PRD 時的 compact strip：cobalt-accent border、明顯的「+ 加檔案」按鈕。 */
function AttachmentStrip({
  thread, attachments, uploading, onUpload, onDelete,
}: {
  thread: string | null;
  attachments: AttachmentInfo[];
  uploading: boolean;
  onUpload: (f: File) => void;
  onDelete: (fileId: string) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  if (!thread) return null;

  const handle = (file: File | undefined) => file && onUpload(file);

  return (
    <div className="mb-3 flex items-center gap-3 border-l-2 border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_6%,transparent)] px-3 py-2">
      <div className="flex shrink-0 items-baseline gap-2">
        <span className="text-[14px]">📎</span>
        <span className="font-[family-name:var(--font-display)] text-[13px] font-semibold text-[#e6ecf5]">
          參考文件
        </span>
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          · {attachments.length}
        </span>
      </div>

      <div className="flex flex-1 flex-wrap items-center gap-1.5">
        {attachments.length === 0 ? (
          <span className="font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">
            尚未上傳；點右側加檔案讓 SA 參考。
          </span>
        ) : (
          attachments.map((a) => (
            <AttachmentChip key={a.file_id} a={a} onDelete={() => onDelete(a.file_id)} />
          ))
        )}
      </div>

      <button
        onClick={() => !uploading && fileInput.current?.click()}
        disabled={uploading}
        className="shrink-0 border border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_18%,transparent)] px-3 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--polaris)] transition hover:bg-[color-mix(in_oklab,var(--polaris)_32%,transparent)] disabled:opacity-50"
      >
        {uploading ? "uploading…" : "＋ 加檔案"}
      </button>
      <input
        ref={fileInput}
        type="file"
        onChange={(e) => { handle(e.target.files?.[0]); e.target.value = ""; }}
        className="hidden"
        accept={ATTACH_ACCEPT}
      />
    </div>
  );
}

/** Empty state 內的大型 drop zone：拖放 + 點擊選檔的主要入口。 */
function AttachmentDropZone({
  thread, uploading, onUpload, hasAttachments,
}: {
  thread: string | null;
  uploading: boolean;
  onUpload: (f: File) => void;
  hasAttachments: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  if (!thread) return null;
  const handle = (file: File | undefined) => file && onUpload(file);

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files[0]); }}
      onClick={() => !uploading && fileInput.current?.click()}
      className={`cursor-pointer border-2 border-dashed px-6 py-8 text-center transition ${
        dragging
          ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_14%,transparent)]"
          : "border-[var(--rule-dark)] bg-[var(--bg-elev)]/30 hover:border-[var(--polaris)] hover:bg-[var(--bg-elev)]/60"
      }`}
    >
      <div className="text-[30px] leading-none">📎</div>
      <div className="mt-2 font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">
        {uploading ? "上傳中…" : hasAttachments ? "再加一個檔案" : "拖放或點擊選檔"}
      </div>
      <div className="mt-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        md · txt · pdf · docx · png · jpg
      </div>
      <input
        ref={fileInput}
        type="file"
        onChange={(e) => { handle(e.target.files?.[0]); e.target.value = ""; }}
        className="hidden"
        accept={ATTACH_ACCEPT}
      />
    </div>
  );
}

function AttachmentChip({ a, onDelete }: { a: AttachmentInfo; onDelete: () => void }) {
  const sizeStr =
    a.size_bytes < 1024 ? `${a.size_bytes}B` :
    a.size_bytes < 1024 * 1024 ? `${(a.size_bytes / 1024).toFixed(0)}KB` :
    `${(a.size_bytes / 1024 / 1024).toFixed(1)}MB`;
  const ok = a.has_parsed_text;
  return (
    <span
      className={`flex items-center gap-1.5 border px-2 py-1 ${
        ok
          ? "border-[var(--rule-dark)] bg-[var(--bg)]"
          : "border-[color-mix(in_oklab,#f59e0b_40%,transparent)] bg-[color-mix(in_oklab,#f59e0b_8%,transparent)]"
      }`}
      title={a.parse_error || undefined}
    >
      <span className="text-[11px]">{ok ? "📎" : "⚠"}</span>
      <span className="font-[family-name:var(--font-mono)] text-[11px] text-[#cdd4df]">{a.filename}</span>
      <span className="font-[family-name:var(--font-mono)] text-[10px] text-[var(--ink-muted)]">
        · {sizeStr}
      </span>
      {!ok && (
        <span className="font-[family-name:var(--font-mono)] text-[10px] text-[#f59e0b]">未解析</span>
      )}
      <button
        onClick={onDelete}
        title="刪除"
        className="ml-1 text-[var(--ink-muted)] transition hover:text-[#f59e0b]"
      >
        ×
      </button>
    </span>
  );
}

// ============================== Architecture workspace ==============================
//
// M2.2 mock review：解析 M2.1 真實 claude-cli E2E 輸出（含 tier line / Mermaid / sections），
// 直接渲染 markdown 結構。M2.3 會把 markdown 來源換成 /api/stage/architecture/{thread}。
function ArchWorkspace() {
  const parsed = useMemo(() => parseArchitecture(MOCK_ARCH_MARKDOWN), []);
  const [view, setView] = useState<"document" | "diagram">("document");
  const [activeDiagram, setActiveDiagram] = useState(0);

  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="architecture" stage="design" op="generate_architecture" right={
          <>
            <ViewToggle value={view} onChange={setView} />
            <DraftPill />
          </>
        } />
        <div className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)] text-[var(--ink)]">
          {view === "document" ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ArchDocument parsed={parsed} />
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="flex items-center justify-between border-b border-[var(--rule)] px-6 py-2.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[#7a8499]">
                <span>{`// architecture · ${parsed.mermaids.length} diagram${parsed.mermaids.length === 1 ? "" : "s"}`}</span>
                {parsed.mermaids.length > 1 && (
                  <div className="flex items-center gap-1">
                    {parsed.mermaids.map((_, i) => (
                      <button
                        key={i}
                        onClick={() => setActiveDiagram(i)}
                        className={`border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] transition ${
                          i === activeDiagram
                            ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_18%,transparent)] text-[var(--polaris)]"
                            : "border-[var(--rule-dark)] bg-transparent text-[var(--ink-muted)] hover:text-[#cdd4df]"
                        }`}
                      >
                        diagram {i + 1}
                      </button>
                    ))}
                  </div>
                )}
                <span>tier <span className="text-[var(--polaris)]">{parsed.tier ?? "—"}</span></span>
              </div>
              <div className="relative min-h-0 flex-1 overflow-auto p-6">
                {parsed.mermaids.length === 0 ? (
                  <div className="grid h-full place-items-center font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                    no mermaid diagrams in this architecture
                  </div>
                ) : (
                  <MermaidDiagram code={parsed.mermaids[activeDiagram]} idPrefix={`arch-${activeDiagram}`} />
                )}
              </div>
            </div>
          )}
        </div>
        <BottomMeta
          left={
            <>
              tier <span className="text-[var(--polaris)]">{parsed.tier ?? "—"}</span> ·{" "}
              {parsed.sections.length} sections · {parsed.mermaids.length} mermaid ·{" "}
              {parsed.raw.length} chars · charted by software_architect
            </>
          }
          right={<>depends_on <span className="text-[#b8c0cf]">prd</span> · downstream <span className="text-[#b8c0cf]">stories</span></>}
        />
        <OperationsRow primaryLabel="核准架構" />
      </section>
      <ChatPanel />
    </div>
  );
}

// ---- Architecture renderers ----
function ArchDocument({ parsed }: { parsed: ReturnType<typeof parseArchitecture> }) {
  return (
    <div className="mx-auto max-w-none px-10 py-10">
      <header className="mb-9 border-b border-[var(--rule)] pb-5">
        <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          SYSTEM ARCHITECTURE · V0.3 (DRAFT)
        </div>
        <div className="mt-3 flex flex-wrap items-baseline gap-3">
          <TierBadge tier={parsed.tier} />
          {parsed.tierJustification && (
            <p className="font-[family-name:var(--font-sans)] text-[14px] leading-[1.6] text-[#cdd4df]">
              {parsed.tierJustification}
            </p>
          )}
        </div>
        <div className="mt-3 flex items-center gap-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          <span>charted by software_architect</span>
          <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
          <span>2 min ago</span>
          <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
          <span>{parsed.sections.length} sections · {parsed.mermaids.length} mermaid</span>
        </div>
      </header>
      <div className="space-y-9">
        {parsed.sections.map((sec, i) => (
          <ArchSectionView key={sec.id + i} heading={sec.heading} body={sec.body} num={String(i + 1)} />
        ))}
      </div>
      {parsed.mermaids.length > 0 && (
        <section className="mt-10 border-t border-[var(--rule)] pt-6">
          <h2 className="mb-3 flex items-baseline gap-3 font-[family-name:var(--font-display)] text-[19px] font-semibold leading-none text-[#e6ecf5]">
            <span className="font-[family-name:var(--font-mono)] text-[12px] font-normal tracking-[0.2em] text-[var(--polaris)]">
              §diagrams
            </span>
            Architecture Diagrams
          </h2>
          <div className="space-y-6">
            {parsed.mermaids.map((code, i) => (
              <div key={i} className="border border-[var(--rule)] bg-[var(--bg)] p-4">
                <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                  diagram {i + 1}
                </div>
                <MermaidDiagram code={code} idPrefix={`arch-doc-${i}`} />
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function TierBadge({ tier }: { tier: "T0" | "T1" | "T2" | null }) {
  if (!tier) {
    return (
      <span className="inline-flex items-center gap-1 border border-dashed border-[var(--rule-dark)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-wider text-[var(--ink-muted)]">
        tier · unset
      </span>
    );
  }
  const accent =
    tier === "T0" ? "var(--ink-muted)"
    : tier === "T1" ? "var(--approved)"
    : "var(--polaris)";
  return (
    <span
      className="inline-flex items-baseline gap-2 border-2 px-3 py-1 font-[family-name:var(--font-display)] font-semibold tracking-tight"
      style={{ borderColor: accent, color: accent }}
    >
      <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em]">tier</span>
      <span className="text-[20px] leading-none">{tier}</span>
    </span>
  );
}

function ArchSectionView({ heading, body, num }: { heading: string; body: string; num: string }) {
  // Body 可能含表格、code block、list；用 MarkdownBlock 渲染。
  return (
    <section>
      <h2 className="mb-4 flex items-baseline gap-3 font-[family-name:var(--font-display)] text-[19px] font-semibold leading-none text-[#e6ecf5]">
        <span className="font-[family-name:var(--font-mono)] text-[12px] font-normal tracking-[0.2em] text-[var(--polaris)]">
          §{num}
        </span>
        {heading}
      </h2>
      <MarkdownBlock text={body} />
    </section>
  );
}

// 輕量 markdown 渲染：表格、code fence、unordered list、paragraph。
// 不引 react-markdown 避免 bundle 變大；對 LLM 生成的內容夠用。
function MarkdownBlock({ text }: { text: string }) {
  const blocks = useMemo(() => splitMarkdownBlocks(text), [text]);
  return (
    <div className="space-y-4 font-[family-name:var(--font-sans)] text-[14px] leading-[1.7] text-[#cdd4df]">
      {blocks.map((b, i) => {
        if (b.kind === "code") {
          return (
            <pre
              key={i}
              className="overflow-x-auto border border-[var(--rule)] bg-[var(--bg)] px-3 py-2.5 font-[family-name:var(--font-mono)] text-[12px] leading-[1.6] text-[#cdd4df]"
            >
              <code>{b.body}</code>
            </pre>
          );
        }
        if (b.kind === "table") {
          return <MarkdownTable key={i} src={b.body} />;
        }
        if (b.kind === "list") {
          return (
            <ul key={i} className="space-y-1.5">
              {b.items.map((item, j) => (
                <li key={j} className="flex items-start gap-2.5">
                  <span className="mt-[10px] inline-block h-1 w-1 shrink-0 rounded-full bg-[var(--polaris)]" />
                  <span dangerouslySetInnerHTML={{ __html: renderInline(item) }} />
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i} dangerouslySetInnerHTML={{ __html: renderInline(b.body) }} />
        );
      })}
    </div>
  );
}

type MdBlock =
  | { kind: "paragraph"; body: string }
  | { kind: "code"; body: string; lang?: string }
  | { kind: "list"; items: string[] }
  | { kind: "table"; body: string };

function splitMarkdownBlocks(md: string): MdBlock[] {
  const lines = md.split(/\r?\n/);
  const out: MdBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    // code fence
    const fence = /^```(\w*)\s*$/.exec(ln);
    if (fence) {
      const lang = fence[1];
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // skip closing fence
      out.push({ kind: "code", body: buf.join("\n"), lang });
      continue;
    }
    // table（含 |、下一行是 |---）
    if (ln.includes("|") && i + 1 < lines.length && /^\s*\|?\s*[-:|\s]+\|/.test(lines[i + 1])) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].includes("|")) { buf.push(lines[i]); i++; }
      out.push({ kind: "table", body: buf.join("\n") });
      continue;
    }
    // list
    if (/^\s*[-*]\s+/.test(ln)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      out.push({ kind: "list", items });
      continue;
    }
    // paragraph：到下一個空行或結尾
    const pbuf: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^```/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])) {
      pbuf.push(lines[i]); i++;
    }
    if (pbuf.length > 0) out.push({ kind: "paragraph", body: pbuf.join(" ") });
    // skip empty line(s)
    while (i < lines.length && lines[i].trim() === "") i++;
  }
  return out;
}

function MarkdownTable({ src }: { src: string }) {
  const lines = src.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return null;
  const split = (l: string) => l.split("|").map((c) => c.trim()).filter((_, i, arr) => i !== 0 || arr[0] !== "").filter((_, i, arr) => i !== arr.length - 1 || arr[arr.length - 1] !== "");
  const header = split(lines[0]);
  const rows = lines.slice(2).map(split);
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i} className="border-b-2 border-[var(--polaris-dim)] bg-[var(--bg)] px-3 py-2 text-left font-[family-name:var(--font-display)] font-semibold text-[#e6ecf5]" dangerouslySetInnerHTML={{ __html: renderInline(h) }} />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={i % 2 === 1 ? "bg-[var(--bg)]/30" : ""}>
              {r.map((c, j) => (
                <td key={j} className="border-b border-[var(--rule)] px-3 py-2 align-top text-[#cdd4df]" dangerouslySetInnerHTML={{ __html: renderInline(c) }} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// inline：bold / inline-code / requirement chip
function renderInline(s: string): string {
  // escape HTML
  let out = s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  // requirement chip：FR-N / NFR-N / OPS-N
  out = out.replace(
    /(?:`)?(FR-\d+|NFR-\d+|OPS-\d+|AC-\d+)(?:`)?/g,
    '<code class="inline-flex items-center border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[11px] text-[var(--polaris)]">$1</code>',
  );
  // bold
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-[#e6ecf5]">$1</strong>');
  // inline code
  out = out.replace(/`([^`]+)`/g, '<code class="border border-[var(--paper-edge)] bg-[var(--bg)] px-1 py-0.5 font-[family-name:var(--font-mono)] text-[12px] text-[#cdd4df]">$1</code>');
  return out;
}

function ViewToggle({ value, onChange }: { value: "document" | "diagram"; onChange: (v: "document" | "diagram") => void }) {
  return (
    <div className="flex border border-[var(--rule-dark)] bg-[var(--bg-elev)]">
      {(["document", "diagram"] as const).map((v) => (
        <button key={v} onClick={() => onChange(v)}
          className={`px-3 py-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] transition ${
            v === value ? "bg-[var(--polaris)] text-white" : "text-[var(--ink-muted)] hover:text-[#b8c0cf]"
          }`}>
          {v}
        </button>
      ))}
    </div>
  );
}

// ============================== Stories workspace ==============================
//
// M2.2 mock review：解析 M2.1 E2E 真實 stories markdown（10 epics / 44 stories）。
// M2.3 wire API 後改吃 /api/stage/stories/{thread}。
function StoriesWorkspace() {
  const parsed = useMemo(() => parseStories(MOCK_STORIES_MARKDOWN), []);
  const counts = useMemo(() => countStoriesAndEstimate(parsed.raw), [parsed.raw]);
  const allStories = useMemo(() => parsed.epics.flatMap((e) => e.stories.map((s) => ({ epicNum: e.num, story: s }))), [parsed.epics]);
  const initialPick = allStories[0]?.story.num ?? null;
  const [picked, setPicked] = useState<string | null>(initialPick);
  const [openEpics, setOpenEpics] = useState<Set<string>>(() => new Set(parsed.epics.slice(0, 3).map((e) => e.num)));

  const toggleEpic = (n: string) => setOpenEpics((prev) => {
    const next = new Set(prev);
    if (next.has(n)) next.delete(n); else next.add(n);
    return next;
  });

  const detail = picked
    ? allStories.find(({ story }) => story.num === picked)?.story ?? null
    : null;

  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="stories" stage="deliver" op="generate_user_stories" right={
          <>
            <DraftPill />
            <button className="border border-[var(--rule-dark)] bg-[var(--bg-elev)] px-3 py-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">
              Preview to GitHub
            </button>
          </>
        } />
        <div className="shadow-anvil paper-texture min-h-0 flex-1 overflow-y-auto bg-[var(--paper)] px-8 py-7">
          <div className="mb-7 border-b border-[var(--rule)] pb-5">
            <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              DELIVERABLE STORIES · V1
            </div>
            <h2 className="mt-2 font-[family-name:var(--font-display)] text-[26px] font-semibold leading-tight text-[#e6ecf5]">
              {parsed.title ?? "User Stories"}
            </h2>
            <div className="mt-3 flex flex-wrap items-center gap-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              <span>charted by product_owner</span>
              <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
              <span>{counts.epics} epics · {counts.stories} stories · {counts.hours.toFixed(1)} hrs</span>
              {parsed.milestones.length > 0 && (
                <>
                  <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
                  <span>{parsed.milestones.length} milestones</span>
                </>
              )}
            </div>
            {parsed.milestones.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {parsed.milestones.map((m) => (
                  <span key={m.num} className="border border-[var(--polaris-dim)] bg-[color-mix(in_oklab,var(--polaris)_8%,transparent)] px-2.5 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--polaris)]">
                    M{m.num} · {m.title}
                  </span>
                ))}
              </div>
            )}
          </div>
          {parsed.epics.map((epic) => {
            const epicHours = epic.stories.reduce((acc, s) => acc + (parseFloat(s.estimate ?? "0") || 0), 0);
            const isOpen = openEpics.has(epic.num);
            return (
              <div key={epic.num} className="mb-7 last:mb-0">
                <button
                  onClick={() => toggleEpic(epic.num)}
                  className="group mb-3 flex w-full items-baseline gap-3 text-left"
                >
                  <span className={`grid h-5 w-5 shrink-0 place-items-center border font-[family-name:var(--font-mono)] text-[10px] transition ${
                    isOpen ? "border-[var(--polaris)] bg-[var(--polaris)] text-white" : "border-[var(--rule-dark)] text-[var(--ink-muted)]"
                  }`}>
                    {isOpen ? "−" : "+"}
                  </span>
                  <span className="font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--polaris)]">
                    Epic {epic.num}
                  </span>
                  <span className="font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">
                    {epic.title}
                  </span>
                  <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                    {epic.stories.length} stories · {epicHours.toFixed(1)} hrs
                  </span>
                  <span className="h-px flex-1 bg-[var(--rule)]" />
                </button>
                {isOpen && (
                  <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                    {epic.stories.map((s) => (
                      <button
                        key={s.num}
                        onClick={() => setPicked(s.num)}
                        className={`flex flex-col items-stretch border bg-[var(--bg-elev)] p-4 text-left transition ${
                          picked === s.num
                            ? "border-[var(--polaris)] glow-star"
                            : "border-[var(--paper-edge)] hover:border-[#4a5468]"
                        }`}
                      >
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <code className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">
                            Story {s.num}
                          </code>
                          {s.estimate && (
                            <span className="border border-[var(--rule-dark)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[#cdd4df]">
                              {s.estimate}h
                            </span>
                          )}
                        </div>
                        <div className="mb-2 font-[family-name:var(--font-display)] text-[15px] font-semibold leading-snug text-[#e6ecf5]">
                          {s.title}
                        </div>
                        {s.iWant && (
                          <p className="mb-3 line-clamp-2 font-[family-name:var(--font-sans)] text-[12px] leading-[1.55] text-[var(--ink-muted)]">
                            {s.iWant}
                          </p>
                        )}
                        <div className="mt-auto flex flex-wrap items-center gap-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                          {s.ac.length > 0 && <span>{s.ac.length} AC</span>}
                          {s.requirements.length > 0 && (
                            <>
                              <span>·</span>
                              {s.requirements.map((r) => (
                                <code key={r} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 text-[var(--polaris)]">
                                  {r}
                                </code>
                              ))}
                            </>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <BottomMeta
          left={`${counts.stories} stories · ${counts.epics} epics · ${counts.hours.toFixed(1)} hrs · ${parsed.raw.length} chars`}
          right={<>depends_on <span className="text-[#b8c0cf]">architecture</span> · publish to <span className="text-[#b8c0cf]">github / jira</span></>}
        />
        <div className="mt-4 flex items-center justify-end gap-2">
          <ToolBtn>Refine…</ToolBtn>
          <ToolBtn>手動編輯</ToolBtn>
          <ToolBtn primary>發佈到 GitHub</ToolBtn>
        </div>
      </section>
      <StoryDetail story={detail} epicNum={picked ? allStories.find(({ story }) => story.num === picked)?.epicNum ?? null : null} />
    </div>
  );
}

function StoryDetail({ story, epicNum }: { story: ParsedStory | null; epicNum: string | null }) {
  return (
    <aside className="rise-4 flex w-[420px] shrink-0 flex-col border-l border-[var(--rule-dark)] bg-[var(--bg-elev)]/40">
      <div className="flex items-center justify-between border-b border-[var(--rule-dark)] px-6 py-4">
        <div className="flex items-baseline gap-3">
          <h3 className="font-[family-name:var(--font-display)] text-[17px] font-semibold text-[#e6ecf5]">Story Detail</h3>
          {story && (
            <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--polaris)]">
              Story {story.num}
            </span>
          )}
        </div>
        <span className="grid h-7 w-7 place-items-center border border-[var(--rule-dark)] font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">⋯</span>
      </div>
      {story ? (
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          <div>
            <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">TITLE</div>
            <div className="mt-1 font-[family-name:var(--font-display)] text-[19px] font-semibold leading-tight text-[#e6ecf5]">{story.title}</div>
          </div>
          <div className="grid grid-cols-2 gap-3 border-y border-[var(--rule-dark)] py-3 text-[12px]">
            <KV k="ESTIMATE" v={story.estimate ? `${story.estimate} hrs` : "—"} />
            <KV k="EPIC" v={epicNum ? `Epic ${epicNum}` : "—"} />
            <KV k="DEPENDS ON" v={story.dependsOn || "—"} />
            <KV k="AC COUNT" v={`${story.ac.length}`} />
          </div>
          {(story.asA || story.iWant || story.soThat) && (
            <div className="border-l-2 border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_5%,transparent)] py-2 pl-4 pr-3 font-[family-name:var(--font-sans)] text-[13px] leading-[1.7] text-[#cdd4df]">
              {story.asA && <><strong className="text-[#e6ecf5]">As a</strong> {story.asA}</>}
              {story.iWant && <><br /><strong className="text-[#e6ecf5]">I want</strong> {story.iWant}</>}
              {story.soThat && <><br /><strong className="text-[#e6ecf5]">so that</strong> {story.soThat}</>}
            </div>
          )}
          {story.requirements.length > 0 && (
            <div>
              <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">REQUIREMENT IDS</div>
              <div className="flex flex-wrap gap-1.5">
                {story.requirements.map((r) => (
                  <code key={r} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-1 font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">
                    {r}
                  </code>
                ))}
              </div>
            </div>
          )}
          {story.ac.length > 0 && (
            <div>
              <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">ACCEPTANCE CRITERIA</div>
              <ul className="space-y-2.5">
                {story.ac.map((a, i) => (
                  <li key={i} className="flex items-start gap-2.5 text-[12.5px] leading-[1.6] text-[#cdd4df]">
                    <span className="mt-0.5 inline-flex shrink-0 items-center border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[var(--polaris)]">
                      {a.code ?? `AC${i + 1}`}
                    </span>
                    <span dangerouslySetInnerHTML={{ __html: renderInline(a.text) }} />
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="border-t border-[var(--rule-dark)] pt-4">
            <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">DELIVERY TARGET</div>
            <div className="flex flex-wrap gap-1.5">
              <span className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[#cdd4df]">github · acme/checkout</span>
              <span className="border border-dashed border-[var(--rule-dark)] px-2 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">+ jira</span>
            </div>
          </div>
        </div>
      ) : (
        <div className="grid flex-1 place-items-center px-6 text-center text-[12px] text-[var(--ink-muted)]">選一張 story 看詳情</div>
      )}
    </aside>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">{k}</div>
      <div className="mt-0.5 text-[13px] text-[#e6ecf5]">{v}</div>
    </div>
  );
}

// ============================== Implementation workspace（M5 preview · dispatch mode）==============================
function ImplementWorkspace() {
  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="implement" stage="deliver" op="auto_implement" right={
          <>
            <Pill color="chart">DISPATCH MODE</Pill>
            <Pill color="muted">M5 PREVIEW</Pill>
          </>
        } />
        <div className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)]">
          {/* Lead 分派指令 */}
          <div className="border-b border-[var(--rule)] px-6 py-5">
            <div className="mb-3 flex items-center gap-2">
              <span className="grid h-5 w-5 place-items-center bg-[var(--polaris)] font-[family-name:var(--font-display)] text-[10px] font-bold text-white">IL</span>
              <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                implementation_lead<span className="ml-1 text-[var(--polaris)]">· LEAD</span>
              </span>
              <span className="ml-auto font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                processing · US-2 · 信用卡 + 3DS 流程
              </span>
            </div>
            <div className="mb-4 text-[13px] leading-6 text-[#cdd4df]">
              拆分為前端 / 後端任務並分派給 subagents：
            </div>
            <div className="grid grid-cols-2 gap-3">
              <DispatchTaskCard to="frontend_engineer" abbr="FE" color="#a78bfa" tasks={["結帳表單 UI（含 Apple Pay 按鈕）", "3DS challenge modal", "錯誤訊息 i18n"]} />
              <DispatchTaskCard to="backend_engineer"  abbr="BE" color="#5b8cff" tasks={["Stripe tokenization endpoint", "3DS callback signature 驗證", "pytest 18 cases"]} />
            </div>
          </div>
          {/* Subagent 並排 panes（streaming log） */}
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-px bg-[var(--rule)]">
            <SubagentPane abbr="FE" color="#a78bfa" name="frontend_engineer" status="running" stats={{ commits: "14", files: "8", pr: "#142 draft" }} logs={[
              { t: "ok",  msg: "scaffolded checkout/Page.tsx" },
              { t: "ok",  msg: "added 3DSChallengeModal component" },
              { t: "ok",  msg: "wired Stripe.js client" },
              { t: "run", msg: "running typecheck..." },
              { t: "run", msg: "test: checkout flow happy path" },
            ]} />
            <SubagentPane abbr="BE" color="#5b8cff" name="backend_engineer" status="running" stats={{ commits: "22", files: "12", pr: "#143 merged" }} logs={[
              { t: "ok",  msg: "tokenization handler" },
              { t: "ok",  msg: "Stripe customer + intent api" },
              { t: "ok",  msg: "3DS callback verified signature" },
              { t: "ok",  msg: "pytest 18 passed" },
              { t: "run", msg: "deploying preview environment..." },
            ]} />
          </div>
        </div>
        <BottomMeta left={<>3 agents · 1 lead + 2 subagents · dispatch · processing <span className="text-[var(--polaris)]">1/8 stories</span></>} right={<>merging into <code className="text-[#cdd4df]">us-2-impl</code> · sha · pending</>} />
        <div className="mt-4 flex items-center justify-end gap-2">
          <ToolBtn>暫停</ToolBtn>
          <ToolBtn>取消</ToolBtn>
          <ToolBtn primary>合併 PR</ToolBtn>
        </div>
      </section>
      <ChatPanel />
    </div>
  );
}

function DispatchTaskCard({ to, abbr, color, tasks }: { to: string; abbr: string; color: string; tasks: string[] }) {
  return (
    <div className="border border-[var(--paper-edge)] bg-[var(--bg)] p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="grid h-4 w-4 place-items-center font-[family-name:var(--font-display)] text-[9px] font-bold text-white" style={{ backgroundColor: color }}>{abbr}</span>
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">↳ to {to}</span>
        <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em]" style={{ color }}>· SUBAGENT</span>
      </div>
      <ul className="space-y-1">
        {tasks.map((t) => (
          <li key={t} className="flex items-start gap-2 text-[12px] text-[#cdd4df]">
            <span className="mt-1.5 inline-block h-1 w-1 shrink-0 rounded-full bg-[var(--ink-muted)]" />
            <span>{t}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SubagentPane({ abbr, color, name, status, stats, logs }: {
  abbr: string; color: string; name: string; status: string;
  stats: Record<string, string>; logs: { t: "ok" | "run" | "warn"; msg: string }[];
}) {
  return (
    <div className="flex flex-col bg-[var(--paper)] p-5">
      <div className="mb-3 flex items-center justify-between border-b border-[var(--rule)] pb-3">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center font-[family-name:var(--font-display)] text-[11px] font-bold text-white" style={{ backgroundColor: color }}>{abbr}</span>
          <div className="leading-tight">
            <div className="font-[family-name:var(--font-display)] text-[13px] font-semibold text-[#e6ecf5]">{name}</div>
            <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em]" style={{ color }}>· SUBAGENT</span>
          </div>
        </div>
        <span className="flex items-center gap-1.5 border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider"
          style={{ color, borderColor: `color-mix(in oklab, ${color} 40%, transparent)` }}>
          <span className="pulse-star inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
          {status}
        </span>
      </div>
      <div className="mb-3 grid grid-cols-3 gap-2">
        {Object.entries(stats).map(([k, v]) => (
          <div key={k}>
            <div className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">{k}</div>
            <div className="text-[13px] text-[#cdd4df]">{v}</div>
          </div>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        <div className="mb-2 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">log · streaming</div>
        <ul className="space-y-1 font-[family-name:var(--font-mono)] text-[11px] leading-5">
          {logs.map((l, i) => (
            <li key={i} className={l.t === "ok" ? "text-[var(--approved)]" : l.t === "run" ? "text-[var(--polaris)]" : "text-[var(--ink-muted)]"}>
              <span className="opacity-50">[{String(i + 1).padStart(2, "0")}]</span> {l.t === "ok" ? "✓" : l.t === "run" ? "▸" : "·"} {l.msg}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ============================== Workflows view ==============================
function WorkflowsView() {
  const [picked, setPicked] = useState<string>("default");
  const wf = WORKFLOWS.find((w) => w.id === picked)!;
  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Workflows" sub="表單式編輯：有序 stage 清單 + 依賴推導（無 DAG canvas）" right={
        <button className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-white transition hover:bg-[var(--polaris-hi)]">
          ＋ new workflow
        </button>
      } />
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[420px] shrink-0 flex-col border-r border-[var(--rule-dark)] overflow-y-auto p-6 space-y-3">
          {WORKFLOWS.map((w) => {
            const active = w.id === picked;
            return (
              <button key={w.id} onClick={() => setPicked(w.id)}
                className={`flex flex-col items-stretch border p-4 text-left transition ${
                  active ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_6%,transparent)]" : "border-[var(--rule-dark)] bg-[var(--bg-elev)]/40 hover:border-[#4a5468]"
                }`}>
                <div className="mb-1 flex items-center justify-between">
                  <code className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{w.id}</code>
                  {w.builtin ? <Pill color="approved">BUILTIN</Pill> : <Pill color="muted">USER</Pill>}
                </div>
                <div className="font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">{w.label}</div>
                <div className="mt-1 text-[12px] text-[#97a0b3]">{w.desc}</div>
                <div className="mt-3 flex items-center gap-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  {w.stages.map((s, i) => (
                    <span key={s} className="flex items-center gap-1.5">
                      <span className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 text-[#cdd4df]">{s}</span>
                      {i < w.stages.length - 1 && <span className="text-[var(--ink-muted)]">→</span>}
                    </span>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between border-t border-[var(--rule-dark)] pt-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  <span>{w.threads} thread{w.threads === 1 ? "" : "s"} using</span>
                  <span>source · <span className="text-[#cdd4df]">{w.source}</span></span>
                </div>
              </button>
            );
          })}
        </div>
        <div className="flex min-w-0 flex-1 flex-col overflow-y-auto p-8">
          <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">EDITING · {wf.id}</div>
          <h2 className="font-[family-name:var(--font-display)] text-[24px] font-semibold leading-tight text-[#e6ecf5]">{wf.label}</h2>
          <p className="mt-1 text-[13px] text-[#97a0b3]">{wf.desc}</p>
          <div className="mt-7">
            <SectionLabel>STAGES（拖曳重排 / 上下移）</SectionLabel>
            <ol className="space-y-2">
              {wf.stages.map((s, i) => (
                <li key={s} className="flex flex-col gap-3 border border-[var(--rule-dark)] bg-[var(--bg-elev)]/60 p-3">
                  <div className="flex items-center gap-3">
                    <span className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--ink-muted)]">{String(i + 1).padStart(2, "0")}</span>
                    <code className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{s}</code>
                    <span className="text-[13px] text-[#cdd4df]">{STAGES.find((x) => x.id === s)?.label ?? s}</span>
                    <span className="ml-auto flex items-center gap-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                      <span>collab</span>
                      <CollabModePill mode={wf.collab_mode[s] ?? "single"} />
                    </span>
                    <span className="flex items-center gap-2 border-l border-[var(--rule-dark)] pl-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                      <span>depends_on</span>
                      {wf.stages.slice(0, i).length === 0 ? <span className="text-[#5e6878]">(root)</span> : wf.stages.slice(0, i).map((d) => (
                        <code key={d} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 text-[#cdd4df]">{d}</code>
                      ))}
                    </span>
                    <button title="上移" className="grid h-6 w-6 place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">↑</button>
                    <button title="下移" className="grid h-6 w-6 place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">↓</button>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 pl-8">
                    <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">agents</span>
                    {(wf.agent_bindings[s] ?? []).map((b) => <AgentBindingChip key={b.agent_id} binding={b} />)}
                    <button className="border border-dashed border-[var(--rule-dark)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">＋ add</button>
                  </div>
                </li>
              ))}
            </ol>
            <button className="mt-3 flex w-full items-center justify-center gap-2 border border-dashed border-[var(--rule-dark)] py-2.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">
              ＋ add stage
            </button>
          </div>
          <div className="mt-8">
            <SectionLabel>STEPPER PREVIEW</SectionLabel>
            <ol className="flex gap-2">
              {wf.stages.map((s, i) => (
                <li key={s} className="flex items-center gap-2">
                  <div className="border border-[var(--rule-dark)] bg-[var(--bg-elev)]/40 px-3 py-2">
                    <div className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">{String(i + 1).padStart(2, "0")}</div>
                    <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{STAGES.find((x) => x.id === s)?.label}</div>
                  </div>
                  {i < wf.stages.length - 1 && <span className="text-[var(--rule-dark)]">→</span>}
                </li>
              ))}
            </ol>
          </div>
          <div className="mt-8 flex justify-end gap-2">
            <ToolBtn>取消</ToolBtn>
            <ToolBtn primary>儲存 workflow</ToolBtn>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================== Agents view ==============================
function AgentsView() {
  const stagesInOrder = Array.from(new Set(AGENTS.map((a) => a.stage)));
  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Agents" sub="完整客製化 AI agent · 多 agent 同 stage（lead / peer / subagent）" right={
        <button className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-white transition hover:bg-[var(--polaris-hi)]">
          ＋ new agent
        </button>
      } />
      <div className="min-h-0 flex-1 space-y-7 overflow-y-auto p-8">
        {stagesInOrder.map((stage) => {
          const agentsInStage = AGENTS.filter((a) => a.stage === stage);
          const mode = WORKFLOWS[0].collab_mode[stage] ?? "single";
          return (
            <div key={stage}>
              <div className="mb-3 flex items-baseline gap-3 border-b border-[var(--rule-dark)] pb-2">
                <span className="font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--polaris)]">stage · {stage}</span>
                <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{agentsInStage.length} agents</span>
                <CollabModePill mode={mode} />
              </div>
              <div className="grid grid-cols-2 gap-4">
                {agentsInStage.map((a) => <AgentCard key={a.id} a={a} />)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgentCard({ a }: { a: typeof AGENTS[number] }) {
  const roleColor = ROLE_COLORS[a.collab];
  return (
    <div className={`flex flex-col border bg-[var(--bg-elev)]/40 p-5 ${a.enabled ? "border-[var(--rule-dark)]" : "border-[var(--rule-dark)] opacity-60"}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center border bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] font-[family-name:var(--font-display)] text-[14px] font-bold" style={{ borderColor: roleColor, color: roleColor }}>
            {a.name.split(" ").map((w) => w[0]).join("")}
          </span>
          <div className="leading-tight">
            <div className="font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">{a.name}</div>
            <code className="mt-1 inline-block font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[var(--ink-muted)]">{a.id}</code>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em]" style={{ color: roleColor, borderColor: `color-mix(in oklab, ${roleColor} 40%, transparent)` }}>
            {a.collab}
          </span>
          {a.enabled ? <Pill color="approved">ENABLED</Pill> : <Pill color="muted">DISABLED</Pill>}
        </div>
      </div>
      {a.subagentOf && (
        <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
          subagent_of · <span className="text-[var(--polaris)]">{a.subagentOf}</span>
        </div>
      )}
      <div className="mb-3 grid grid-cols-3 gap-3 border-y border-[var(--rule-dark)] py-3">
        <KV k="MODEL" v={a.model} />
        <KV k="MAX ITER" v={String(a.iter)} />
        <KV k="SKILLS" v={String(a.skills.length)} />
      </div>
      <div className="mb-3">
        <SectionLabel>SYSTEM PROMPT</SectionLabel>
        <div className="line-clamp-2 font-[family-name:var(--font-mono)] text-[12px] leading-5 text-[#cdd4df]">{a.prompt}</div>
      </div>
      <div className="mb-3">
        <SectionLabel>SKILLS</SectionLabel>
        <div className="flex flex-wrap gap-1.5">
          {a.skills.map((s) => <span key={s} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[#cdd4df]">{s}</span>)}
        </div>
      </div>
      {a.tools.length > 0 && (
        <div className="mb-3">
          <SectionLabel>TOOLS</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {a.tools.map((t) => <span key={t} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--polaris)]">{t}</span>)}
          </div>
        </div>
      )}
      <div className="mt-auto flex items-center justify-between border-t border-[var(--rule-dark)] pt-3">
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">seed · {a.seed}</span>
        <div className="flex gap-2">
          <ToolBtn>編輯</ToolBtn>
          <ToolBtn>{a.enabled ? "停用" : "啟用"}</ToolBtn>
        </div>
      </div>
    </div>
  );
}

// ============================== Plugins view ==============================
function PluginsView() {
  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Plugins" sub="所有功能都是 plugin —— 內建與第三方走同一套 API" right={
        <div className="flex items-center gap-3 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          <span>installed · {PLUGINS.length}</span>
          <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
          <span className="text-[var(--approved)]">{PLUGINS.filter((p) => p.enabled).length} loaded</span>
        </div>
      } />
      <div className="min-h-0 flex-1 overflow-y-auto p-8">
        <div className="grid grid-cols-2 gap-4">
          {PLUGINS.map((p) => (
            <div key={p.id} className={`flex flex-col border p-5 ${p.enabled ? "border-[var(--rule-dark)] bg-[var(--bg-elev)]/40" : "border-[var(--rule-dark)] bg-[var(--bg-elev)]/20"}`}>
              <div className="mb-3 flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <code className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{p.id}</code>
                    {p.builtin && <Pill color="chart">BUILTIN</Pill>}
                  </div>
                  <h3 className="mt-1 font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]">{p.name}</h3>
                  <div className="mt-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">version · <span className="text-[#cdd4df]">{p.version}</span></div>
                </div>
                <label className="relative inline-flex cursor-pointer items-center gap-2">
                  <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{p.enabled ? "enabled" : "disabled"}</span>
                  <span className={`relative inline-block h-4 w-7 transition ${p.enabled ? "bg-[var(--polaris)]" : "bg-[var(--rule-dark)]"}`}>
                    <span className={`absolute top-0.5 h-3 w-3 bg-white transition ${p.enabled ? "left-3" : "left-0.5"}`} />
                  </span>
                </label>
              </div>
              <p className="mb-4 text-[13px] leading-6 text-[#97a0b3]">{p.desc}</p>
              <div className="mb-4 space-y-2">
                <SectionLabel>PROVIDES</SectionLabel>
                <div className="flex flex-col gap-1.5">
                  {(["stages", "workflows", "agents", "integrations"] as const).map((cat) =>
                    p.provides[cat].length > 0 ? (
                      <div key={cat} className="flex items-baseline gap-2">
                        <span className="w-24 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{cat}</span>
                        <div className="flex flex-wrap gap-1">
                          {p.provides[cat].map((x) => (
                            <code key={x} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[#cdd4df]">{x}</code>
                          ))}
                        </div>
                      </div>
                    ) : null
                  )}
                </div>
              </div>
              {p.loadError && (
                <div className="mb-3 border-l-2 border-[var(--locked)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  load_error · {p.loadError}
                </div>
              )}
              <div className="mt-auto flex items-center justify-between border-t border-[var(--rule-dark)] pt-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                <span>host_api · &gt;=1.0,&lt;2.0</span>
                <button className="border border-[var(--rule-dark)] px-3 py-1 text-[#cdd4df] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">manifest</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ============================== Chat panel ==============================
function ChatPanel() {
  return (
    <section className="rise-4 flex w-[400px] shrink-0 flex-col border-l border-[var(--rule-dark)] bg-[var(--bg-elev)]/40">
      <div className="border-b border-[var(--rule-dark)] px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-3">
            <h3 className="font-[family-name:var(--font-display)] text-[17px] font-semibold text-[#e6ecf5]">PRD Discussion</h3>
            <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">3 agents · {CHAT.length} msgs</span>
          </div>
          <span className="grid h-7 w-7 place-items-center border border-[var(--rule-dark)] font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">⋯</span>
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          {(Object.keys(SPEAKER_STYLES) as SpeakerId[]).map((id) => {
            const sp = SPEAKER_STYLES[id];
            return (
              <div key={id} className="flex items-center gap-1.5 border border-[var(--rule-dark)] py-0.5 pl-0.5 pr-2">
                <span className="grid h-4 w-4 place-items-center font-[family-name:var(--font-display)] text-[9px] font-bold text-white" style={{ backgroundColor: sp.color }}>{sp.abbr}</span>
                <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">{id}</span>
                <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em]" style={{ color: sp.color }}>· {sp.tier}</span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-5">
        {CHAT.map((m, i) => <ChatMessage key={i} m={m} />)}
      </div>
      <div className="border-t border-[var(--rule-dark)] p-4">
        <div className="flex items-end gap-2 border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2.5 focus-within:border-[var(--polaris)]">
          <textarea rows={2} placeholder="補充需求 / 要求 SA 修正……"
            className="flex-1 resize-none bg-transparent text-[13px] text-[#cdd4df] outline-none placeholder:text-[var(--ink-muted)]" />
          <button className="grid h-7 w-7 shrink-0 place-items-center bg-[var(--polaris)] text-white hover:bg-[var(--polaris-hi)]"><span className="-mt-0.5 text-sm">↵</span></button>
        </div>
        <div className="mt-2 flex items-center justify-between font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
          <span>↵ send · ⌘↵ refine</span>
          <span>tokens 1,234 / 200k</span>
        </div>
      </div>
    </section>
  );
}

function ChatMessage({ m }: { m: ChatMsg }) {
  if (m.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1">
        {m.toAgent && (
          <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
            ↳ to <span style={{ color: SPEAKER_STYLES[m.toAgent].color }}>{m.toAgent}</span>
          </span>
        )}
        <div className="max-w-[88%] border-l-2 border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_12%,transparent)] px-4 py-2.5 text-[13px] leading-6 text-[#e6ecf5]">{m.content}</div>
      </div>
    );
  }
  const sp = SPEAKER_STYLES[m.speaker];
  return (
    <div className="flex flex-col items-start gap-2.5">
      <div className="flex items-center gap-2">
        <span
          className="grid h-5 w-5 place-items-center font-[family-name:var(--font-display)] text-[10px] font-bold text-white"
          style={{ backgroundColor: sp.color }}
        >
          {sp.abbr}
        </span>
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          {m.speaker} <span className="ml-1" style={{ color: sp.color }}>· {sp.tier}</span>
        </span>
      </div>
      <div
        className="max-w-[92%] whitespace-pre-wrap border-l-2 px-3 py-1 text-[13px] leading-6 text-[#cdd4df]"
        style={{ borderColor: `color-mix(in oklab, ${sp.color} 50%, transparent)` }}
      >
        {m.content}
      </div>
      {m.chips && (
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
          {m.chips.map((c, i) => <ReplyChip key={i} label={c.label} selected={c.selected} multi={m.multi} />)}
          {m.multi && <span className="ml-1 self-center font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">· multi-select</span>}
        </div>
      )}
    </div>
  );
}

function ReplyChip({ label, selected, multi }: { label: string; selected?: boolean; multi?: boolean }) {
  return (
    <button className={`flex items-center gap-1.5 border px-3 py-1.5 text-[12px] transition ${
      selected ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_18%,transparent)] text-[var(--polaris)]" : "border-[var(--rule-dark)] bg-transparent text-[#cdd4df] hover:border-[#4a5468] hover:bg-[var(--bg-elev)]"
    }`}>
      {multi ? (
        <span className={`grid h-3 w-3 place-items-center border ${selected ? "border-[var(--polaris)] bg-[var(--polaris)]" : "border-[#2e3441]"}`}>
          {selected && <span className="text-[9px] leading-none text-white">✓</span>}
        </span>
      ) : (
        selected && <span className="text-[8px] leading-none">●</span>
      )}
      <span>{label}</span>
    </button>
  );
}

// ============================== Doc fullscreen ==============================
function DocFullscreen({ onClose, prdArtifact }: { onClose: () => void; prdArtifact: string }) {
  const hasContent = (prdArtifact || "").trim().length > 0;
  return (
    <div className="rise-1 fixed inset-0 z-50 flex flex-col bg-[var(--bg)]/96 backdrop-blur-sm">
      <header className="flex items-center justify-between border-b border-[var(--rule-dark)] bg-[var(--bg-elev)]/60 px-8 py-3">
        <div className="flex items-baseline gap-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          <span>artifact</span><span className="text-[#cdd4df]">prd</span><span>·</span><span>specify · generate_prd</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">esc · 關閉</span>
          <button onClick={onClose} title="關閉" className="grid h-8 w-8 place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]"><CloseIcon /></button>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {hasContent ? (
          <article className="paper-texture shadow-anvil mx-auto my-10 max-w-3xl bg-[var(--paper)]">
            <pre className="whitespace-pre-wrap px-10 py-12 font-[family-name:var(--font-mono)] text-[13px] leading-[1.85] text-[#cdd4df]">
              {prdArtifact}
            </pre>
          </article>
        ) : (
          <div className="grid h-full place-items-center font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            PRD 尚未生成
          </div>
        )}
      </div>
    </div>
  );
}

// ============================== Shared bits ==============================
function ArtifactBar({ artifact, stage, op, right }: { artifact: string; stage: string; op: string; right: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <div className="flex items-baseline gap-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        <span>artifact</span><span className="text-[#cdd4df]">{artifact}</span><span>·</span><span>{stage} · {op}</span>
      </div>
      <div className="flex items-center gap-2">{right}</div>
    </div>
  );
}

function BottomMeta({ left, right }: { left: React.ReactNode; right: React.ReactNode }) {
  return (
    <div className="mt-3 flex items-center justify-between font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
      <span>{left}</span><span>{right}</span>
    </div>
  );
}

function OperationsRow({ approved, primaryLabel }: { approved?: boolean; primaryLabel?: string }) {
  return (
    <div className="mt-4 flex items-center justify-end gap-2">
      <ToolBtn>Refine…</ToolBtn>
      <ToolBtn>手動編輯</ToolBtn>
      <ToolBtn primary>{primaryLabel ?? (approved ? "已核准 ✓" : "核准")}</ToolBtn>
    </div>
  );
}

function ApprovedSeal() {
  return (
    <div className="flex items-center gap-2 border border-[color-mix(in_oklab,var(--approved)_40%,transparent)] bg-[color-mix(in_oklab,var(--approved)_12%,transparent)] px-3 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--approved)]">
      <span className="glow-approved relative inline-block h-1.5 w-1.5 rounded-full bg-[var(--approved)]" />
      charted · approved
    </div>
  );
}

function DraftPill() {
  return (
    <div className="flex items-center gap-2 border border-[color-mix(in_oklab,var(--polaris)_40%,transparent)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] px-3 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--polaris)]">
      <span className="pulse-star relative inline-block h-1.5 w-1.5 rounded-full bg-[var(--polaris)]" />
      charting · draft
    </div>
  );
}

function Pill({ children, color }: { children: React.ReactNode; color: "approved" | "chart" | "muted" }) {
  const cls = color === "approved"
    ? "border-[color-mix(in_oklab,var(--approved)_40%,transparent)] bg-[color-mix(in_oklab,var(--approved)_10%,transparent)] text-[var(--approved)]"
    : color === "chart"
      ? "border-[color-mix(in_oklab,var(--polaris)_40%,transparent)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] text-[var(--polaris)]"
      : "border-[var(--rule-dark)] text-[var(--ink-muted)]";
  return (
    <span className={`border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] ${cls}`}>{children}</span>
  );
}

const ROLE_COLORS: Record<Collab, string> = {
  lead:     "var(--polaris)",  // cobalt
  peer:     "#f59e0b",       // amber
  subagent: "#a78bfa",       // violet
};

function AgentBindingChip({ binding }: { binding: Binding }) {
  const color = ROLE_COLORS[binding.role];
  return (
    <span className="flex items-center gap-1.5 border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5">
      <code className="font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[#cdd4df]">{binding.agent_id}</code>
      <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.18em]" style={{ color }}>{binding.role}</span>
    </span>
  );
}

function CollabModePill({ mode }: { mode: CollabMode }) {
  const styles: Record<CollabMode, { color: string; label: string }> = {
    single:     { color: "var(--ink-muted)", label: "single" },
    discussion: { color: "var(--polaris)",     label: "discussion" },
    dispatch:   { color: "#f59e0b",          label: "dispatch" },
  };
  const sp = styles[mode];
  return (
    <span className="border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider" style={{ color: sp.color, borderColor: `color-mix(in oklab, ${sp.color} 40%, transparent)` }}>
      {sp.label}
    </span>
  );
}

function ToolBtn({
  children, primary, onClick, disabled,
}: {
  children: React.ReactNode;
  primary?: boolean;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`border px-4 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] transition disabled:cursor-not-allowed disabled:opacity-50 ${
        primary
          ? "border-[var(--approved)] bg-[var(--approved)] text-[#0a0d12] hover:bg-[#85e5b4]"
          : "border-[var(--rule-dark)] bg-transparent text-[#cdd4df] hover:border-[#404a5b] hover:bg-[var(--bg-elev)]"
      }`}
    >
      {children}
    </button>
  );
}

function IconBtn({ children, onClick, title, small }: { children: React.ReactNode; onClick?: () => void; title?: string; small?: boolean }) {
  const sz = small ? "h-6 w-6" : "h-8 w-8";
  return (
    <button onClick={onClick} title={title}
      className={`grid ${sz} place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]`}>
      {children}
    </button>
  );
}

function ViewHeader({ title, sub, right }: { title: string; sub: string; right?: React.ReactNode }) {
  return (
    <div className="border-b border-[var(--rule-dark)] px-10 pt-7 pb-5">
      <div className="flex items-end justify-between">
        <div>
          <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">LODESTAR · MANAGEMENT</div>
          <h1 className="mt-1 font-[family-name:var(--font-display)] text-[34px] font-semibold leading-none tracking-tight text-[#e6ecf5]">{title}</h1>
          <p className="mt-2 text-[13px] text-[#97a0b3]">{sub}</p>
        </div>
        <div>{right}</div>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">{children}</div>
  );
}

function BuildSeal() {
  return (
    <div className="pointer-events-none absolute right-5 bottom-3 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)] opacity-60">
      build · m0.2026.05 · feat/m0-plugin-foundation
    </div>
  );
}

// ============================== Icons ==============================
function ChevronDouble({ dir }: { dir: "left" | "right" }) {
  return (
    <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
      {dir === "left" ? (<><path d="M7 3 L3 6 L7 9" /><path d="M10 3 L6 6 L10 9" /></>) : (<><path d="M5 3 L9 6 L5 9" /><path d="M2 3 L6 6 L2 9" /></>)}
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2 5 V2 H5" /><path d="M12 5 V2 H9" /><path d="M2 9 V12 H5" /><path d="M12 9 V12 H9" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M3 3 L9 9" /><path d="M9 3 L3 9" />
    </svg>
  );
}
