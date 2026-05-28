"use client";

// M0 mock — 靜態假資料；M1 起改吃 /api/stages（catalog-driven）+ /api/stage/{id}/generate|refine|chat。
// Aesthetic：Industrial Cobalt × Drafting Dusk。
// Mock views：Workspace（PRD / Architecture / Stories）+ Workflows / Agents / Plugins。

import { useCallback, useEffect, useState } from "react";

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

// ---------- PRD ----------
type PrdReq = { code: string; text: string };
type DocSection =
  | { id: string; num: string; heading: string; kind: "paragraphs"; body: string[] }
  | { id: string; num: string; heading: string; kind: "items"; body: string[] }
  | { id: string; num: string; heading: string; kind: "reqs"; body: PrdReq[] };

const PRD_TITLE = "電商結帳重構";
const PRD_SUB = "Product Requirements · charted by system_analyst";

const PRD_SECTIONS: DocSection[] = [
  { id: "overview", num: "1", heading: "概述", kind: "paragraphs", body: [
    "重構現有結帳流程，提升 mobile 轉換率與並發承載能力，並補足 PCI-DSS 合規。",
    "本次重點放在 client/server 兩端的資料一致性、金流抽象層，以及尖峰時段的可用性。",
  ]},
  { id: "goals", num: "2", heading: "目標", kind: "items", body: [
    "結帳完成率 +15%（base 62% → 72%）",
    "尖峰並發 5,000 → 不掉單",
    "全鏈路 p95 < 800 ms",
  ]},
  { id: "fr", num: "3", heading: "功能需求", kind: "reqs", body: [
    { code: "FR-1", text: "訪客結帳（不強制註冊）" },
    { code: "FR-2", text: "多金流：信用卡 / Apple Pay / LINE Pay / 街口" },
    { code: "FR-3", text: "即時庫存校驗（避免超賣）" },
    { code: "FR-4", text: "訂單分割與部分退款" },
  ]},
  { id: "nfr", num: "4", heading: "非功能需求", kind: "reqs", body: [
    { code: "NFR-1", text: "尖峰 5,000 並發、p95 < 800 ms、p99 < 1.5 s" },
    { code: "NFR-2", text: "PCI-DSS Level 1；信用卡資料不入庫（tokenization）" },
    { code: "NFR-3", text: "99.95% 可用性（月度）" },
    { code: "NFR-4", text: "跨區資料一致性 ≤ 2 s" },
  ]},
  { id: "ops", num: "5", heading: "運維 / 安全", kind: "reqs", body: [
    { code: "OPS-1", text: "金鑰由 Secret Manager 管理；30 天輪替" },
    { code: "OPS-2", text: "所有交易事件保留 7 年（稅務合規）" },
  ]},
];

// ---------- Architecture ----------
const ARCH_TITLE = "電商結帳重構 · 系統架構";
const ARCH_SUB = "System Architecture · charted by software_architect";

const ARCH_SECTIONS: DocSection[] = [
  { id: "overview", num: "1", heading: "系統概觀", kind: "paragraphs", body: [
    "整體採 BFF + 微服務拆分。Web/Mobile 透過 API Gateway 進入，下游分 Checkout / Payment / Inventory 三個獨立部署的 service。",
    "金流抽象在 Payment Service 內，用 strategy pattern 支援多家金流商；其他服務不感知金流商差異。",
  ]},
  { id: "layering", num: "2", heading: "服務分層", kind: "items", body: [
    "Edge（API Gateway）：TLS 終止、WAF、rate-limit、JWT 驗證",
    "Services：Checkout / Payment / Inventory，各自獨立 DB、事件驅動解耦",
    "Data：PostgreSQL（orders、payments）+ Redis（庫存鎖、session）",
    "External：Stripe / Apple Pay / LINE Pay，全部隔離在 Payment Service 後",
  ]},
  { id: "capacity", num: "3", heading: "容量規劃", kind: "items", body: [
    "尖峰 5,000 QPS → Checkout 6 instances × 1,000 QPS（30% headroom）",
    "Payment 受外部 API rate-limit → 內部 queue + retry with jitter",
    "DB 讀寫分離：1 主 + 2 讀；replication lag SLO ≤ 2s",
    "Redis Cluster 3 主 3 從，記憶體 32 GB，TTL 10 min 自動釋放鎖",
  ]},
  { id: "data", num: "4", heading: "資料模型", kind: "reqs", body: [
    { code: "T-ORDER", text: "orders、order_items、refunds（PostgreSQL）" },
    { code: "T-PAY",   text: "payment_attempts、payment_tokens（PostgreSQL；敏感欄位 KMS 加密）" },
    { code: "T-STOCK", text: "stock_locks、stock_levels（Redis；SETNX + TTL）" },
    { code: "T-EVENT", text: "outbox（PostgreSQL）→ Kafka topic（事件驅動下游）" },
  ]},
  { id: "security", num: "5", heading: "安全 / 合規", kind: "reqs", body: [
    { code: "S-1", text: "金鑰 30 天輪替（Secret Manager + rotation lambda）" },
    { code: "S-2", text: "PCI-DSS L1：卡號完全 tokenization；不入庫" },
    { code: "S-3", text: "稽核日誌寫到不可變儲存（S3 Object Lock，7 年）" },
    { code: "S-4", text: "金流外部 API 走專用 egress NAT；IP allowlist 鎖定" },
  ]},
];

// ---------- Stories ----------
type Story = {
  code: string; title: string; estimate: number; group: string;
  labels: string[]; reqs: string[]; ac: string[];
};
const STORIES: Story[] = [
  { code: "US-1", title: "訪客結帳流程",          estimate: 3, group: "Phase 1", labels: ["checkout", "guest"],   reqs: ["FR-1"],          ac: ["未登入可結帳", "完成後 email 收據", "可選綁定帳號"] },
  { code: "US-2", title: "信用卡 + 3DS 流程",     estimate: 5, group: "Phase 1", labels: ["payment", "card"],     reqs: ["FR-2", "NFR-2"], ac: ["Stripe tokenization", "3DS challenge flow", "錯誤訊息 i18n"] },
  { code: "US-3", title: "Apple Pay 整合",        estimate: 3, group: "Phase 1", labels: ["payment", "wallet"],   reqs: ["FR-2"],          ac: ["Safari 顯示按鈕", "成功率 > 95%", "fallback to 信用卡"] },
  { code: "US-4", title: "LINE Pay 整合",         estimate: 3, group: "Phase 1", labels: ["payment", "wallet"],   reqs: ["FR-2"],          ac: ["重導流程", "未付款 reconcile job"] },
  { code: "US-5", title: "即時庫存校驗 + 鎖庫存", estimate: 5, group: "Phase 2", labels: ["inventory"],            reqs: ["FR-3"],          ac: ["Redis SETNX 鎖", "10 min TTL 釋放", "超賣 < 0.1%"] },
  { code: "US-6", title: "訂單部分退款",          estimate: 5, group: "Phase 2", labels: ["order", "refund"],     reqs: ["FR-4"],          ac: ["逐項退款", "partial-capture", "退款憑證"] },
  { code: "US-7", title: "尖峰壓測 + p95 監控",   estimate: 5, group: "Phase 2", labels: ["perf", "obs"],         reqs: ["NFR-1", "NFR-3"], ac: ["k6 5k vu", "p95 dashboard", "alert rule"] },
  { code: "US-8", title: "跨區資料一致",          estimate: 8, group: "Phase 3", labels: ["infra", "data"],       reqs: ["NFR-4"],         ac: ["lag < 2s 指標", "讀寫分離文件", "切流 runbook"] },
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
}: {
  onOpenFs: () => void;
  thread: string | null;
  artifact: string;
  status: string;
  busy: PrdBusy;
  onGenerate: () => void;
  onRefine: () => void;
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
        <article className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)] text-[var(--ink)]">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {hasContent ? (
              <PrdArtifactView artifact={artifact} />
            ) : (
              <PrdEmptyState busy={busy} thread={thread} onGenerate={onGenerate} />
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

function PrdEmptyState({ busy, thread, onGenerate }: { busy: PrdBusy; thread: string | null; onGenerate: () => void }) {
  return (
    <div className="flex h-full items-center justify-center px-10 py-12">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-5 grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">
          01
        </div>
        <h3 className="font-[family-name:var(--font-display)] text-[26px] font-semibold text-[#e6ecf5]">
          尚未標繪
        </h3>
        <p className="mt-2 text-[13px] leading-6 text-[#7a8499]">
          PRD 是 pipeline 的起點。點下方按鈕，
          <br />
          系統分析師（claude-cli）會與你對話／生成完整 PRD。
        </p>
        <div className="mx-auto my-6 h-px w-20 bg-[var(--rule-dark)]" />
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

// ============================== Architecture workspace ==============================
function ArchWorkspace() {
  const [view, setView] = useState<"document" | "diagram">("document");
  const [zoom, setZoom] = useState(1);
  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="architecture" stage="design" op="generate_architecture" right={
          <>
            <ViewToggle value={view} onChange={setView} />
            <DraftPill />
            {view === "diagram" && (
              <>
                <ZoomControls zoom={zoom} onZoom={setZoom} />
                <IconBtn title="重置縮放" onClick={() => setZoom(1)}><ResetIcon /></IconBtn>
              </>
            )}
          </>
        } />
        <div className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)] text-[var(--ink)]">
          {view === "document" ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <Article title={ARCH_TITLE} sub={ARCH_SUB} kind="SYSTEM ARCHITECTURE · V0.3 (DRAFT)" sections={ARCH_SECTIONS} />
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-[var(--rule)] px-6 py-2.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[#7a8499]">
                <span>{"// architecture.mmd · service topology"}</span>
                <span>generated 2 min ago · in revision</span>
              </div>
              <div className="relative min-h-0 flex-1 overflow-auto">
                <MermaidCanvas zoom={zoom} />
              </div>
            </>
          )}
        </div>
        <BottomMeta left="6 services · 3 datastores · 4 external APIs · sha · b8e1d4f" right={<>depends_on <span className="text-[#b8c0cf]">prd</span> · downstream <span className="text-[#b8c0cf]">stories</span></>} />
        <OperationsRow primaryLabel="核准架構" />
      </section>
      <ChatPanel />
    </div>
  );
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

function ZoomControls({ zoom, onZoom }: { zoom: number; onZoom: (z: number) => void }) {
  return (
    <div className="flex items-center gap-0.5 border border-[var(--rule-dark)] bg-[var(--bg-elev)] px-0.5 py-0.5">
      <IconBtn small onClick={() => onZoom(Math.max(0.4, +(zoom - 0.2).toFixed(2)))} title="縮小"><span className="text-[14px] leading-none">−</span></IconBtn>
      <span className="grid min-w-[3.5em] place-items-center px-1 font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">{Math.round(zoom * 100)}%</span>
      <IconBtn small onClick={() => onZoom(Math.min(2.5, +(zoom + 0.2).toFixed(2)))} title="放大"><span className="text-[14px] leading-none">+</span></IconBtn>
    </div>
  );
}

function MermaidCanvas({ zoom }: { zoom: number }) {
  return (
    <div className="grid h-full w-full place-items-center p-8">
      <svg viewBox="0 0 900 560" width="900" height="560"
        style={{ transform: `scale(${zoom})`, transformOrigin: "center center", transition: "transform 220ms cubic-bezier(0.2,0.7,0.2,1)" }}
        className="max-w-full">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4a5468" />
          </marker>
          <marker id="arrow-chart" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#5b8cff" />
          </marker>
        </defs>
        <ArchLane y={30}  label="CLIENTS" />
        <ArchLane y={170} label="EDGE" />
        <ArchLane y={300} label="SERVICES" />
        <ArchLane y={430} label="DATA / EXTERNAL" />
        <ArchBox x={200} y={20}  w={160} h={56} title="Web"        sub="NEXT.JS · TS" />
        <ArchBox x={540} y={20}  w={160} h={56} title="Mobile"     sub="iOS / ANDROID" />
        <ArchBox x={370} y={160} w={160} h={56} title="API Gateway" sub="EDGE / TLS / WAF" accent />
        <ArchBox x={100} y={290} w={170} h={56} title="Checkout"    sub="FR-1 · FR-3 · FR-4" accent />
        <ArchBox x={365} y={290} w={170} h={56} title="Payment"     sub="FR-2 · tokenization" />
        <ArchBox x={630} y={290} w={170} h={56} title="Inventory"   sub="FR-3 stock guard" />
        <ArchBox x={100} y={420} w={170} h={56} title="PostgreSQL"  sub="orders · users" muted />
        <ArchBox x={365} y={420} w={170} h={56} title="Redis"       sub="stock · sessions" muted />
        <ArchBox x={630} y={420} w={170} h={56} title="External"    sub="Stripe / ApplePay / LINE Pay" muted />
        <line x1="280" y1="76"  x2="430" y2="160" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="620" y1="76"  x2="470" y2="160" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="430" y1="216" x2="200" y2="290" stroke="#5b8cff" strokeWidth="1.4" markerEnd="url(#arrow-chart)" />
        <line x1="450" y1="216" x2="445" y2="290" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="470" y1="216" x2="715" y2="290" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="185" y1="346" x2="185" y2="420" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="450" y1="346" x2="450" y2="420" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="715" y1="346" x2="715" y2="420" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
        <line x1="270" y1="318" x2="365" y2="318" stroke="#5b8cff" strokeWidth="1.4" markerEnd="url(#arrow-chart)" strokeDasharray="2 3" />
        <line x1="535" y1="332" x2="630" y2="440" stroke="#4a5468" strokeWidth="1.2" markerEnd="url(#arrow)" />
      </svg>
    </div>
  );
}

function ArchLane({ y, label }: { y: number; label: string }) {
  return (
    <g>
      <text x={28} y={y + 18} fontFamily="var(--font-mono)" fontSize="10" letterSpacing="3" fill="#5b6479">{label}</text>
      <line x1={28} y1={y + 26} x2={870} y2={y + 26} stroke="#1c222e" strokeWidth="1" strokeDasharray="2 4" />
    </g>
  );
}

function ArchBox({ x, y, w, h, title, sub, accent, muted }: {
  x: number; y: number; w: number; h: number; title: string; sub: string; accent?: boolean; muted?: boolean;
}) {
  const fill = muted ? "#11151c" : "#161c26";
  const stroke = accent ? "#5b8cff" : muted ? "#2a3242" : "#3a4054";
  return (
    <g transform={`translate(${x},${y})`}>
      <rect width={w} height={h} rx={2} fill={fill} stroke={stroke} strokeWidth={accent ? "1.3" : "1"} />
      <text x={w / 2} y={24} textAnchor="middle" fontFamily="var(--font-display)" fontWeight="600" fontSize="14" fill="#e6ecf5">{title}</text>
      <text x={w / 2} y={42} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="9" letterSpacing="1.2" fill="#7a8499">{sub}</text>
    </g>
  );
}

// ============================== Stories workspace ==============================
function StoriesWorkspace() {
  const [picked, setPicked] = useState<string | null>("US-2");
  const groups = Array.from(new Set(STORIES.map((s) => s.group)));
  const detail: Story | null = picked ? STORIES.find((s) => s.code === picked) ?? null : null;
  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="stories" stage="deliver" op="generate_stories" right={
          <>
            <DraftPill />
            <button className="border border-[var(--rule-dark)] bg-[var(--bg-elev)] px-3 py-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">
              Preview to GitHub
            </button>
          </>
        } />
        <div className="shadow-anvil paper-texture min-h-0 flex-1 overflow-y-auto bg-[var(--paper)] px-8 py-7">
          <div className="mb-7 border-b border-[var(--rule)] pb-5">
            <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">DELIVERABLE STORIES · V1</div>
            <h2 className="mt-2 font-[family-name:var(--font-display)] text-[26px] font-semibold leading-tight text-[#e6ecf5]">{STORIES.length} stories · {STORIES.reduce((s, x) => s + x.estimate, 0)} pts</h2>
            <div className="mt-3 flex items-center gap-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              <span>charted by product_owner</span>
              <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
              <span>2 min ago</span>
              <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
              <span>3 phases</span>
            </div>
          </div>
          {groups.map((g) => (
            <div key={g} className="mb-7 last:mb-0">
              <div className="mb-3 flex items-baseline gap-3">
                <span className="font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--polaris)]">{g}</span>
                <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  {STORIES.filter((s) => s.group === g).length} stories · {STORIES.filter((s) => s.group === g).reduce((sum, x) => sum + x.estimate, 0)} pts
                </span>
                <span className="h-px flex-1 bg-[var(--rule)]" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                {STORIES.filter((s) => s.group === g).map((s) => (
                  <button key={s.code} onClick={() => setPicked(s.code)}
                    className={`flex flex-col items-stretch border bg-[var(--bg-elev)] p-4 text-left transition ${
                      picked === s.code ? "border-[var(--polaris)] glow-star" : "border-[var(--paper-edge)] hover:border-[#4a5468]"
                    }`}>
                    <div className="mb-2 flex items-center justify-between">
                      <code className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{s.code}</code>
                      <span className="border border-[var(--rule-dark)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[#cdd4df]">
                        {s.estimate} pts
                      </span>
                    </div>
                    <div className="mb-2 font-[family-name:var(--font-display)] text-[15px] font-semibold text-[#e6ecf5]">{s.title}</div>
                    <div className="mb-3 flex flex-wrap gap-1">
                      {s.labels.map((l) => (
                        <span key={l} className="border border-[var(--rule-dark)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[#7a8499]">
                          {l}
                        </span>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                      <span>links</span>
                      {s.reqs.map((r) => (
                        <code key={r} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 text-[var(--polaris)]">{r}</code>
                      ))}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <BottomMeta left={`${STORIES.length} stories · ${STORIES.reduce((s, x) => s + x.estimate, 0)} story points · 3 phases · sha · c2f9a18`} right={<>depends_on <span className="text-[#b8c0cf]">architecture</span> · publish to <span className="text-[#b8c0cf]">github / jira</span></>} />
        <div className="mt-4 flex items-center justify-end gap-2">
          <ToolBtn>Refine…</ToolBtn>
          <ToolBtn>手動編輯</ToolBtn>
          <ToolBtn primary>發佈到 GitHub</ToolBtn>
        </div>
      </section>
      <StoryDetail story={detail} />
    </div>
  );
}

function StoryDetail({ story }: { story: Story | null }) {
  return (
    <aside className="rise-4 flex w-[400px] shrink-0 flex-col border-l border-[var(--rule-dark)] bg-[var(--bg-elev)]/40">
      <div className="flex items-center justify-between border-b border-[var(--rule-dark)] px-6 py-4">
        <div className="flex items-baseline gap-3">
          <h3 className="font-[family-name:var(--font-display)] text-[17px] font-semibold text-[#e6ecf5]">Story Detail</h3>
          {story && <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--polaris)]">{story.code}</span>}
        </div>
        <span className="grid h-7 w-7 place-items-center border border-[var(--rule-dark)] font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">⋯</span>
      </div>
      {story ? (
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          <div>
            <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">TITLE</div>
            <div className="mt-1 font-[family-name:var(--font-display)] text-[20px] font-semibold leading-tight text-[#e6ecf5]">{story.title}</div>
          </div>
          <div className="grid grid-cols-2 gap-3 border-y border-[var(--rule-dark)] py-3 text-[12px]">
            <KV k="ESTIMATE" v={`${story.estimate} pts`} />
            <KV k="PHASE" v={story.group} />
            <KV k="LABELS" v={story.labels.join(", ")} />
            <KV k="REQS" v={story.reqs.join(", ")} />
          </div>
          <div>
            <div className="mb-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">ACCEPTANCE CRITERIA</div>
            <ul className="space-y-2">
              {story.ac.map((a, i) => (
                <li key={i} className="flex items-start gap-2.5 text-[13px] leading-6 text-[#cdd4df]">
                  <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center border border-[var(--paper-edge)] font-[family-name:var(--font-mono)] text-[9px] text-[var(--polaris)]">AC{i + 1}</span>
                  <span>{a}</span>
                </li>
              ))}
            </ul>
          </div>
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

// ============================== Article (shared by PRD / Architecture) ==============================
function Article({ title, sub, kind, sections, wide = false }: {
  title: string; sub: string; kind: string; sections: DocSection[]; wide?: boolean;
}) {
  return (
    <div className={`mx-auto ${wide ? "max-w-3xl px-10 py-12" : "max-w-none px-10 py-10"}`}>
      <header className="mb-9 border-b border-[var(--rule)] pb-5">
        <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">{kind}</div>
        <h1 className="mt-2 font-[family-name:var(--font-display)] text-[28px] font-semibold leading-tight text-[#e6ecf5]">{title}</h1>
        <div className="mt-3 flex items-center gap-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          <span>{sub}</span>
          <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
          <span>5 min ago</span>
        </div>
      </header>
      <div className="space-y-9">
        {sections.map((sec) => (
          <section key={sec.id}>
            <h2 className="mb-4 flex items-baseline gap-3 font-[family-name:var(--font-display)] text-[19px] font-semibold leading-none text-[#e6ecf5]">
              <span className="font-[family-name:var(--font-mono)] text-[12px] font-normal tracking-[0.2em] text-[var(--polaris)]">§{sec.num}</span>
              {sec.heading}
            </h2>
            {sec.kind === "paragraphs" && (
              <div className="space-y-2.5 font-[family-name:var(--font-sans)] text-[14.5px] leading-[1.75] text-[#cdd4df]">
                {sec.body.map((p, i) => <p key={i}>{p}</p>)}
              </div>
            )}
            {sec.kind === "items" && (
              <ul className="space-y-2 font-[family-name:var(--font-sans)] text-[14.5px] leading-[1.7] text-[#cdd4df]">
                {sec.body.map((item, i) => (
                  <li key={i} className="flex items-start gap-3">
                    <span className="mt-[10px] inline-block h-1 w-1 shrink-0 rounded-full bg-[var(--polaris)]" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}
            {sec.kind === "reqs" && (
              <ul className="space-y-2.5">
                {sec.body.map((r) => (
                  <li key={r.code} className="flex items-start gap-3.5">
                    <code className="mt-0.5 inline-flex shrink-0 items-center border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[11px] font-medium tracking-wider text-[var(--polaris)]">{r.code}</code>
                    <span className="font-[family-name:var(--font-sans)] text-[14.5px] leading-[1.65] text-[#cdd4df]">{r.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
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

function ResetIcon() {
  return (
    <svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M3 7 a4 4 0 1 0 1.5 -3" /><path d="M3 3 V5 H5" />
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
