"use client";

// M0 mock — 靜態假資料；M1 起 PRD 改吃 /api/stages（catalog-driven）+ /api/stage/{id}/generate|refine|chat。
// M2.2 mock review：Architecture / Stories 用 M2.1 E2E 真實 claude-cli 生成內容當靜態假資料，
//   先給看 UI 結構與排版；M2.3 才 wire 真實 API。
// Aesthetic：Industrial Cobalt × Drafting Dusk。

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentEditorModal, type AgentDraft } from "@/components/AgentEditorModal";
import { SkillEditorModal, type SkillDraft } from "@/components/SkillEditorModal";
import { ConfirmDialog, PromptDialog } from "@/components/Modal";
import { PublishModal } from "@/components/PublishModal";
import { IntegrationsModal } from "@/components/IntegrationsModal";
import { ProjectDeliveryModal } from "@/components/ProjectDeliveryModal";
import RcaWorkspace from "@/components/RcaWorkspace";
import {
  type Agent,
  type Skill,
  type AgentBinding as ApiAgentBinding,
  type CollabMode as ApiCollabMode,
  type CollabRole as ApiCollabRole,
  type Plugin,
  type Workflow,
  type WorkflowStage,
  type ImplementSession,
  type ImplementRun,
  type ImplementLogLine,
  type ImplementBatch,
  type RunnerInfo,
  type StageCatalogItem,
  apiCall,
  createAgent,
  createSkill,
  createWorkflow,
  deleteAgentApi,
  deleteSkillApi,
  fetchSkills,
  setAgentSkills,
  updateSkill,
  deleteWorkflowApi,
  fetchAgents,
  fetchPlugins,
  fetchStages,
  fetchWorkflows,
  setProjectWorkflow,
  togglePlugin,
  updateAgent,
  updateWorkflow,
  fetchRunners,
  startImplement,
  fetchImplementSession,
  fetchImplementSessions,
  fetchImplementLog,
  cancelImplement,
  startBatch,
  fetchBatch,
  fetchBatches,
  cancelBatch,
  type StageChatMessage,
  type DeliveryStatus,
  fetchStageHistory,
  stageChat,
} from "@/lib/api";
import {
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

// ============================== Stage stepper static catalog（M2.2：UI 標示用；M2.3 改 catalog-driven）==============================
// status 是 fallback —— Page 會把真實 prdStatus 傳給 Stepper，其他 stage 用此處 default。
type StageStatus = "approved" | "draft" | "needs_revision" | "locked";

const STAGES: Array<{
  id: string; n: string; label: string; caption: string; status: StageStatus; badge: string; agent: string;
}> = [
  { id: "prd",          n: "01", label: "PRD",            caption: "PRODUCT REQUIREMENTS", status: "draft",   badge: "CHARTING", agent: "system_analyst"     },
  { id: "architecture", n: "02", label: "Architecture",   caption: "SYSTEM DESIGN",        status: "draft",   badge: "CHARTING", agent: "software_architect" },
  { id: "stories",      n: "03", label: "Stories",        caption: "DELIVERABLE STORIES",  status: "draft",   badge: "DRAFTED",  agent: "product_owner"      },
  { id: "implement",    n: "04", label: "Implementation", caption: "AUTO-CODE · M5",       status: "locked",  badge: "DISPATCH", agent: "3 agents · dispatch"},
];

// ============================== Project（M1 / M2 baseline）==============================
type Project = {
  thread_id: string;
  name: string;
  workflow_id: string | null;
  created_at: number;
};

// 用於 sidebar 顯示的縮寫 glyph：取 name 第一個非空白字元
function projectGlyph(p: Project): string {
  const ch = p.name.trim()[0];
  return ch ?? "?";
}

// ============================== Modal state（取代 window.prompt / window.confirm）==============================
type StageRefineKind = "prd" | "architecture" | "stories";
const STAGE_LABEL: Record<StageRefineKind, string> = {
  prd: "PRD", architecture: "架構", stories: "使用者故事",
};
const STAGE_REFINE_PLACEHOLDER: Record<StageRefineKind, string> = {
  prd: "例：加上 OAuth 登入；NFR 並發提到 10k；補充 OPS 監控指標……",
  architecture: "例：拆出獨立 Notification service；改用 gRPC 取代 REST；資料庫換 PostgreSQL……",
  stories: "例：拆掉超過 4h 的故事；補上 CI/CD scaffold story；對齊 NFR 切跨 epic……",
};

type ModalState =
  | { kind: "none" }
  | { kind: "newThread" }
  | { kind: "renameThread"; threadId: string; currentName: string }
  | { kind: "confirmDeleteThread"; threadId: string; threadName: string }
  | { kind: "refineStage"; stageId: StageRefineKind };

// ============================== ModelAdapter（GET /api/models）==============================
type ModelAdapterInfo = {
  model_choice: string;
  description: string;
  is_available: boolean;
  supports_multimodal: boolean;
  max_context_tokens: number;
  prompt_budget_tokens: number;
  response_budget_tokens: number;
  source_plugin: string | null;
};

const MODEL_STORAGE_KEY = "lodestar.model_choice";
const DEFAULT_MODEL = "claude-cli";

const NAV = [
  { id: "workspace", label: "WORKSPACE" },
  { id: "workflows", label: "WORKFLOWS" },
  { id: "agents",    label: "AGENTS" },
  { id: "skills",    label: "SKILLS" },
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


// Chat 已改吃真實 per-thread 歷史（見 ChatPanel + lib/api fetchStageHistory/stageChat）。
// 舊的 multi-agent discussion mock（SPEAKER_STYLES / CHAT）已移除。

// ============================== Page ==============================
export default function Page() {
  const [nav, setNav] = useState<string>("workspace");
  const [selected, setSelected] = useState<string>("prd");
  // 初始恆為 true（與 SSR 一致，避免 hydration mismatch）；窄螢幕在 mount 後的 effect 收合。
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
  // ===== M2.3：Architecture / Stories 真實 state（接 /api/stage/{sid}/{tid}）=====
  type StageBusy = false | "generate" | "refine";
  const [archArtifact, setArchArtifact] = useState<string>("");
  const [archStatus, setArchStatus] = useState<string>("draft");
  const [archBusy, setArchBusy] = useState<StageBusy>(false);
  const [storiesArtifact, setStoriesArtifact] = useState<string>("");
  const [storiesStatus, setStoriesStatus] = useState<string>("draft");
  const [storiesBusy, setStoriesBusy] = useState<StageBusy>(false);
  const [storiesDelivery, setStoriesDelivery] = useState<DeliveryStatus | null>(null);
  // ===== M2 baseline：真實 thread list + plugin count + model list + modal state =====
  const [threadList, setThreadList] = useState<Project[]>([]);
  const [pluginCount, setPluginCount] = useState<number | null>(null);
  const [modelList, setModelList] = useState<ModelAdapterInfo[]>([]);
  // modelChoice：preference 來源是 localStorage；fetch model list 後若 invalid 會 fallback
  const [modelChoice, setModelChoice] = useState<string>(() => {
    if (typeof window === "undefined") return DEFAULT_MODEL;
    return window.localStorage.getItem(MODEL_STORAGE_KEY) || DEFAULT_MODEL;
  });
  const [modal, setModal] = useState<ModalState>({ kind: "none" });
  // M2.5：Publish modal 開啟旗標（獨立 state，因為它是 multi-step internal state machine）
  const [publishOpen, setPublishOpen] = useState(false);
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const [projectModal, setProjectModal] = useState<{ open: boolean; thread: string | null }>({ open: false, thread: null });
  // M3：workflows / agents 真實 list（給 /workflows, /agents 頁面與 thread switcher 共用）
  const [workflowList, setWorkflowList] = useState<Workflow[]>([]);
  const [agentList, setAgentList] = useState<Agent[]>([]);
  const [skillList, setSkillList] = useState<Skill[]>([]);
  const [pluginList, setPluginList] = useState<Plugin[]>([]);   // M4
  // M3：Agent / Skill editor modal state
  const [agentEditor, setAgentEditor] = useState<{ open: boolean; initial: Agent | null }>({ open: false, initial: null });
  const [skillEditor, setSkillEditor] = useState<{ open: boolean; initial: Skill | null }>({ open: false, initial: null });
  // M3：delete confirm modal（共用給 workflow / agent / skill 刪除）
  const [deleteConfirm, setDeleteConfirm] = useState<{
    kind: "workflow" | "agent" | "skill"; id: string; name: string;
  } | null>(null);

  // 清掉所有 per-thread 衍生狀態（切 thread / 刪除後重置 UI 用）
  const resetThreadDerivedState = useCallback(() => {
    setPrdArtifact("");
    setPrdStatus("draft");
    setAttachments([]);
    setBusy(false);
    setArchArtifact("");
    setArchStatus("draft");
    setArchBusy(false);
    setStoriesArtifact("");
    setStoriesStatus("draft");
    setStoriesBusy(false);
    setStoriesDelivery(null);
  }, []);

  // 切換 active thread —— 同時寫 localStorage、清衍生狀態、回到 workspace / PRD
  const switchThread = useCallback((tid: string) => {
    window.localStorage.setItem("lodestar.thread", tid);
    setThread(tid);
    resetThreadDerivedState();
    setNav("workspace");
    setSelected("prd");
  }, [resetThreadDerivedState]);

  // 拉真實 thread list
  const refreshThreadList = useCallback(async () => {
    try {
      const r = await apiFetch<{ projects: Project[] }>("/api/projects");
      setThreadList(r.projects);
      return r.projects;
    } catch (e) {
      setErr(`讀取專案列表失敗：${(e as Error).message}`);
      return [];
    }
  }, []);

  // bootstrap：取 list → localStorage 對得到就用，對不到就用第一個，list 空就建新
  useEffect(() => {
    let mounted = true;
    (async () => {
      const projects = await refreshThreadList();
      if (!mounted) return;
      const stored = typeof window !== "undefined"
        ? window.localStorage.getItem("lodestar.thread")
        : null;
      const matched = stored && projects.find((p) => p.thread_id === stored);
      if (matched) {
        setThread(matched.thread_id);
        return;
      }
      if (projects.length > 0) {
        const first = projects[0];
        window.localStorage.setItem("lodestar.thread", first.thread_id);
        setThread(first.thread_id);
        return;
      }
      // 空 list → 建一個 default thread
      try {
        const p = await apiFetch<{ thread_id: string }>("/api/projects", {
          method: "POST", body: JSON.stringify({ name: "新需求" }),
        });
        if (!mounted) return;
        window.localStorage.setItem("lodestar.thread", p.thread_id);
        setThread(p.thread_id);
        await refreshThreadList();
      } catch (e) {
        if (mounted) setErr(`建立 thread 失敗：${(e as Error).message}`);
      }
    })();
    return () => { mounted = false; };
  }, [refreshThreadList]);

  // 拉 plugin count（給 TopBar 顯示）
  useEffect(() => {
    let mounted = true;
    apiFetch<{ plugins: Array<{ enabled: boolean; load_error: string | null }> }>("/api/plugins")
      .then((r) => {
        if (!mounted) return;
        setPluginCount(r.plugins.filter((p) => p.enabled && !p.load_error).length);
      })
      .catch(() => {/* silent：plugin 端點失敗不致命 */});
    return () => { mounted = false; };
  }, []);

  // 拉 model list；fetch 完若 localStorage 存的 model 不在 registry 內 → fallback 第一個可用
  useEffect(() => {
    let mounted = true;
    apiFetch<{ models: ModelAdapterInfo[] }>("/api/models")
      .then((r) => {
        if (!mounted) return;
        setModelList(r.models);
        const choices = new Set(r.models.map((m) => m.model_choice));
        setModelChoice((prev) => {
          if (choices.has(prev)) return prev;
          const firstAvailable = r.models.find((m) => m.is_available);
          const fallback = firstAvailable?.model_choice
            ?? r.models[0]?.model_choice
            ?? DEFAULT_MODEL;
          window.localStorage.setItem(MODEL_STORAGE_KEY, fallback);
          return fallback;
        });
      })
      .catch(() => {/* silent：model 端點失敗時用預設 claude-cli */});
    return () => { mounted = false; };
  }, []);

  // 切 model：localStorage + state（後續 generate/refine 自動套用）
  const onSelectModel = useCallback((choice: string) => {
    window.localStorage.setItem(MODEL_STORAGE_KEY, choice);
    setModelChoice(choice);
  }, []);

  // ===== M3：workflows / agents refresh =====
  const refreshWorkflows = useCallback(async () => {
    try {
      setWorkflowList(await fetchWorkflows());
    } catch (e) {
      setErr(`讀取 workflows 失敗：${(e as Error).message}`);
    }
  }, []);
  const refreshAgents = useCallback(async () => {
    try {
      setAgentList(await fetchAgents());
    } catch (e) {
      setErr(`讀取 agents 失敗：${(e as Error).message}`);
    }
  }, []);
  const refreshSkills = useCallback(async () => {
    try {
      setSkillList(await fetchSkills());
    } catch (e) {
      setErr(`讀取 skills 失敗：${(e as Error).message}`);
    }
  }, []);
  const refreshPlugins = useCallback(async () => {
    try {
      setPluginList(await fetchPlugins());
    } catch (e) {
      setErr(`讀取 plugins 失敗：${(e as Error).message}`);
    }
  }, []);

  // 啟動時抓一次（thread switcher dropdown / plugins view 用得到）
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const [wfs, ags, sks, plugs] = await Promise.all([fetchWorkflows(), fetchAgents(), fetchSkills(), fetchPlugins()]);
        if (!mounted) return;
        setWorkflowList(wfs);
        setAgentList(ags);
        setSkillList(sks);
        setPluginList(plugs);
      } catch { /* silent */ }
    })();
    return () => { mounted = false; };
  }, []);

  // ===== M4：plugin enable / disable —— toggle 後 refetch plugins + catalog 相關 list =====
  const onTogglePlugin = useCallback(async (id: string, enabled: boolean) => {
    setErr(null);
    try {
      await togglePlugin(id, enabled);
      // plugin 變動會影響 catalog（stages）/ workflows / agents / plugin count → 全部 refetch
      const [plugs, wfs, ags] = await Promise.all([fetchPlugins(), fetchWorkflows(), fetchAgents()]);
      setPluginList(plugs);
      setWorkflowList(wfs);
      setAgentList(ags);
      setPluginCount(plugs.filter((p) => p.enabled && !p.load_error).length);
    } catch (e) {
      setErr(`切換 plugin 失敗：${(e as Error).message}`);
      // 失敗時 refetch 還原 UI
      refreshPlugins();
    }
  }, [refreshPlugins]);

  // ===== M3：thread workflow switcher =====
  const [currentProjectWorkflowId, setCurrentProjectWorkflowId] = useState<string | null>(null);
  useEffect(() => {
    if (!thread) return;
    apiCall<{ workflow_id: string | null }>(`/api/projects/${thread}`)
      .then((p) => setCurrentProjectWorkflowId(p.workflow_id ?? null))
      .catch(() => {/* silent */});
  }, [thread]);

  // RCA：catalog（給 WorkflowsView availableStages + 每個 stage 的用途說明，catalog-driven）
  const [catalogStages, setCatalogStages] = useState<StageCatalogItem[]>([]);
  useEffect(() => { fetchStages().then(setCatalogStages).catch(() => {/* silent */}); }, []);
  const catalogStageIds = useMemo(() => catalogStages.map((s) => s.id), [catalogStages]);
  const stageInfo = useMemo(
    () => Object.fromEntries(catalogStages.map((s) => [s.id, { label: s.label, description: s.description }])),
    [catalogStages],
  );
  // StageHeader 用：stage_id → 顯示 meta（含 icon）。
  const stageMeta = useMemo(
    () => Object.fromEntries(catalogStages.map((s) => [s.id, { label: s.label, description: s.description, icon: s.icon }])),
    [catalogStages],
  );
  // RCA thread：綁定的 workflow 以 rca 開頭 → workspace 走 RcaWorkspace（與 PRD 流程互不干擾）
  const isRcaThread = !!currentProjectWorkflowId && currentProjectWorkflowId.startsWith("rca");

  // M3：StageHeader 階段圖表改 catalog-driven —— 依「當前綁定 workflow 的 stages」動態渲染，
  // 不再用寫死的 STAGES 常數（否則切 workflow 中央圖表不變，使用者覺得「沒反應」）。
  const activeWorkflow = useMemo(
    () => workflowList.find((w) => w.id === (currentProjectWorkflowId ?? "default")) ?? null,
    [workflowList, currentProjectWorkflowId],
  );
  const activeStages = useMemo<WorkflowStage[]>(
    () => activeWorkflow?.stages ?? STAGES.map((s) => ({ stage_id: s.id, depends_on: [], agent_bindings: [], collab_mode: "single" })),
    [activeWorkflow],
  );
  const workflowSource = activeWorkflow?.source_plugin ?? activeWorkflow?.source ?? "—";
  // thread 初次載入 / workflow 變動後，若 selected 不在當前 stages 內 → 對齊到第一個 stage
  useEffect(() => {
    const ids = activeStages.map((s) => s.stage_id);
    if (ids.length && !ids.includes(selected)) setSelected(ids[0]);
  }, [activeStages, selected]);

  // onChangeProjectWorkflow 移到 refreshPrd/Arch/Stories 後（避免 TS 引用順序錯誤）

  // ===== M3：Agent CRUD handlers =====
  const onSaveAgent = useCallback(async (draft: AgentDraft) => {
    const isEdit = !!agentEditor.initial;
    // 解構排除 skill_ids（agent 主體 CRUD 不收 skills）；綁定走獨立的 setAgentSkills。
    const { skill_ids, ...agentBody } = draft;
    if (isEdit) {
      await updateAgent(draft.agent_id, agentBody);
    } else {
      await createAgent(agentBody);
    }
    // agent_id 由表單指定（非後端產生），此時 row 已存在 → 安全綁定
    await setAgentSkills(draft.agent_id, skill_ids);
    setAgentEditor({ open: false, initial: null });
    await refreshAgents();
  }, [agentEditor.initial, refreshAgents]);

  const onSaveSkill = useCallback(async (draft: SkillDraft) => {
    const isEdit = !!skillEditor.initial;
    if (isEdit) {
      await updateSkill(draft.skill_id, draft);
    } else {
      await createSkill(draft);
    }
    setSkillEditor({ open: false, initial: null });
    await refreshSkills();
    await refreshAgents();   // 綁了此 skill 的 agent 卡片 name 可能變
  }, [skillEditor.initial, refreshSkills, refreshAgents]);

  const onDeleteConfirmed = useCallback(async () => {
    if (!deleteConfirm) return;
    setErr(null);
    try {
      if (deleteConfirm.kind === "workflow") {
        await deleteWorkflowApi(deleteConfirm.id);
        await refreshWorkflows();
      } else if (deleteConfirm.kind === "skill") {
        await deleteSkillApi(deleteConfirm.id);
        await refreshSkills();
        await refreshAgents();   // 綁定被連帶清除 → 重抓 agent
      } else {
        await deleteAgentApi(deleteConfirm.id);
        await refreshAgents();
      }
    } catch (e) {
      setErr(`刪除失敗：${(e as Error).message}`);
    } finally {
      setDeleteConfirm(null);
    }
  }, [deleteConfirm, refreshWorkflows, refreshAgents, refreshSkills]);

  // ===== Thread CRUD callbacks（打開 modal；submit 才呼 API）=====
  const onNewThread = useCallback(() => setProjectModal({ open: true, thread: null }), []);
  const onConfigureDelivery = useCallback((tid: string) => setProjectModal({ open: true, thread: tid }), []);
  const onRenameThread = useCallback((tid: string, currentName: string) => {
    setModal({ kind: "renameThread", threadId: tid, currentName });
  }, []);
  const onDeleteThread = useCallback((tid: string, name: string) => {
    setModal({ kind: "confirmDeleteThread", threadId: tid, threadName: name });
  }, []);

  // Modal submit handlers
  const submitNewThread = useCallback(async (name: string) => {
    setModal({ kind: "none" });
    setErr(null);
    try {
      const p = await apiFetch<{ thread_id: string }>("/api/projects", {
        method: "POST", body: JSON.stringify({ name }),
      });
      await refreshThreadList();
      switchThread(p.thread_id);
    } catch (e) {
      setErr(`開新專案失敗：${(e as Error).message}`);
    }
  }, [refreshThreadList, switchThread]);

  const submitRenameThread = useCallback(async (tid: string, newName: string) => {
    setModal({ kind: "none" });
    setErr(null);
    try {
      await apiFetch(`/api/projects/${tid}`, {
        method: "PATCH", body: JSON.stringify({ name: newName }),
      });
      await refreshThreadList();
    } catch (e) {
      setErr(`重新命名失敗：${(e as Error).message}`);
    }
  }, [refreshThreadList]);

  const submitDeleteThread = useCallback(async (tid: string) => {
    setModal({ kind: "none" });
    setErr(null);
    try {
      await apiFetch(`/api/projects/${tid}`, { method: "DELETE" });
      const remaining = await refreshThreadList();
      // 如果刪掉的是當前 thread → 切到剩下的第一個；沒剩下就清空（bootstrap 會建新）
      if (tid === thread) {
        if (remaining.length > 0) {
          switchThread(remaining[0].thread_id);
        } else {
          window.localStorage.removeItem("lodestar.thread");
          setThread(null);
          resetThreadDerivedState();
          // 重跑 bootstrap 邏輯：建新 thread
          try {
            const p = await apiFetch<{ thread_id: string }>("/api/projects", {
              method: "POST", body: JSON.stringify({ name: "新需求" }),
            });
            window.localStorage.setItem("lodestar.thread", p.thread_id);
            setThread(p.thread_id);
            await refreshThreadList();
          } catch (e) {
            setErr(`建立替代 thread 失敗：${(e as Error).message}`);
          }
        }
      }
    } catch (e) {
      setErr(`刪除失敗：${(e as Error).message}`);
    }
  }, [thread, refreshThreadList, switchThread, resetThreadDerivedState]);

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

  // M2.3：拿 architecture + stories state
  const refreshArchitecture = useCallback(async (tid: string) => {
    try {
      const s = await apiFetch<{ artifact: string; status: string }>(`/api/stage/architecture/${tid}`);
      setArchArtifact(s.artifact || "");
      setArchStatus(s.status);
    } catch (e) {
      // 不致命：thread 切換瞬間可能 404，silent retry by next thread change
      console.warn("讀取 architecture 失敗：", (e as Error).message);
    }
  }, []);

  const refreshStories = useCallback(async (tid: string) => {
    try {
      const s = await apiFetch<{ artifact: string; status: string; delivery: DeliveryStatus | null }>(`/api/stage/stories/${tid}`);
      setStoriesArtifact(s.artifact || "");
      setStoriesStatus(s.status);
      setStoriesDelivery(s.delivery ?? null);
    } catch (e) {
      console.warn("讀取 stories 失敗：", (e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (!thread) return;
    refreshPrd(thread);
    refreshArchitecture(thread);
    refreshStories(thread);
  }, [thread, refreshPrd, refreshArchitecture, refreshStories]);

  // M3：切 thread workflow（hoisted 到 refreshXxx 之後避免 hoisting issue）
  const onChangeProjectWorkflow = useCallback(async (workflowId: string | null) => {
    if (!thread) return;
    setErr(null);
    try {
      await setProjectWorkflow(thread, workflowId);
      setCurrentProjectWorkflowId(workflowId);
      // 切後分頁對齊交給 activeStages 的 effect 處理（currentProjectWorkflowId 變 → 自動跳第一個 stage）
      // 切後重 fetch 三 stage state
      resetThreadDerivedState();
      refreshPrd(thread);
      refreshArchitecture(thread);
      refreshStories(thread);
    } catch (e) {
      setErr(`切換 workflow 失敗：${(e as Error).message}`);
    }
  }, [thread, resetThreadDerivedState, refreshPrd, refreshArchitecture, refreshStories]);

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
        body: JSON.stringify({ thread_id: thread, model_choice: modelChoice }),
      });
      setPrdArtifact(data.artifact || "");
      setPrdStatus("draft");
      // 上游 PRD 重生若改變 artifact，後端會把已核准的下游標 needs_revision；
      // 同步重抓 architecture / stories 讓 UI 反映（與 onGenerateArch / submitRefine 一致）。
      refreshArchitecture(thread);
      refreshStories(thread);
    } catch (e) {
      setErr(`生成 PRD 失敗：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [thread, busy, modelChoice, refreshArchitecture, refreshStories]);

  // ===== M2.3：Architecture / Stories handlers（與 PRD 對稱）=====
  const onGenerateArch = useCallback(async () => {
    if (!thread || archBusy) return;
    setErr(null);
    setArchBusy("generate");
    try {
      const data = await apiFetch<{ artifact: string }>("/api/stage/architecture/generate", {
        method: "POST",
        body: JSON.stringify({ thread_id: thread, model_choice: modelChoice }),
      });
      setArchArtifact(data.artifact || "");
      setArchStatus("draft");
      // 上游若 reset 下游：下游 stories 變 needs_revision
      refreshStories(thread);
    } catch (e) {
      setErr(`生成架構失敗：${(e as Error).message}`);
    } finally {
      setArchBusy(false);
    }
  }, [thread, archBusy, modelChoice, refreshStories]);

  const onGenerateStories = useCallback(async () => {
    if (!thread || storiesBusy) return;
    setErr(null);
    setStoriesBusy("generate");
    try {
      const data = await apiFetch<{ artifact: string }>("/api/stage/stories/generate", {
        method: "POST",
        body: JSON.stringify({ thread_id: thread, model_choice: modelChoice }),
      });
      setStoriesArtifact(data.artifact || "");
      setStoriesStatus("draft");
    } catch (e) {
      setErr(`生成使用者故事失敗：${(e as Error).message}`);
    } finally {
      setStoriesBusy(false);
    }
  }, [thread, storiesBusy, modelChoice]);

  // 統一 refine：開 modal 帶 stageId
  const onRefine = useCallback((stageId: StageRefineKind = "prd") => {
    if (!thread) return;
    // busy check 依 stage
    const isBusy = stageId === "prd" ? !!busy
      : stageId === "architecture" ? !!archBusy
      : !!storiesBusy;
    if (isBusy) return;
    setModal({ kind: "refineStage", stageId });
  }, [thread, busy, archBusy, storiesBusy]);

  // 統一 submit refine：根據 modal.stageId 派到對應 endpoint + state setter
  const submitRefine = useCallback(async (instruction: string) => {
    if (modal.kind !== "refineStage") return;
    const stageId = modal.stageId;
    setModal({ kind: "none" });
    if (!thread) return;
    setErr(null);

    const setBusyFor: Record<StageRefineKind, (b: StageBusy) => void> = {
      prd: setBusy as (b: StageBusy) => void,
      architecture: setArchBusy,
      stories: setStoriesBusy,
    };
    const setArtFor: Record<StageRefineKind, (s: string) => void> = {
      prd: setPrdArtifact, architecture: setArchArtifact, stories: setStoriesArtifact,
    };
    const setStatusFor: Record<StageRefineKind, (s: string) => void> = {
      prd: setPrdStatus, architecture: setArchStatus, stories: setStoriesStatus,
    };

    setBusyFor[stageId]("refine");
    try {
      const data = await apiFetch<{ artifact: string }>(`/api/stage/${stageId}/refine`, {
        method: "POST",
        body: JSON.stringify({ thread_id: thread, model_choice: modelChoice, instruction }),
      });
      setArtFor[stageId](data.artifact || "");
      setStatusFor[stageId]("draft");
      // refine 上游 → 下游 reset
      if (stageId === "prd") {
        refreshArchitecture(thread);
        refreshStories(thread);
      } else if (stageId === "architecture") {
        refreshStories(thread);
      }
    } catch (e) {
      setErr(`修訂 ${STAGE_LABEL[stageId]} 失敗：${(e as Error).message}`);
    } finally {
      setBusyFor[stageId](false);
    }
  }, [modal, thread, modelChoice, refreshArchitecture, refreshStories]);

  // 統一 approve：根據 stageId 派
  const approveStage = useCallback(async (stageId: StageRefineKind) => {
    if (!thread) return;
    const art = stageId === "prd" ? prdArtifact : stageId === "architecture" ? archArtifact : storiesArtifact;
    if (!art.trim()) return;
    setErr(null);
    try {
      const r = await apiFetch<{ status: string }>(`/api/stage/${stageId}/${thread}/approve`, {
        method: "POST",
      });
      if (stageId === "prd") setPrdStatus(r.status);
      else if (stageId === "architecture") setArchStatus(r.status);
      else setStoriesStatus(r.status);
    } catch (e) {
      setErr(`核准 ${STAGE_LABEL[stageId]} 失敗：${(e as Error).message}`);
    }
  }, [thread, prdArtifact, archArtifact, storiesArtifact]);

  const onApprovePrd = useCallback(() => approveStage("prd"), [approveStage]);
  const onApproveArch = useCallback(() => approveStage("architecture"), [approveStage]);
  const onApproveStories = useCallback(() => approveStage("stories"), [approveStage]);

  // Stage-specific refine wrappers — workspace 點按鈕時直接呼叫
  const onRefinePrd = useCallback(() => onRefine("prd"), [onRefine]);
  const onRefineArch = useCallback(() => onRefine("architecture"), [onRefine]);
  const onRefineStories = useCallback(() => onRefine("stories"), [onRefine]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDocFs(false);
      // ⌘N / Ctrl+N → 開新專案（與 sidebar 按鈕標籤一致）
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        onNewThread();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onNewThread]);

  // 響應式最小防護：mount 後若為窄螢幕（<1024）先收合；之後只在「跨越斷點變窄」時自動收合，
  // 不自動展開 → 尊重使用者在窄螢幕手動展開的選擇。window 僅在 effect 內讀取（SSR-safe）。
  useEffect(() => {
    const BP = 1024;
    if (window.innerWidth < BP) setSidebarOpen(false);
    let wasWide = window.innerWidth >= BP;
    const onResize = () => {
      const isWide = window.innerWidth >= BP;
      if (wasWide && !isWide) setSidebarOpen(false);
      wasWide = isWide;
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const showSidebar = nav === "workspace";

  return (
    <>
      <div className="relative z-10 flex h-full flex-col overflow-hidden">
        <TopBar
          nav={nav}
          onNav={setNav}
          thread={thread}
          pluginCount={pluginCount}
          modelChoice={modelChoice}
          modelList={modelList}
          onSelectModel={onSelectModel}
          onOpenIntegrations={() => setIntegrationsOpen(true)}
        />
        {err && (
          <div className="border-b border-[color-mix(in_oklab,#f59e0b_40%,transparent)] bg-[color-mix(in_oklab,#f59e0b_12%,transparent)] px-6 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[#f59e0b]">
            ⚠ {err}
            <button onClick={() => setErr(null)} className="ml-3 underline">關閉</button>
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          {showSidebar && (
            <Sidebar
              open={sidebarOpen}
              onToggle={() => setSidebarOpen((o) => !o)}
              threadList={threadList}
              activeThread={thread}
              onSelectThread={switchThread}
              onNewThread={onNewThread}
              onRenameThread={onRenameThread}
              onDeleteThread={onDeleteThread}
              onConfigureDelivery={onConfigureDelivery}
            />
          )}
          <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
            {/* workspace 切頁籤時不卸載、只用 CSS 隱藏 —— 保住 ChatPanel 的本地 state（草稿、進行中對話、問卷選項） */}
            <div className={nav === "workspace" ? "contents" : "hidden"}>
              {isRcaThread && thread ? (
              <RcaWorkspace
                thread={thread}
                workflowId={currentProjectWorkflowId}
                workflows={workflowList}
                modelChoice={modelChoice}
                onChangeWorkflow={onChangeProjectWorkflow}
                onError={(m) => setErr(m)}
                threadName={threadList.find((p) => p.thread_id === thread)?.name ?? null}
              />
            ) : (
              <>
                <StageHeader
                  selected={selected}
                  onSelect={setSelected}
                  prdStatus={prdStatus}
                  archStatus={archStatus}
                  storiesStatus={storiesStatus}
                  threadName={threadList.find((p) => p.thread_id === thread)?.name ?? null}
                  workflows={workflowList}
                  currentWorkflowId={currentProjectWorkflowId}
                  onChangeWorkflow={onChangeProjectWorkflow}
                  stages={activeStages}
                  stageMeta={stageMeta}
                  workflowSource={workflowSource}
                />
                <div className="flex min-h-0 flex-1">
                  {selected === "prd" && (
                    <PrdWorkspace
                      onOpenFs={() => setDocFs(true)}
                      thread={thread}
                      artifact={prdArtifact}
                      status={prdStatus}
                      busy={busy}
                      onGenerate={onGenerate}
                      onRefine={onRefinePrd}
                      onApprove={onApprovePrd}
                      attachments={attachments}
                      uploading={uploading}
                      onUploadAttachment={onUploadAttachment}
                      onDeleteAttachment={onDeleteAttachment}
                      modelChoice={modelChoice}
                      onChatArtifact={setPrdArtifact}
                    />
                  )}
                  {selected === "architecture" && (
                    <ArchWorkspace
                      thread={thread}
                      artifact={archArtifact}
                      status={archStatus}
                      busy={archBusy}
                      prdReady={prdArtifact.trim().length > 0}
                      onGenerate={onGenerateArch}
                      onRefine={onRefineArch}
                      onApprove={onApproveArch}
                      modelChoice={modelChoice}
                      onChatArtifact={setArchArtifact}
                    />
                  )}
                  {selected === "stories" && (
                    <StoriesWorkspace
                      thread={thread}
                      artifact={storiesArtifact}
                      status={storiesStatus}
                      busy={storiesBusy}
                      delivery={storiesDelivery}
                      archReady={archArtifact.trim().length > 0}
                      onGenerate={onGenerateStories}
                      onRefine={onRefineStories}
                      onApprove={onApproveStories}
                      onPublish={() => setPublishOpen(true)}
                    />
                  )}
                  {selected === "implement"    && (
                    <ImplementWorkspace
                      thread={thread}
                      storiesArtifact={storiesArtifact}
                      storiesApproved={storiesStatus === "approved"}
                      delivery={storiesDelivery}
                      onSetError={(m) => setErr(m)}
                    />
                  )}
                </div>
              </>
              )}
            </div>
            {nav === "workflows" && (
              <WorkflowsView
                workflows={workflowList}
                agents={agentList}
                availableStages={catalogStageIds.length ? catalogStageIds : ["prd", "architecture", "stories", "implement"]}
                stageInfo={stageInfo}
                onRefresh={refreshWorkflows}
                onDelete={(wf) => setDeleteConfirm({ kind: "workflow", id: wf.id, name: wf.label })}
                onSetError={(m) => setErr(m)}
              />
            )}
            {nav === "agents" && (
              <AgentsView
                agents={agentList}
                onNew={() => setAgentEditor({ open: true, initial: null })}
                onEdit={(a) => setAgentEditor({ open: true, initial: a })}
                onDelete={(a) => setDeleteConfirm({ kind: "agent", id: a.agent_id, name: a.name })}
              />
            )}
            {nav === "skills" && (
              <SkillsView
                skills={skillList}
                onNew={() => setSkillEditor({ open: true, initial: null })}
                onEdit={(s) => setSkillEditor({ open: true, initial: s })}
                onDelete={(s) => setDeleteConfirm({ kind: "skill", id: s.skill_id, name: s.name })}
              />
            )}
            {nav === "plugins"   && <PluginsView plugins={pluginList} onToggle={onTogglePlugin} />}
            {/* BuildSeal 只在無 ChatPanel 的 view 顯示，避免跟 footer 的「↵ send · ⌘↵ refine」overlap */}
            <BuildSeal visible={nav !== "workspace"} />
          </main>
        </div>
      </div>
      {docFs && <DocFullscreen onClose={() => setDocFs(false)} prdArtifact={prdArtifact} />}

      {/* ========== Dialogs（取代 window.prompt / window.confirm）========== */}
      <PromptDialog
        open={modal.kind === "newThread"}
        title="開新專案"
        subtitle="POST /api/projects"
        label="專案名稱"
        placeholder="例：電商結帳重構"
        defaultValue="新需求"
        onSubmit={submitNewThread}
        onCancel={() => setModal({ kind: "none" })}
      />
      <PromptDialog
        open={modal.kind === "renameThread"}
        title="重新命名專案"
        subtitle={modal.kind === "renameThread" ? `PATCH /api/projects/${modal.threadId}` : ""}
        label="新名稱"
        defaultValue={modal.kind === "renameThread" ? modal.currentName : ""}
        onSubmit={(name) => modal.kind === "renameThread" && submitRenameThread(modal.threadId, name)}
        onCancel={() => setModal({ kind: "none" })}
      />
      <ConfirmDialog
        open={modal.kind === "confirmDeleteThread"}
        destructive
        title="刪除專案？"
        subtitle="此動作無法復原"
        message={
          modal.kind === "confirmDeleteThread" ? (
            <>
              即將刪除「<span className="font-semibold text-[#e6ecf5]">{modal.threadName}</span>
              」與其 PRD / 架構 / 故事 artifact、對話、附件與遙測紀錄。
            </>
          ) : null
        }
        confirmLabel="刪除"
        onConfirm={() => modal.kind === "confirmDeleteThread" && submitDeleteThread(modal.threadId)}
        onCancel={() => setModal({ kind: "none" })}
      />
      <PromptDialog
        open={modal.kind === "refineStage"}
        title={modal.kind === "refineStage" ? `修訂 ${STAGE_LABEL[modal.stageId]}` : ""}
        subtitle={modal.kind === "refineStage" ? `POST /api/stage/${modal.stageId}/refine` : ""}
        label="修訂指令"
        placeholder={modal.kind === "refineStage" ? STAGE_REFINE_PLACEHOLDER[modal.stageId] : ""}
        multiline
        submitLabel="送出修訂"
        onSubmit={submitRefine}
        onCancel={() => setModal({ kind: "none" })}
      />
      {/* M2.5：Stories → GitHub / Jira / GitLab publish */}
      <PublishModal
        open={publishOpen}
        thread={thread}
        apiBase={API_BASE}
        onClose={() => { setPublishOpen(false); if (thread) refreshStories(thread); }}
      />
      <IntegrationsModal
        open={integrationsOpen}
        apiBase={API_BASE}
        onClose={() => setIntegrationsOpen(false)}
      />
      <ProjectDeliveryModal
        open={projectModal.open}
        thread={projectModal.thread}
        apiBase={API_BASE}
        onClose={() => setProjectModal({ open: false, thread: null })}
        onSaved={(tid) => { void refreshThreadList(); switchThread(tid); }}
        onOpenIntegrations={() => { setProjectModal({ open: false, thread: null }); setIntegrationsOpen(true); }}
      />

      {/* M3：Agent editor modal（new / edit user agent）*/}
      <AgentEditorModal
        open={agentEditor.open}
        initial={agentEditor.initial}
        allSkills={skillList}
        onSubmit={onSaveAgent}
        onCancel={() => setAgentEditor({ open: false, initial: null })}
      />

      {/* Skill editor modal（new / edit user skill）*/}
      <SkillEditorModal
        open={skillEditor.open}
        initial={skillEditor.initial}
        onSubmit={onSaveSkill}
        onCancel={() => setSkillEditor({ open: false, initial: null })}
      />

      {/* M3：刪除 workflow / agent 確認 */}
      <ConfirmDialog
        open={deleteConfirm !== null}
        destructive
        title={
          deleteConfirm?.kind === "workflow" ? "刪除 workflow？"
            : deleteConfirm?.kind === "skill" ? "刪除 skill？"
              : "刪除 agent？"
        }
        subtitle="此動作無法復原"
        message={
          deleteConfirm ? (
            <>
              即將刪除「<span className="font-semibold text-[#e6ecf5]">{deleteConfirm.name}</span>
              」（<code className="font-[family-name:var(--font-mono)] text-[var(--polaris)]">{deleteConfirm.id}</code>）。
            </>
          ) : null
        }
        confirmLabel="刪除"
        onConfirm={onDeleteConfirmed}
        onCancel={() => setDeleteConfirm(null)}
      />
    </>
  );
}

// ============================== TopBar ==============================
function TopBar({ nav, onNav, thread, pluginCount, modelChoice, modelList, onSelectModel, onOpenIntegrations }: {
  nav: string;
  onNav: (n: string) => void;
  thread: string | null;
  pluginCount: number | null;
  modelChoice: string;
  modelList: ModelAdapterInfo[];
  onSelectModel: (choice: string) => void;
  onOpenIntegrations: () => void;
}) {
  return (
    <header className="rise-1 relative z-50 flex h-14 shrink-0 items-center justify-between border-b border-[var(--rule-dark)] px-6">
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
        <ModelSelector value={modelChoice} options={modelList} onChange={onSelectModel} />
        <div className="h-3 w-px bg-[var(--rule-dark)]" />
        <div className="flex items-center gap-2">
          <span className="glow-approved relative inline-block h-1.5 w-1.5 rounded-full bg-[var(--approved)]" />
          <span className="text-[#b8c0cf]">{pluginCount ?? "—"}</span>
          <span className="text-[var(--ink-muted)]">PLUGINS LOADED</span>
        </div>
        <div className="h-3 w-px bg-[var(--rule-dark)]" />
        <button
          onClick={onOpenIntegrations}
          title="Integrations 憑證設定（GitHub PAT 等）"
          className="font-[family-name:var(--font-mono)] text-[11px] tracking-[0.18em] text-[var(--ink-muted)] transition hover:text-[#cdd4df]"
        >
          ⚙ INTEGRATIONS
        </button>
      </div>
    </header>
  );
}

// ============================== ModelSelector（TopBar popover）==============================
function ModelSelector({ value, options, onChange }: {
  value: string;
  options: ModelAdapterInfo[];
  onChange: (choice: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // 點 popover 外面收起來
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const currentMeta = options.find((m) => m.model_choice === value);
  const hasOptions = options.length > 0;

  return (
    <div ref={wrapRef} className="relative flex items-center gap-1.5">
      <span className="text-[var(--ink-muted)]">MODEL</span>
      <button
        type="button"
        onClick={() => hasOptions && setOpen((o) => !o)}
        disabled={!hasOptions}
        className="flex items-center gap-1 border border-transparent px-1 text-[#b8c0cf] transition hover:border-[var(--rule-dark)] hover:bg-[var(--bg-elev)] disabled:opacity-60"
      >
        <span>{value}</span>
        {currentMeta && !currentMeta.is_available && (
          <span title="adapter 自報 unavailable（如 cli 不在 PATH）" className="text-[#f59e0b]">⚠</span>
        )}
        <svg viewBox="0 0 10 10" width="9" height="9" className={`transition ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="1.4">
          <path d="M2 4 L5 7 L8 4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && hasOptions && (
        <div className="absolute right-0 top-[calc(100%+8px)] z-40 min-w-[280px] border border-[var(--paper-edge)] bg-[var(--paper)] shadow-anvil">
          <div className="border-b border-[var(--rule)] px-3 py-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            選擇 model adapter
          </div>
          <ul className="max-h-[60vh] overflow-y-auto">
            {options.map((m) => {
              const selected = m.model_choice === value;
              return (
                <li key={m.model_choice}>
                  <button
                    type="button"
                    onClick={() => { onChange(m.model_choice); setOpen(false); }}
                    className={`group flex w-full items-start gap-2.5 border-l-2 px-3 py-2.5 text-left transition ${
                      selected
                        ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)]"
                        : "border-transparent hover:bg-[var(--bg-elev)] hover:border-[#404a5b]"
                    }`}
                  >
                    <span className={`mt-0.5 grid h-3 w-3 shrink-0 place-items-center border ${
                      selected ? "border-[var(--polaris)] bg-[var(--polaris)]" : "border-[var(--rule-dark)]"
                    }`}>
                      {selected && <span className="text-[8px] leading-none text-white">●</span>}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <code className={`font-[family-name:var(--font-mono)] text-[12px] ${selected ? "text-[var(--polaris)]" : "text-[#e6ecf5]"}`}>
                          {m.model_choice}
                        </code>
                        {!m.is_available && (
                          <span className="border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-1.5 py-px font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[#f59e0b]">
                            unavailable
                          </span>
                        )}
                        {m.supports_multimodal && (
                          <span className="border border-[var(--polaris-dim)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] px-1.5 py-px font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[var(--polaris)]">
                            multimodal
                          </span>
                        )}
                      </div>
                      <div className="mt-1 font-[family-name:var(--font-sans)] text-[11.5px] leading-[1.5] text-[#97a0b3]">
                        {m.description || "—"}
                      </div>
                      <div className="mt-1 font-[family-name:var(--font-mono)] text-[9.5px] uppercase tracking-wider text-[var(--ink-muted)]">
                        ctx {m.max_context_tokens.toLocaleString()} ·
                        {" "}prompt {m.prompt_budget_tokens.toLocaleString()} ·
                        {" "}reply {m.response_budget_tokens.toLocaleString()}
                        {m.source_plugin && <> · {m.source_plugin}</>}
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
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
function Sidebar({
  open, onToggle, threadList, activeThread,
  onSelectThread, onNewThread, onRenameThread, onDeleteThread, onConfigureDelivery,
}: {
  open: boolean;
  onToggle: () => void;
  threadList: Project[];
  activeThread: string | null;
  onSelectThread: (tid: string) => void;
  onNewThread: () => void;
  onRenameThread: (tid: string, currentName: string) => void;
  onDeleteThread: (tid: string, name: string) => void;
  onConfigureDelivery: (tid: string) => void;
}) {
  if (!open) {
    return (
      <aside className="rise-2 flex w-14 shrink-0 flex-col items-center border-r border-[var(--rule-dark)] bg-[var(--bg-elev)]/40 py-3">
        <button onClick={onToggle} title="展開側欄"
          className="mb-3 grid h-7 w-7 place-items-center text-[var(--ink-muted)] transition hover:text-[#b8c0cf]">
          <ChevronDouble dir="right" />
        </button>
        <div className="mb-3 h-px w-6 bg-[var(--rule-dark)]" />
        {threadList.map((p) => {
          const active = p.thread_id === activeThread;
          return (
            <button
              key={p.thread_id}
              title={p.name}
              onClick={() => onSelectThread(p.thread_id)}
              className={`mb-1.5 grid h-9 w-9 place-items-center border font-[family-name:var(--font-display)] text-[15px] transition ${
                active
                  ? "glow-star border-[var(--polaris)] text-[var(--polaris)]"
                  : "border-[var(--rule-dark)] text-[#7a8499] hover:border-[#404a5b] hover:text-[#b8c0cf]"
              }`}
            >
              {projectGlyph(p)}
            </button>
          );
        })}
        <button
          onClick={onNewThread}
          title="開新專案"
          className="mt-2 grid h-9 w-9 place-items-center border border-dashed border-[var(--rule-dark)] font-[family-name:var(--font-display)] text-[18px] leading-none text-[#5e6878] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]"
        >
          ＋
        </button>
      </aside>
    );
  }
  return (
    <aside className="rise-2 flex w-72 shrink-0 flex-col border-r border-[var(--rule-dark)] bg-[var(--bg-elev)]/40">
      <div className="flex items-center justify-between border-b border-[var(--rule-dark)] px-5 py-3.5">
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          / threads · {threadList.length}
        </span>
        <button onClick={onToggle} title="收合側欄"
          className="grid h-6 w-6 place-items-center text-[var(--ink-muted)] transition hover:text-[#b8c0cf]">
          <ChevronDouble dir="left" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {threadList.length === 0 && (
          <div className="px-5 py-6 text-center font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            尚無專案
          </div>
        )}
        {threadList.map((p) => (
          <ThreadRow
            key={p.thread_id}
            project={p}
            active={p.thread_id === activeThread}
            onSelect={() => onSelectThread(p.thread_id)}
            onRename={() => onRenameThread(p.thread_id, p.name)}
            onDelete={() => onDeleteThread(p.thread_id, p.name)}
            onConfigure={() => onConfigureDelivery(p.thread_id)}
          />
        ))}
      </div>
      <button
        onClick={onNewThread}
        className="m-3 flex items-center justify-between border border-dashed border-[var(--rule-dark)] px-4 py-2.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[#5e6878] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]"
      >
        <span>＋ new thread</span>
        <span className="text-[var(--ink-muted)]">⌘N</span>
      </button>
    </aside>
  );
}

function ThreadRow({ project, active, onSelect, onRename, onDelete, onConfigure }: {
  project: Project;
  active: boolean;
  onSelect: () => void;
  onRename: () => void;
  onDelete: () => void;
  onConfigure: () => void;
}) {
  return (
    <div
      className={`group relative flex w-full items-center gap-3 px-5 py-3 transition ${
        active ? "bg-[var(--anvil)]/60" : "hover:bg-[var(--bg-elev)]"
      }`}
    >
      {active && <span className="absolute top-3 bottom-3 left-0 w-[3px] bg-[var(--polaris)]" />}
      <button
        onClick={onSelect}
        className="flex min-w-0 flex-1 items-center gap-3 text-left"
        title={`切換到「${project.name}」`}
      >
        <span
          className={`grid h-9 w-9 shrink-0 place-items-center border font-[family-name:var(--font-display)] text-[15px] ${
            active
              ? "border-[var(--polaris)] text-[var(--polaris)]"
              : "border-[var(--rule-dark)] text-[#7a8499] group-hover:border-[#404a5b]"
          }`}
        >
          {projectGlyph(project)}
        </span>
        <div className="min-w-0 flex-1">
          <div className={`truncate text-[13px] ${active ? "text-[#e6ecf5]" : "text-[#97a0b3]"}`}>
            {project.name}
          </div>
          <div className="mt-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
            {project.workflow_id ?? "default"}
          </div>
        </div>
      </button>
      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100">
        <button
          onClick={(e) => { e.stopPropagation(); onConfigure(); }}
          title="專案設定（delivery repo）"
          className="grid h-6 w-6 place-items-center font-[family-name:var(--font-mono)] text-[12px] text-[var(--ink-muted)] transition hover:text-[var(--polaris)]"
        >
          ⚙
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onRename(); }}
          title="重新命名"
          className="grid h-6 w-6 place-items-center text-[var(--ink-muted)] transition hover:text-[var(--polaris)]"
        >
          <RenameIcon />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          title="刪除專案"
          className="grid h-6 w-6 place-items-center text-[var(--ink-muted)] transition hover:text-[#f47171]"
        >
          <TrashIcon />
        </button>
      </div>
    </div>
  );
}

function RenameIcon() {
  return (
    <svg viewBox="0 0 14 14" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 10.5 L2 12 L3.5 12 L10.5 5 L9 3.5 Z" />
      <path d="M8.5 4 L10 5.5" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 14 14" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4 L11 4" />
      <path d="M5 4 L5 3 L9 3 L9 4" />
      <path d="M4 4 L4.5 12 L9.5 12 L10 4" />
      <path d="M6 6.5 L6 10" />
      <path d="M8 6.5 L8 10" />
    </svg>
  );
}

// ============================== Stage header ==============================
function WorkflowSwitcher({ workflows, currentId, onChange }: {
  workflows: Workflow[];
  currentId: string | null;
  onChange: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  // currentId 為 null → effective workflow 是 "default"（lazy fallback）
  // 缺口6：區分 null（未綁定 → lazy fallback default）vs 真的綁了某個 workflow
  const isUnbound = currentId === null;
  const effectiveId = currentId ?? "default";
  return (
    <div ref={ref} className="relative inline-block">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 border border-transparent px-1 text-[var(--polaris)] transition hover:border-[var(--rule-dark)] hover:bg-[var(--bg-elev)]"
      >
        <span>{effectiveId}</span>
        {isUnbound && (
          <span className="font-[family-name:var(--font-mono)] text-[8px] uppercase tracking-wider text-[var(--ink-muted)]">(auto)</span>
        )}
        <svg viewBox="0 0 10 10" width="9" height="9" className={`transition ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="1.4">
          <path d="M2 4 L5 7 L8 4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="absolute left-0 top-[calc(100%+6px)] z-40 min-w-[280px] border border-[var(--paper-edge)] bg-[var(--paper)] shadow-anvil">
          <div className="border-b border-[var(--rule)] px-3 py-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            切換 thread 的 workflow
          </div>
          <ul className="max-h-[50vh] overflow-y-auto">
            {/* 解除綁定 → lazy default */}
            <li>
              <button
                onClick={() => { onChange(null); setOpen(false); }}
                className={`flex w-full items-baseline gap-2 border-l-2 px-3 py-2 text-left transition ${
                  isUnbound
                    ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)]"
                    : "border-transparent hover:bg-[var(--bg-elev)] hover:border-[#404a5b]"
                }`}
              >
                <code className={`font-[family-name:var(--font-mono)] text-[11.5px] ${isUnbound ? "text-[var(--polaris)]" : "text-[#e6ecf5]"}`}>(auto)</code>
                <span className="font-[family-name:var(--font-sans)] text-[11.5px] text-[#cdd4df]">未綁定 · 自動用 default</span>
                {isUnbound && <span className="ml-auto font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[var(--polaris)]">current</span>}
              </button>
            </li>
            <li className="border-t border-[var(--rule-dark)]" />
            {workflows.length === 0 && (
              <li className="px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">loading…</li>
            )}
            {workflows.map((w) => {
              // 只有「明確綁定」才高亮（isUnbound 時即使 effectiveId==default 也不在此高亮）
              const selected = !isUnbound && w.id === effectiveId;
              return (
                <li key={w.id}>
                  <button
                    onClick={() => { onChange(w.id); setOpen(false); }}
                    className={`flex w-full items-baseline gap-2 border-l-2 px-3 py-2 text-left transition ${
                      selected
                        ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)]"
                        : "border-transparent hover:bg-[var(--bg-elev)] hover:border-[#404a5b]"
                    }`}
                  >
                    <code className={`font-[family-name:var(--font-mono)] text-[11.5px] ${selected ? "text-[var(--polaris)]" : "text-[#e6ecf5]"}`}>
                      {w.id}
                    </code>
                    <span className="font-[family-name:var(--font-sans)] text-[11.5px] text-[#cdd4df]">{w.label}</span>
                    <span className="ml-auto font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[var(--ink-muted)]">
                      {w.source} · {w.stages.length}st
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}


function StageHeader({
  selected, onSelect, prdStatus, archStatus, storiesStatus, threadName,
  workflows, currentWorkflowId, onChangeWorkflow,
  stages, stageMeta, workflowSource,
}: {
  selected: string;
  onSelect: (s: string) => void;
  prdStatus: string;            // M2 baseline：PRD 從真實 state 來
  archStatus: string;           // M2.3：架構從真實 state 來
  storiesStatus: string;        // M2.3：故事從真實 state 來
  threadName: string | null;    // 從 thread list 找對應名稱
  workflows: Workflow[];        // M3：thread workflow switcher
  currentWorkflowId: string | null;
  onChangeWorkflow: (workflowId: string | null) => void;
  stages: WorkflowStage[];      // M3：當前綁定 workflow 的 stages（有序）→ 圖表 catalog-driven
  stageMeta: Record<string, { label: string; description: string; icon: string }>;
  workflowSource: string;       // 來源標籤（source_plugin / source）
}) {
  // 已知 stage（prd/architecture/stories/implement）的精緻顯示 meta；未知 stage fallback catalog / id。
  const displayOf = (sid: string) => {
    const builtin = STAGES.find((s) => s.id === sid);
    const meta = stageMeta[sid];
    return {
      label: builtin?.label ?? meta?.label ?? sid,
      caption: builtin?.caption ?? meta?.description ?? "",
      agent: builtin?.agent ?? "",
    };
  };
  // 把後端 status 字串映成 StageStatus（draft/approved/needs_revision；其餘 fallback draft）
  const normalize = (s: string): StageStatus =>
    s === "approved" || s === "needs_revision" || s === "draft" ? s : "draft";

  const statusOf = (sid: string): StageStatus => {
    if (sid === "prd") return normalize(prdStatus);
    if (sid === "architecture") return normalize(archStatus);
    if (sid === "stories") return normalize(storiesStatus);
    // M5.3：implement 依賴 stories——stories 核准前 locked，核准後可進入（draft）
    if (sid === "implement") return storiesStatus === "approved" ? "draft" : "locked";
    return STAGES.find((s) => s.id === sid)?.status ?? "draft";
  };

  const badgeOf = (sid: string, status: StageStatus): string => {
    if (sid === "implement") return STAGES.find((s) => s.id === sid)?.badge ?? "";
    return status === "approved" ? "CHARTED"
      : status === "needs_revision" ? "REVISE"
      : "CHARTING";
  };

  return (
    // relative z-30：rise 動畫的 transform 讓本 header 與下方 workspace section 各自成 stacking context，
    // 且 workspace 是後續 sibling 會 paint 在上 → WorkflowSwitcher 下拉的下半部會被蓋住、點不到。
    // 抬升 header 的 stacking context 蓋過 workspace，下拉項目才全可點。
    <div className="rise-3 relative z-30 border-b border-[var(--rule-dark)] px-10 pt-6 pb-4">
      <div className="mb-3 flex items-center gap-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        <span>thread</span><span className="text-[#2a3041]">/</span>
        <span className="text-[#b8c0cf]">{threadName ?? "—"}</span><span className="text-[#2a3041]">·</span>
        <span>workflow</span><span className="text-[#2a3041]">/</span>
        <WorkflowSwitcher
          workflows={workflows}
          currentId={currentWorkflowId}
          onChange={onChangeWorkflow}
        />
        <span className="ml-auto">stages by <span className="text-[#b8c0cf]">{workflowSource}</span></span>
      </div>
      <h1 className="mb-5 font-[family-name:var(--font-display)] text-[32px] font-semibold leading-none tracking-tight text-[#e6ecf5]">
        Requirement <em className="font-[family-name:var(--font-display)] italic text-[var(--polaris)]">chart</em>
      </h1>
      <ol className="flex items-stretch">
        {stages.map((st, i) => {
          const sid = st.stage_id;
          const disp = displayOf(sid);
          const leadAgent = st.agent_bindings.find((b) => b.role === "lead")?.agent_id ?? st.agent_bindings[0]?.agent_id;
          const agentLabel = disp.agent || leadAgent || (st.collab_mode !== "single" ? st.collab_mode : "—");
          const status = statusOf(sid);
          const badge = badgeOf(sid, status);
          const isSelected = sid === selected;
          const isLocked = status === "locked";
          const topBorder = isSelected ? "border-t-[var(--polaris)]"
            : status === "approved" ? "border-t-[var(--approved)]"
            : status === "needs_revision" ? "border-t-[#f59e0b]"
            : isLocked ? "border-t-[var(--locked)]"
            : "border-t-[var(--rule-dark)]";
          const badgeColor = status === "approved" ? "text-[var(--approved)]"
            : status === "needs_revision" ? "text-[#f59e0b]"
            : isLocked ? "text-[var(--locked)]"
            : isSelected ? "text-[var(--polaris)]"
            : "text-[var(--ink-muted)]";
          return (
            <li key={sid} className="flex flex-1 items-stretch">
              <button disabled={isLocked} onClick={() => !isLocked && onSelect(sid)}
                className={`group relative w-full border-t-[2px] ${topBorder} py-3 pr-6 text-left transition ${isLocked ? "cursor-not-allowed opacity-55" : ""} ${isSelected ? "" : "hover:border-t-[#2e3441]"}`}>
                <div className="flex items-baseline gap-3">
                  <span className={`font-[family-name:var(--font-display)] text-[26px] font-semibold leading-none ${isLocked ? "text-[#404a5b]" : "text-[#e6ecf5]"}`}>{String(i + 1).padStart(2, "0")}</span>
                  <span className={`font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] ${badgeColor} ${status === "draft" && !isSelected ? "pulse-star" : ""}`}>{badge}</span>
                </div>
                <div className={`mt-1.5 font-[family-name:var(--font-display)] text-[16px] ${isLocked ? "text-[#5e6878]" : "text-[#cdd4df]"}`}>{disp.label}</div>
                <div className="mt-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{disp.caption} · {agentLabel}</div>
              </button>
              {i < stages.length - 1 && <div className="my-3 w-px self-stretch bg-[var(--rule-dark)]" />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ============================== PRD workspace (M1：真實 API) ==============================
function PrdWorkspace({
  onOpenFs, thread, artifact, status, busy, onGenerate, onRefine, onApprove,
  attachments, uploading, onUploadAttachment, onDeleteAttachment,
  modelChoice, onChatArtifact,
}: {
  onOpenFs: () => void;
  thread: string | null;
  artifact: string;
  status: string;
  busy: PrdBusy;
  onGenerate: () => void;
  onRefine: () => void;
  onApprove: () => void;
  attachments: AttachmentInfo[];
  uploading: boolean;
  onUploadAttachment: (f: File) => void;
  onDeleteAttachment: (fileId: string) => void;
  modelChoice: string;
  onChatArtifact: (content: string) => void;
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
          <ToolBtn primary onClick={onApprove} disabled={!hasContent || !!busy || isApproved}>
            {isApproved ? "已核准 ✓" : "核准"}
          </ToolBtn>
        </div>
      </section>
      {/* key=thread：切 thread 時整個 chat remount，重置 msgs/busy + 所有 QuestionnaireCard 的 sent/answers
          （否則 index-based key 會讓舊卡片的 sent=true 殘留，新 thread 的選項變 disabled 無法選） */}
      <ChatPanel key={thread ?? "none"} thread={thread} stageId="prd" stageLabel="SA Discovery"
        modelChoice={modelChoice} onArtifactUpdated={onChatArtifact} />
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
type StageBusyLike = false | "generate" | "refine" | "chat";

// 可縮放 / 拖曳 / 全螢幕的 Mermaid 圖：滾輪或 ＋/− 縮放、拖曳平移、⤢ 全螢幕（Esc 離開）。
function ZoomableDiagram({ code, idPrefix }: { code: string; idPrefix?: string }) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [fs, setFs] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const clamp = (n: number) => Math.min(5, Math.max(0.2, n));
  const reset = () => { setScale(1); setPos({ x: 0, y: 0 }); };

  // 滾輪縮放（非 passive 才能 preventDefault，避免連帶捲動頁面）
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => { e.preventDefault(); setScale((s) => clamp(s * (e.deltaY < 0 ? 1.12 : 1 / 1.12))); };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [fs]);

  // 全螢幕時 Esc 離開
  useEffect(() => {
    if (!fs) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFs(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fs]);

  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y }; setDragging(true); };
  const onMove = (e: React.MouseEvent) => {
    if (!drag.current) return;
    setPos({ x: drag.current.px + (e.clientX - drag.current.x), y: drag.current.py + (e.clientY - drag.current.y) });
  };
  const onUp = () => { drag.current = null; setDragging(false); };

  const btn = "grid h-7 w-7 place-items-center border border-[var(--rule-dark)] bg-[var(--bg)]/80 text-[13px] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]";
  const stage = (
    <div
      ref={wrapRef}
      onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
      className="relative h-full w-full overflow-hidden"
      style={{ cursor: dragging ? "grabbing" : "grab" }}
    >
      <div className="absolute right-3 top-3 z-10 flex items-center gap-1">
        <button className={btn} title="縮小" onClick={() => setScale((s) => clamp(s / 1.2))}>−</button>
        <button className="border border-[var(--rule-dark)] bg-[var(--bg)]/80 px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[10px] tabular-nums text-[var(--ink-muted)] transition hover:text-[var(--polaris)]" title="重置縮放/位置" onClick={reset}>{Math.round(scale * 100)}%</button>
        <button className={btn} title="放大" onClick={() => setScale((s) => clamp(s * 1.2))}>＋</button>
        <button className={btn} title={fs ? "離開全螢幕 (Esc)" : "全螢幕"} onClick={() => setFs((v) => !v)}>{fs ? "⤡" : "⤢"}</button>
      </div>
      <div
        className="grid h-full w-full place-items-center"
        style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})`, transformOrigin: "center", transition: dragging ? "none" : "transform 80ms" }}
      >
        <MermaidDiagram code={code} idPrefix={idPrefix} />
      </div>
    </div>
  );

  if (fs) return <div className="fixed inset-0 z-50 bg-[var(--bg)]/96 backdrop-blur-sm">{stage}</div>;
  return <div className="h-full w-full">{stage}</div>;
}

function ArchWorkspace({
  thread, artifact, status, busy, prdReady,
  onGenerate, onRefine, onApprove, modelChoice, onChatArtifact,
}: {
  thread: string | null;
  artifact: string;
  status: string;
  busy: StageBusyLike;
  prdReady: boolean;
  onGenerate: () => void;
  onRefine: () => void;
  onApprove: () => void;
  modelChoice: string;
  onChatArtifact: (content: string) => void;
}) {
  const parsed = useMemo(() => parseArchitecture(artifact || ""), [artifact]);
  const [view, setView] = useState<"document" | "diagram">("document");
  const [activeDiagram, setActiveDiagram] = useState(0);
  const hasContent = artifact.trim().length > 0;
  const isApproved = status === "approved";
  const needsRevision = status === "needs_revision";

  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="architecture" stage="design" op="generate_architecture" right={
          <>
            {hasContent && <ViewToggle value={view} onChange={setView} />}
            {hasContent && (isApproved ? <ApprovedSeal /> : <DraftPill />)}
            {needsRevision && (
              <span className="border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#f59e0b]">
                needs revision
              </span>
            )}
          </>
        } />
        <article className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)] text-[var(--ink)]">
          {hasContent ? (
            view === "document" ? (
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
                    <ZoomableDiagram code={parsed.mermaids[activeDiagram]} idPrefix={`arch-${activeDiagram}`} />
                  )}
                </div>
              </div>
            )
          ) : (
            <ArchEmptyState busy={busy} thread={thread} prdReady={prdReady} onGenerate={onGenerate} />
          )}
        </article>
        <BottomMeta
          left={
            hasContent ? (
              <>
                tier <span className="text-[var(--polaris)]">{parsed.tier ?? "—"}</span> ·{" "}
                {parsed.sections.length} sections · {parsed.mermaids.length} mermaid ·{" "}
                {artifact.length} chars · charted by software_architect
              </>
            ) : (
              <>empty · {prdReady ? "ready to chart" : "PRD 須先有內容"}</>
            )
          }
          right={<>thread <code className="text-[#cdd4df]">{thread ?? "(none)"}</code> · depends_on <span className="text-[#b8c0cf]">prd</span> · downstream <span className="text-[#b8c0cf]">stories</span></>}
        />
        <div className="mt-4 flex items-center justify-end gap-2">
          <ToolBtn onClick={onRefine} disabled={!thread || !hasContent || !!busy}>
            {busy === "refine" ? "Refining…" : "Refine…"}
          </ToolBtn>
          <ToolBtn onClick={onGenerate} disabled={!thread || !prdReady || !!busy}>
            {busy === "generate" ? "Charting…" : hasContent ? "重新生成" : "產生架構設計"}
          </ToolBtn>
          <ToolBtn primary onClick={onApprove} disabled={!hasContent || !!busy || isApproved}>
            {isApproved ? "已核准 ✓" : "核准架構"}
          </ToolBtn>
        </div>
      </section>
      {/* key=thread：同 PRD chat，切 thread 時 remount 重置 QuestionnaireCard 卡片狀態 */}
      <ChatPanel key={thread ?? "none"} thread={thread} stageId="architecture" stageLabel="Architecture Chat"
        modelChoice={modelChoice} onArtifactUpdated={onChatArtifact} />
    </div>
  );
}

function ArchEmptyState({ busy, thread, prdReady, onGenerate }: {
  busy: StageBusyLike;
  thread: string | null;
  prdReady: boolean;
  onGenerate: () => void;
}) {
  return (
    <div className="flex h-full items-start justify-center overflow-y-auto px-10 py-12">
      <div className="w-full max-w-xl text-center">
        <div className="mx-auto mb-5 grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">
          02
        </div>
        <h3 className="font-[family-name:var(--font-display)] text-[26px] font-semibold text-[#e6ecf5]">
          {prdReady ? "等待標繪架構" : "尚未具備上游需求"}
        </h3>
        <p className="mt-2 text-[13px] leading-6 text-[#7a8499]">
          {prdReady
            ? "PRD 已就緒，Architect agent 將依需求生成 tier 分級、tech stack、Mermaid 拓樸與 module layout。"
            : "需要先在 PRD 階段完成（至少有內容）才能生成下游架構設計。"}
        </p>
        <div className="mx-auto my-7 h-px w-24 bg-[var(--rule-dark)]" />
        <div className="mb-4 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          agent: software_architect · model: claude-cli
        </div>
        <button
          onClick={onGenerate}
          disabled={!thread || !prdReady || !!busy}
          className="border-2 border-[var(--polaris)] bg-[var(--polaris)] px-8 py-3 font-[family-name:var(--font-mono)] text-[12px] uppercase tracking-[0.22em] text-white transition hover:bg-[var(--polaris-hi)] disabled:cursor-not-allowed disabled:bg-transparent disabled:text-[var(--polaris)] disabled:opacity-50"
        >
          {busy === "generate" ? "★  charting…（60–120s）" : "✦  產生架構設計"}
        </button>
      </div>
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
// M2.3 wire 真實 API：接 /api/stage/stories/{thread}；empty state 顯示「架構未生成」提示。
function StoriesWorkspace({
  thread, artifact, status, busy, delivery, archReady,
  onGenerate, onRefine, onApprove, onPublish,
}: {
  thread: string | null;
  artifact: string;
  status: string;
  busy: StageBusyLike;
  delivery: DeliveryStatus | null;
  archReady: boolean;
  onGenerate: () => void;
  onRefine: () => void;
  onApprove: () => void;
  onPublish: () => void;
}) {
  const parsed = useMemo(() => parseStories(artifact || ""), [artifact]);
  const counts = useMemo(() => countStoriesAndEstimate(parsed.raw), [parsed.raw]);
  const allStories = useMemo(() => parsed.epics.flatMap((e) => e.stories.map((s) => ({ epicNum: e.num, story: s }))), [parsed.epics]);
  const initialPick = allStories[0]?.story.num ?? null;
  const [picked, setPicked] = useState<string | null>(initialPick);
  const [openEpics, setOpenEpics] = useState<Set<string>>(() => new Set(parsed.epics.slice(0, 3).map((e) => e.num)));

  // artifact 切換時，重置選中項到第一個（避免 stale picked 指向已不存在的 story）
  useEffect(() => { setPicked(initialPick); }, [initialPick]);

  const toggleEpic = (n: string) => setOpenEpics((prev) => {
    const next = new Set(prev);
    if (next.has(n)) next.delete(n); else next.add(n);
    return next;
  });

  const detail = picked
    ? allStories.find(({ story }) => story.num === picked)?.story ?? null
    : null;

  const hasContent = artifact.trim().length > 0;
  const isApproved = status === "approved";
  const needsRevision = status === "needs_revision";

  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="stories" stage="deliver" op="generate_user_stories" right={
          <>
            {hasContent && (isApproved ? <ApprovedSeal /> : <DraftPill />)}
            {needsRevision && (
              <span className="border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#f59e0b]">
                needs revision
              </span>
            )}
            {delivery && (
              <span
                title={`已發佈 ${delivery.created}/${delivery.count} 筆到 ${delivery.repo || delivery.target}`}
                className="border border-[var(--approved)]/40 bg-[color-mix(in_oklab,var(--approved)_10%,transparent)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--approved)]"
              >
                ✓ 已發佈 {delivery.created} 筆
              </span>
            )}
            <button
              onClick={onPublish}
              disabled={!hasContent}
              className="border border-[var(--rule-dark)] bg-[var(--bg-elev)] px-3 py-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {delivery ? "重新發佈…" : "發佈到 tracker…"}
            </button>
          </>
        } />
        {!hasContent ? (
          <article className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)] text-[var(--ink)]">
            <StoriesEmptyState busy={busy} thread={thread} archReady={archReady} onGenerate={onGenerate} />
          </article>
        ) : (
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
        )}
        <BottomMeta
          left={
            hasContent
              ? `${counts.stories} stories · ${counts.epics} epics · ${counts.hours.toFixed(1)} hrs · ${parsed.raw.length} chars`
              : <>empty · {archReady ? "ready to chart" : "需架構先有內容"}</>
          }
          right={<>thread <code className="text-[#cdd4df]">{thread ?? "(none)"}</code> · depends_on <span className="text-[#b8c0cf]">architecture</span> · publish to <span className="text-[#b8c0cf]">github / jira</span></>}
        />
        <div className="mt-4 flex items-center justify-end gap-2">
          <ToolBtn onClick={onRefine} disabled={!thread || !hasContent || !!busy}>
            {busy === "refine" ? "Refining…" : "Refine…"}
          </ToolBtn>
          <ToolBtn onClick={onGenerate} disabled={!thread || !archReady || !!busy}>
            {busy === "generate" ? "Drafting…" : hasContent ? "重新生成" : "產生使用者故事"}
          </ToolBtn>
          <ToolBtn onClick={onApprove} disabled={!hasContent || !!busy || isApproved}>
            {isApproved ? "已核准 ✓" : "核准故事"}
          </ToolBtn>
          <ToolBtn primary onClick={onPublish} disabled={!hasContent}>
            {delivery ? "重新發佈…" : "發佈到 tracker…"}
          </ToolBtn>
        </div>
      </section>
      <StoryDetail story={detail} epicNum={picked ? allStories.find(({ story }) => story.num === picked)?.epicNum ?? null : null} />
    </div>
  );
}

function StoriesEmptyState({ busy, thread, archReady, onGenerate }: {
  busy: StageBusyLike;
  thread: string | null;
  archReady: boolean;
  onGenerate: () => void;
}) {
  return (
    <div className="flex h-full items-start justify-center overflow-y-auto px-10 py-12">
      <div className="w-full max-w-xl text-center">
        <div className="mx-auto mb-5 grid h-14 w-14 place-items-center border-2 border-[var(--polaris)] font-[family-name:var(--font-display)] text-[22px] font-semibold text-[var(--polaris)]">
          03
        </div>
        <h3 className="font-[family-name:var(--font-display)] text-[26px] font-semibold text-[#e6ecf5]">
          {archReady ? "等待拆故事" : "上游架構尚未就緒"}
        </h3>
        <p className="mt-2 text-[13px] leading-6 text-[#7a8499]">
          {archReady
            ? "Product Owner agent 將依 PRD + 架構，按 tier 規則拆 Epic / Story（≤4h 一條），含 AC（Gherkin）/ 估點 / Requirement IDs。"
            : "需要先在架構階段完成（至少有內容）才能生成下游使用者故事。"}
        </p>
        <div className="mx-auto my-7 h-px w-24 bg-[var(--rule-dark)]" />
        <div className="mb-4 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
          agent: product_owner · model: claude-cli
        </div>
        <button
          onClick={onGenerate}
          disabled={!thread || !archReady || !!busy}
          className="border-2 border-[var(--polaris)] bg-[var(--polaris)] px-8 py-3 font-[family-name:var(--font-mono)] text-[12px] uppercase tracking-[0.22em] text-white transition hover:bg-[var(--polaris-hi)] disabled:cursor-not-allowed disabled:bg-transparent disabled:text-[var(--polaris)] disabled:opacity-50"
        >
          {busy === "generate" ? "★  drafting…（120–240s）" : "✦  產生使用者故事"}
        </button>
      </div>
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

// ============================== Implementation workspace（M5：async runner + fix-loop ≤3）==============================
function ImplStatusPill({ status }: { status: string }) {
  const map: Record<string, { c: "approved" | "chart" | "muted"; t: string }> = {
    pending: { c: "muted", t: "PENDING" },
    running: { c: "chart", t: "RUNNING" },
    succeeded: { c: "approved", t: "SUCCEEDED" },
    failed: { c: "muted", t: "FAILED" },
    cancelled: { c: "muted", t: "CANCELLED" },
  };
  const m = map[status] ?? { c: "muted" as const, t: status.toUpperCase() };
  return <Pill color={m.c}>{m.t}</Pill>;
}

function ImplSmallLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.2em] text-[var(--ink-muted)]">
      {children}
    </span>
  );
}

function ImplLockedNotice() {
  return (
    <div className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-[var(--paper)] px-10 py-16 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-full border border-[var(--rule)] text-[var(--ink-muted)]">🔒</span>
      <div className="font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5]">使用者故事尚未核准</div>
      <p className="max-w-md text-[13px] leading-6 text-[var(--ink-muted)]">
        自動實作依賴已核准的 stories。先到「使用者故事」階段生成並核准後，即可在此啟動 async 實作 agent。
      </p>
    </div>
  );
}

function ImplPrBanner({ url }: { url: string }) {
  return (
    <a href={url} target="_blank" rel="noreferrer"
      className="flex items-center gap-3 border-b border-[color-mix(in_oklab,var(--approved)_40%,transparent)] bg-[color-mix(in_oklab,var(--approved)_10%,transparent)] px-6 py-3 transition hover:bg-[color-mix(in_oklab,var(--approved)_18%,transparent)]">
      <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[var(--approved)]">PR opened</span>
      <code className="truncate text-[12px] text-[#cdd4df]">{url}</code>
      <span className="ml-auto font-[family-name:var(--font-mono)] text-[10px] text-[var(--approved)]">開啟 ↗</span>
    </a>
  );
}

function ImplFailBanner({ msg }: { msg: string }) {
  return (
    <div className="border-b border-[color-mix(in_oklab,#e0608a_40%,transparent)] bg-[color-mix(in_oklab,#e0608a_10%,transparent)] px-6 py-3">
      <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#e0608a]">failed</span>
      <span className="ml-3 text-[12px] text-[#cdd4df]">{msg}</span>
    </div>
  );
}

function ImplAttemptChip({ run }: { run: ImplementRun }) {
  const tone =
    run.status === "succeeded" ? "var(--approved)"
    : run.status === "running" ? "var(--polaris)"
    : run.status === "rejected" ? "#e0a05b"
    : "var(--ink-muted)";
  return (
    <span className="flex items-center gap-1.5 border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider"
      style={{ color: tone, borderColor: `color-mix(in oklab, ${tone} 40%, transparent)` }}>
      {run.dispatch_role ? `${run.dispatch_role} · ` : ""}attempt {run.attempt} · {run.status}
      {run.exit_code != null && run.status !== "running" ? ` · exit ${run.exit_code}` : ""}
    </span>
  );
}

// ---------- batch 進度表：逐 issue 依序實作，每列一個 story（可點開看該 session log）----------
const BATCH_ROW_DOT: Record<string, string> = {
  pending: "var(--ink-muted)",
  running: "var(--polaris)",
  succeeded: "var(--approved)",
  failed: "#e0608a",
  cancelled: "var(--ink-muted)",
};

function issueUrlFor(repo: string, target: string, n: number): string | null {
  if (!repo || !/^[\w.-]+\/[\w.-]+$/.test(repo)) return null;
  return target === "gitlab"
    ? `https://gitlab.com/${repo}/-/issues/${n}`
    : `https://github.com/${repo}/issues/${n}`;
}

function ImplBatchProgress({
  batch, repo, target, selectedSessionId, onSelect,
}: {
  batch: ImplementBatch;
  repo: string;
  target: string;
  selectedSessionId: number | null;
  onSelect: (sessionId: number) => void;
}) {
  const ok = batch.items.filter((i) => i.status === "succeeded").length;
  const failed = batch.items.filter((i) => i.status === "failed" || i.status === "cancelled").length;
  const done = ok + failed;
  const current = batch.items.find((i) => i.status === "running") ?? null;
  const pct = batch.total > 0 ? Math.round((done / batch.total) * 100) : 0;
  return (
    <div className="border-b border-[var(--rule)]">
      {/* 進度彙整 + 進度條 */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 bg-[var(--bg-elev)]/30 px-6 pt-2.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.16em] text-[var(--ink-muted)]">
        <span>逐 issue 依序 · {batch.status}</span>
        <span className="text-[var(--approved)]">已完成 {ok}</span>
        <span className="text-[#e0608a]">失敗 {failed}</span>
        <span>進度 {done}/{batch.total}</span>
      </div>
      <div className="bg-[var(--bg-elev)]/30 px-6 pb-2.5 pt-1.5">
        <div className="h-1 w-full overflow-hidden rounded bg-[var(--rule)]">
          <div className="h-full bg-[var(--polaris)] transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {/* 目前正在處理哪個 issue（醒目橫幅）*/}
      {current ? (
        <button onClick={() => onSelect(current.session_id)}
          className="flex w-full items-center gap-3 border-y border-[color-mix(in_oklab,var(--polaris)_45%,transparent)] bg-[var(--polaris)]/12 px-6 py-2.5 text-left">
          <span className="pulse-star inline-block h-2 w-2 shrink-0 rounded-full bg-[var(--polaris)]" />
          <span className="shrink-0 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.2em] text-[var(--polaris)]">
            正在處理
          </span>
          <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] text-[var(--polaris)]">
            {current.story_key || "—"}
          </span>
          <span className="min-w-0 flex-1 truncate text-[12px] text-[#e7ecf3]">{current.title}</span>
          {current.issue_number != null && (
            <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] text-[var(--polaris)]">#{current.issue_number}</span>
          )}
        </button>
      ) : batch.status === "running" ? (
        <div className="flex items-center gap-2 border-y border-[var(--rule)] bg-[var(--bg-elev)]/30 px-6 py-2.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.16em] text-[var(--ink-muted)]">
          <span className="pulse-star inline-block h-1.5 w-1.5 rounded-full bg-[var(--polaris)]" />
          準備下一個 issue…
        </div>
      ) : null}

      <ul className="max-h-[46vh] overflow-auto">
        {batch.items.map((it) => {
          const sel = it.session_id === selectedSessionId;
          const running = it.status === "running";
          const iurl = it.issue_number != null ? issueUrlFor(repo, target, it.issue_number) : null;
          return (
            <li key={it.session_id}
              className={`flex items-center gap-3 border-b border-[var(--rule)]/60 px-6 py-2 ${
                running ? "bg-[var(--polaris)]/10" : sel ? "bg-[var(--bg-elev)]/40" : ""}`}>
              <button onClick={() => onSelect(it.session_id)}
                className="flex min-w-0 flex-1 items-center gap-3 text-left">
                {running ? (
                  <span className="pulse-star inline-block h-2 w-2 shrink-0 rounded-full bg-[var(--polaris)]" />
                ) : (
                  <span className="inline-block h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: BATCH_ROW_DOT[it.status] ?? "var(--ink-muted)" }} />
                )}
                <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] text-[var(--ink-muted)]">
                  {it.story_key || "—"}
                </span>
                <span className={`min-w-0 flex-1 truncate text-[12px] ${running ? "text-[#e7ecf3]" : "text-[#cdd4df]"}`}>{it.title}</span>
                <span className={`shrink-0 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.16em] ${running ? "text-[var(--polaris)]" : "text-[var(--ink-muted)]"}`}>
                  {running ? "處理中" : it.status}
                </span>
              </button>
              <div className="flex shrink-0 items-center gap-2 font-[family-name:var(--font-mono)] text-[10px]">
                {it.issue_number != null && (iurl ? (
                  <a href={iurl} target="_blank" rel="noreferrer noopener"
                    className="text-[var(--ink-muted)] hover:text-[var(--polaris)] hover:underline">
                    #{it.issue_number}
                  </a>
                ) : <span className="text-[var(--ink-muted)]">#{it.issue_number}</span>)}
                {it.pr_url && (
                  <a href={it.pr_url} target="_blank" rel="noreferrer noopener"
                    className="text-[var(--polaris)] hover:underline">PR ↗</a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------- implement log：把 claude-cli stream-json 整理成可讀事件 + 統計 ----------
type ImplTone = "agent" | "tool" | "ok" | "warn" | "muted";
type ImplEvent = { key: string; attempt: number; tone: ImplTone; text: string; ts: number | null };

// epoch 秒 → HH:MM:SS（24h）；無值回 ""
function fmtLogTs(sec: number | null): string {
  if (!sec) return "";
  return new Date(sec * 1000).toLocaleTimeString("en-GB", { hour12: false });
}
type ImplStats = {
  turns: number; durationMs: number; costUsd: number;
  toolCalls: number; filesWritten: number; hasCost: boolean;
};

const IMPL_TONE_CLASS: Record<ImplTone, string> = {
  agent: "text-[#cdd4df]",
  tool: "text-[var(--polaris)]",
  ok: "text-[var(--approved)]",
  warn: "text-[#e0a05b]",
  muted: "text-[var(--ink-muted)]",
};
const IMPL_FILE_TOOLS = new Set(["Write", "Edit", "MultiEdit", "NotebookEdit", "Create"]);

// clone 的絕對路徑 → repo 相對路徑（.../impl_work/{id}/repo/foo → foo）
function implShortPath(p: string): string {
  const i = p.indexOf("/repo/");
  return i >= 0 ? p.slice(i + 6) : p;
}

// 解析整段 log：events = 過濾後可讀事件；stats = 彙整數字（隱藏 thinking_tokens / rate_limit 噪音）
function parseImplLog(lines: ImplementLogLine[]): { events: ImplEvent[]; stats: ImplStats } {
  const events: ImplEvent[] = [];
  const stats: ImplStats = { turns: 0, durationMs: 0, costUsd: 0, toolCalls: 0, filesWritten: 0, hasCost: false };
  for (const l of lines) {
    const pushE = (e: Omit<ImplEvent, "ts">) => events.push({ ...e, ts: l.created_at });
    const raw = l.content.replace(/\n$/, "");
    if (!raw.trim()) continue;
    let m: { type?: string; subtype?: string; model?: string; is_error?: boolean;
             num_turns?: number; duration_ms?: number; total_cost_usd?: number;
             message?: { content?: Array<Record<string, unknown>> } } | null = null;
    try { m = JSON.parse(raw); } catch {
      // 非 JSON（mock 純文字 / 後端 system 註記）→ 原樣顯示
      pushE({ key: `${l.id}`, attempt: l.attempt, tone: l.kind === "system" ? "warn" : "muted", text: raw });
      continue;
    }
    const t = m?.type;
    if (t === "assistant") {
      (m?.message?.content ?? []).forEach((b, bi) => {
        if (b.type === "text" && typeof b.text === "string" && b.text.trim()) {
          pushE({ key: `${l.id}-${bi}`, attempt: l.attempt, tone: "agent", text: b.text.trim() });
        } else if (b.type === "tool_use") {
          stats.toolCalls++;
          const name = (b.name as string) ?? "tool";
          if (IMPL_FILE_TOOLS.has(name)) stats.filesWritten++;
          const inp = (b.input ?? {}) as Record<string, string>;
          const arg = implShortPath(inp.file_path || inp.path || "") || inp.command || inp.pattern || "";
          pushE({ key: `${l.id}-${bi}`, attempt: l.attempt, tone: "tool", text: `→ ${name}${arg ? " " + arg.slice(0, 120) : ""}` });
        }
        // thinking → 跳過
      });
    } else if (t === "user") {
      (m?.message?.content ?? []).forEach((b, bi) => {
        if (b.type === "tool_result" && b.is_error) {
          const c = typeof b.content === "string" ? b.content : JSON.stringify(b.content);
          pushE({ key: `${l.id}-${bi}`, attempt: l.attempt, tone: "warn", text: `✗ ${c.slice(0, 160)}` });
        }
        // 成功 tool_result（如 File created）→ 跳過，tool_use 已表達
      });
    } else if (t === "result") {
      if (typeof m?.num_turns === "number") stats.turns = Math.max(stats.turns, m.num_turns);
      if (typeof m?.duration_ms === "number") stats.durationMs += m.duration_ms;
      if (typeof m?.total_cost_usd === "number") { stats.costUsd += m.total_cost_usd; stats.hasCost = true; }
      const ok = m?.subtype === "success" && !m?.is_error;
      pushE({ key: `${l.id}`, attempt: l.attempt, tone: ok ? "ok" : "warn",
        text: ok ? "✓ attempt 完成" : `⚠ attempt 結束（${m?.subtype ?? "error"}）` });
    } else if (t === "system" && m?.subtype === "init") {
      pushE({ key: `${l.id}`, attempt: l.attempt, tone: "muted", text: `session 啟動${m?.model ? " · " + m.model : ""}` });
    }
    // system/thinking_tokens、rate_limit_event → 隱藏
  }
  return { events, stats };
}

function ImplStat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[#e6ecf5]">{value}</span>
      <span className="text-[var(--ink-muted)]">{label}</span>
    </span>
  );
}

function ImplementWorkspace({
  thread, storiesArtifact, storiesApproved, delivery, onSetError,
}: {
  thread: string | null;
  storiesArtifact: string;
  storiesApproved: boolean;
  delivery: DeliveryStatus | null;
  onSetError: (m: string) => void;
}) {
  const [runners, setRunners] = useState<RunnerInfo[]>([]);
  const [runner, setRunner] = useState<string>("mock");
  const [mode, setMode] = useState<"single" | "roles">("roles");
  const [runMode, setRunMode] = useState<"batch" | "session">("batch");
  const [autoMerge, setAutoMerge] = useState<boolean>(false);
  const [logExpanded, setLogExpanded] = useState<boolean>(false);   // ⤢ 展開 log（隱藏左側儀表軌）
  const [targetRepo, setTargetRepo] = useState<string>("");
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [session, setSession] = useState<ImplementSession | null>(null);
  const [lines, setLines] = useState<ImplementLogLine[]>([]);
  const [starting, setStarting] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [batchId, setBatchId] = useState<number | null>(null);
  const [batch, setBatch] = useState<ImplementBatch | null>(null);
  const cursorRef = useRef(0);
  const logBottomRef = useRef<HTMLDivElement>(null);
  const followCurrentRef = useRef(true);   // batch log 是否自動跟到目前處理中的 issue（手動點列後關閉）

  // log 整理：過濾噪音 + 彙整統計（claude-cli stream-json）
  const { events: logEvents, stats: logStats } = useMemo(() => parseImplLog(lines), [lines]);

  // runner 清單（data-driven：第三方 runner plugin 會自動出現）
  useEffect(() => {
    let on = true;
    fetchRunners()
      .then((rs) => {
        if (!on) return;
        setRunners(rs);
        const firstAvail = rs.find((r) => r.available);
        setRunner(firstAvail ? firstAvail.choice : rs[0]?.choice ?? "mock");
      })
      .catch(() => {/* 靜默；仍可手選 mock */});
    return () => { on = false; };
  }, []);

  // 專案 delivery 設定的 repo（fallback：尚未發佈時也能帶入）
  const [projectRepo, setProjectRepo] = useState<string>("");
  useEffect(() => {
    if (!thread) { setProjectRepo(""); return; }
    let on = true;
    apiFetch<{ repo_full_name: string; repo_owner: string }>(`/api/projects/${thread}`)
      .then((p) => {
        if (!on) return;
        const full = (p.repo_full_name || "").trim();
        const owner = (p.repo_owner || "").trim();
        // owner/repo 直接用；只有名稱 + 有 owner → 組起來；其餘原樣（new 模式個人 repo 只有名稱）
        setProjectRepo(full.includes("/") ? full : owner && full ? `${owner}/${full}` : full);
      })
      .catch(() => { if (on) setProjectRepo(""); });
    return () => { on = false; };
  }, [thread]);

  // 自動帶入目標 repo：已發佈 repo（owner/repo）優先，否則用專案設定；只在使用者尚未手動填時帶
  useEffect(() => {
    const candidate = delivery?.repo || projectRepo;
    if (candidate) setTargetRepo((cur) => cur || candidate);
  }, [delivery?.repo, projectRepo]);

  // F5 / 切換專案後還原最近一次實作 session（含 log 與執行中進度）。
  // 後端 impl_sessions 已持久化；前端 mount 時主動撈最新一筆，設定 sessionId 後
  // 既有的 poll effect 會自動拉回 session 狀態並從游標 0 補齊整段 log。
  useEffect(() => {
    setSessionId(null); setSession(null); setLines([]); cursorRef.current = 0;
    setBatchId(null); setBatch(null);
    if (!thread) return;
    let on = true;
    // 最近一次 batch（DESC，[0] = 最近）優先還原；無 batch 則退回單 session
    fetchBatches(thread)
      .then((batches) => {
        if (!on || !batches.length) return;
        setRunMode("batch");
        setBatchId(batches[0].batch_id);
      })
      .catch(() => {/* 靜默 */});
    fetchImplementSessions(thread)
      .then((sessions) => {
        if (!on || !sessions.length) return;
        setSessionId(sessions[0].session_id); // DESC 排序，[0] = 最近一次
      })
      .catch(() => {/* 靜默；無歷史 session 即維持 idle */});
    return () => { on = false; };
  }, [thread]);

  // poll batch 進度（batch 在跑時遞迴 setTimeout，終局再補一次）
  useEffect(() => {
    if (batchId == null) return;
    let on = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const b = await fetchBatch(batchId);
        if (!on) return;
        setBatch(b);
        if (b.status === "running") timer = setTimeout(tick, 800);
      } catch (e) {
        if (on) onSetError(`讀取批次狀態失敗：${(e as Error).message}`);
      }
    };
    tick();
    return () => { on = false; if (timer) clearTimeout(timer); };
  }, [batchId, onSetError]);

  // batch log 自動跟到「目前處理中」的 issue（除非使用者手動點了某列）
  const runningSessionId = runMode === "batch"
    ? batch?.items.find((i) => i.status === "running")?.session_id ?? null
    : null;
  useEffect(() => {
    if (runningSessionId == null || !followCurrentRef.current) return;
    setSessionId((cur) => {
      if (cur === runningSessionId) return cur;
      setLines([]); cursorRef.current = 0; setSession(null);
      return runningSessionId;
    });
  }, [runningSessionId]);

  // 已發佈 issues 連結（GitHub/GitLab repo issues 頁）
  const issuesUrl = delivery?.repo && /^[\w.-]+\/[\w.-]+$/.test(delivery.repo)
    ? (delivery.target === "gitlab"
        ? `https://gitlab.com/${delivery.repo}/-/issues`
        : `https://github.com/${delivery.repo}/issues`)
    : null;

  const status = session?.status ?? "idle";
  const sessionPolling = status === "running" || status === "pending";
  const batchPolling = runMode === "batch" && batch?.status === "running";
  const polling = runMode === "batch" ? !!batchPolling : sessionPolling;

  // poll status + log（session 在跑時，遞迴 setTimeout；完成後再 drain 一次補尾）
  useEffect(() => {
    if (sessionId == null) return;
    let on = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const drainLog = async () => {
      const log = await fetchImplementLog(sessionId, cursorRef.current);
      if (!on) return;
      if (log.lines.length) {
        cursorRef.current = log.next_cursor;
        setLines((prev) => [...prev, ...log.lines]);
      }
    };

    const tick = async () => {
      try {
        const s = await fetchImplementSession(sessionId);
        if (!on) return;
        setSession(s);
        await drainLog();
        if (!on) return;
        if (s.status === "running" || s.status === "pending") {
          timer = setTimeout(tick, 700);
        } else {
          await drainLog(); // 終局再補一次，確保尾端輸出不漏
        }
      } catch (e) {
        if (on) onSetError(`讀取實作狀態失敗：${(e as Error).message}`);
      }
    };
    tick();
    return () => { on = false; if (timer) clearTimeout(timer); };
  }, [sessionId, onSetError]);

  // log 自動捲到底
  useEffect(() => { logBottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [lines]);

  const canStart = !!thread && storiesApproved && !polling && !starting;
  const runs = session?.runs ?? [];

  const start = async () => {
    if (!thread) return;
    setStarting(true);
    onSetError("");
    setLines([]); cursorRef.current = 0; setSession(null); setSessionId(null);
    setBatch(null); setBatchId(null);
    followCurrentRef.current = true;
    try {
      if (runMode === "batch") {
        const { batch_id } = await startBatch({
          thread_id: thread, runner, target_repo: targetRepo.trim(), mode,
          auto_merge: autoMerge,
        });
        setBatchId(batch_id);
      } else {
        const { session_id } = await startImplement({
          thread_id: thread, runner, target_repo: targetRepo.trim(),
          story: storiesArtifact, title: "Implement", mode,
        });
        setSessionId(session_id);
      }
    } catch (e) {
      onSetError(`啟動實作失敗：${(e as Error).message}`);
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    try {
      if (runMode === "batch" && batchId != null) await cancelBatch(batchId);
      else if (sessionId != null) await cancelImplement(sessionId);
    } catch (e) { onSetError(`取消失敗：${(e as Error).message}`); }
  };

  const selStyle =
    "border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-1.5 font-[family-name:var(--font-mono)] text-[11px] text-[#cdd4df] focus:border-[var(--polaris)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className="flex min-h-0 flex-1">
      <section className="rise-4 flex min-w-0 flex-1 flex-col overflow-hidden px-10 py-6">
        <ArtifactBar artifact="implement" stage="deliver" op="auto_implement" right={
          <>
            {status !== "idle" && <ImplStatusPill status={status} />}
            <Pill color="muted">FIX-LOOP ≤3</Pill>
          </>
        } />

        {!storiesApproved ? (
          <ImplLockedNotice />
        ) : (
          <div className="shadow-anvil paper-texture relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--paper)]">
            {/* 控制列 */}
            <div className="flex flex-wrap items-end gap-4 border-b border-[var(--rule)] px-6 py-5">
              <label className="flex flex-col gap-1">
                <ImplSmallLabel>runner</ImplSmallLabel>
                <select value={runner} onChange={(e) => setRunner(e.target.value)} disabled={polling} className={selStyle}>
                  {runners.length === 0 && <option value="mock">mock</option>}
                  {runners.map((r) => (
                    <option key={r.choice} value={r.choice}>
                      {r.choice}{r.available ? "" : "（不可用）"}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <ImplSmallLabel>執行方式</ImplSmallLabel>
                <select value={runMode} onChange={(e) => setRunMode(e.target.value as "batch" | "session")} disabled={polling} className={selStyle}>
                  <option value="batch">逐 issue 依序</option>
                  <option value="session">整份一次</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <ImplSmallLabel>模式</ImplSmallLabel>
                <select value={mode} onChange={(e) => setMode(e.target.value as "single" | "roles")} disabled={polling} className={selStyle}>
                  <option value="single">單一 fix-loop</option>
                  <option value="roles">多角色 pipeline</option>
                </select>
              </label>
              {runMode === "batch" && (
                <label className="flex flex-col gap-1" title="每個 story 過 QA gate 後依序 merge 進 main，下一個 story 從更新後的 main 切（後者吃得到前者、避免衝突）。僅 github 真跑生效。">
                  <ImplSmallLabel>整合方式</ImplSmallLabel>
                  <button
                    type="button"
                    onClick={() => setAutoMerge((v) => !v)}
                    disabled={polling}
                    className={`flex items-center gap-2 border px-3 py-1.5 font-[family-name:var(--font-mono)] text-[11px] transition disabled:cursor-not-allowed disabled:opacity-50 ${
                      autoMerge
                        ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_14%,transparent)] text-[var(--polaris)]"
                        : "border-[var(--rule-dark)] text-[#cdd4df] hover:border-[#4a5468]"
                    }`}
                  >
                    <span className={`grid h-3 w-3 place-items-center border ${autoMerge ? "border-[var(--polaris)] bg-[var(--polaris)]" : "border-[#2e3441]"}`}>
                      {autoMerge && <span className="text-[8px] leading-none text-white">✓</span>}
                    </span>
                    過 gate 自動 merge
                  </button>
                </label>
              )}
              <label className="flex min-w-[200px] flex-1 flex-col gap-1">
                <ImplSmallLabel>target repo · owner/repo</ImplSmallLabel>
                <input value={targetRepo} onChange={(e) => setTargetRepo(e.target.value)} disabled={polling}
                  placeholder="owner/repo"
                  className={selStyle + " w-full"} />
                <span className="font-[family-name:var(--font-mono)] text-[9px] leading-4 text-[var(--ink-muted)]">
                  claude-cli 以專案發佈設定的 repo 為準；mock 用此欄位 dry-run。
                </span>
              </label>
              <div className="ml-auto flex items-center gap-2">
                {polling ? (
                  <ToolBtn onClick={cancel}>取消</ToolBtn>
                ) : (
                  <ToolBtn primary onClick={start} disabled={!canStart}>
                    {starting ? "啟動中…" : (runMode === "batch" ? !batch : status === "idle") ? "開始自動實作" : "重新實作"}
                  </ToolBtn>
                )}
              </div>
            </div>

            {delivery && (
              <div className="flex flex-wrap items-center gap-2 border-b border-[var(--rule)] bg-[var(--bg-elev)]/30 px-6 py-2.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.16em] text-[var(--ink-muted)]">
                <span className="text-[var(--approved)]">✓ 已發佈 {delivery.created} 筆 issue</span>
                <span>→</span>
                {issuesUrl ? (
                  <a href={issuesUrl} target="_blank" rel="noreferrer noopener" className="text-[var(--polaris)] hover:underline">
                    {delivery.repo}/issues
                  </a>
                ) : (
                  <span className="text-[#cdd4df]">{delivery.repo || delivery.target}</span>
                )}
              </div>
            )}
            {/* body：水平分割 —— 左「儀表軌」+ 主導 log 視窗（log 佔滿整個 body 高度，不再吃殘高）*/}
            <div className="flex min-h-0 flex-1 overflow-hidden">
              {!logExpanded && (
                <aside className="flex w-[320px] shrink-0 flex-col overflow-y-auto border-r border-[var(--rule)]">
                  {runMode === "batch" && batch ? (
                    <ImplBatchProgress
                      batch={batch}
                      repo={batch.target_repo || delivery?.repo || ""}
                      target={delivery?.target || "github"}
                      selectedSessionId={sessionId}
                      onSelect={(sid) => {
                        // 手動點列 → 停止自動跟隨（點目前處理中那列則恢復跟隨）
                        followCurrentRef.current = sid === runningSessionId;
                        setLines([]); cursorRef.current = 0; setSession(null);
                        setSessionId(sid);
                      }}
                    />
                  ) : null}

                  {session?.pr_url ? <ImplPrBanner url={session.pr_url} /> : null}
                  {status === "failed" && session?.error_message ? <ImplFailBanner msg={session.error_message} /> : null}

                  {runs.length > 0 && (
                    <div className="flex flex-wrap gap-2 border-b border-[var(--rule)] px-4 py-3">
                      {runs.map((r) => <ImplAttemptChip key={r.run_id} run={r} />)}
                    </div>
                  )}

                  {/* 統計：files/tools/turns/elapsed/cost */}
                  {lines.length > 0 && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.16em]">
                      <ImplStat label="files" value={String(logStats.filesWritten)} />
                      <ImplStat label="tools" value={String(logStats.toolCalls)} />
                      <ImplStat label="turns" value={String(logStats.turns)} />
                      <ImplStat label="elapsed" value={`${(logStats.durationMs / 1000).toFixed(0)}s`} />
                      {logStats.hasCost && <ImplStat label="cost" value={`$${logStats.costUsd.toFixed(3)}`} />}
                    </div>
                  )}
                </aside>
              )}

              {/* log 欄：header（raw / 展開切換）+ stream，佔滿 body 高度 */}
              <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                <div className="flex items-center justify-between gap-2 border-b border-[var(--rule)] px-6 py-2.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                  <span className="flex items-center gap-2">
                    {polling && <span className="pulse-star inline-block h-1.5 w-1.5 rounded-full bg-[var(--polaris)]" />}
                    log · {polling ? "streaming" : status === "idle" ? "idle" : "complete"}
                  </span>
                  <span className="flex items-center gap-2">
                    {lines.length > 0 && (
                      <button onClick={() => setShowRaw((v) => !v)} className="border border-[var(--rule-dark)] px-2 py-0.5 tracking-[0.16em] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">
                        {showRaw ? "整理後" : "raw"}
                      </button>
                    )}
                    <button onClick={() => setLogExpanded((v) => !v)}
                      title={logExpanded ? "顯示左側儀表軌" : "展開 log（隱藏左側儀表軌）"}
                      className="border border-[var(--rule-dark)] px-2 py-0.5 tracking-[0.16em] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">
                      {logExpanded ? "⤡ 收合" : "⤢ 展開"}
                    </button>
                  </span>
                </div>
                <div className="min-h-0 flex-1 overflow-auto px-6 py-4">
                  {lines.length === 0 ? (
                    status === "failed" && session?.error_message ? (
                      // 失敗但無 agent log（多半是準備工作目錄 / 前置階段就掛）→ 直接秀原因，別只留空白
                      <div className="space-y-2">
                        <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.2em] text-[#e0608a]">
                          ✗ 實作失敗（無 agent 輸出 — 多為準備工作目錄 / 前置失敗）
                        </div>
                        <pre className="whitespace-pre-wrap border-l-2 border-[#e0608a]/50 bg-[#e0608a]/8 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] leading-5 text-[#f0a8bd]">
                          {session.error_message}
                        </pre>
                      </div>
                    ) : (
                      <div className="py-8 text-center font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">
                        {runMode === "batch" && batch
                          ? "點左側任一 story 看該 issue 的實作 log。"
                          : "尚無輸出。選 runner（mock = 安全 dry-run）後按「開始自動實作」。"}
                      </div>
                    )
                  ) : showRaw ? (
                    <ul className="space-y-0.5 font-[family-name:var(--font-mono)] text-[11px] leading-5">
                      {lines.map((l) => (
                        <li key={l.id} className={l.kind === "system" ? "text-[#e0a05b]" : "text-[#cdd4df]"}>
                          {l.created_at && <span className="mr-1 tabular-nums opacity-30">{fmtLogTs(l.created_at)}</span>}
                          <span className="opacity-40">[a{l.attempt}]</span> {l.content.replace(/\n$/, "")}
                        </li>
                      ))}
                      <div ref={logBottomRef} />
                    </ul>
                  ) : logEvents.length === 0 ? (
                    <div className="py-8 text-center font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">
                      {polling ? "agent 思考中…（無可顯示事件）" : "無可顯示事件，可切「raw」看原始輸出。"}
                    </div>
                  ) : (
                    <ul className="space-y-1 font-[family-name:var(--font-mono)] text-[11px] leading-5">
                      {logEvents.map((e) => (
                        <li key={e.key} className={IMPL_TONE_CLASS[e.tone]}>
                          {e.ts && <span className="mr-1 tabular-nums opacity-30">{fmtLogTs(e.ts)}</span>}
                          <span className="opacity-30">[a{e.attempt}]</span>{" "}
                          <span className={e.tone === "agent" ? "whitespace-pre-wrap" : ""}>{e.text}</span>
                        </li>
                      ))}
                      <div ref={logBottomRef} />
                    </ul>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        <BottomMeta
          left={<>runner <code className="text-[#cdd4df]">{runner}</code> · {runMode === "batch" ? "逐 issue 一 PR · QA gate" : "fix-loop 硬上限 3 · 成功開 PR"}</>}
          right={runMode === "batch"
            ? (batch ? <>batch #{batch.batch_id} · {batch.status} · {batch.total} stories</> : <>尚未啟動</>)
            : (session ? <>session #{session.session_id} · {status}</> : <>尚未啟動</>)}
        />
      </section>
    </div>
  );
}

// ============================== Workflows view（M3：API-driven CRUD）==============================
type WorkflowDraft = Omit<Workflow, "source" | "source_plugin" | "created_at">;

// 缺口1：NewWorkflowModal —— 取代 window.prompt，一次收 id + label + description
function NewWorkflowModal({ open, existingIds, onSubmit, onCancel }: {
  open: boolean;
  existingIds: string[];
  onSubmit: (id: string, label: string, description: string) => void;
  onCancel: () => void;
}) {
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const idRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setId(""); setLabel(""); setDescription(""); setError(null);
    setTimeout(() => idRef.current?.focus(), 50);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open, onCancel]);

  if (!open) return null;

  const submit = () => {
    const tid = id.trim();
    if (!tid) return setError("workflow id 不可為空");
    if (!/^[a-z0-9_-]+$/i.test(tid)) return setError("id 只允許英數 / 底線 / 連字號");
    if (existingIds.includes(tid)) return setError(`id「${tid}」已存在`);
    onSubmit(tid, label.trim() || tid, description.trim());
  };

  const fc = "w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-mono)] text-[12.5px] text-[#e6ecf5] outline-none placeholder:text-[var(--ink-muted)] focus:border-[var(--polaris)]";

  return (
    <div
      className="rise-1 fixed inset-0 z-50 grid place-items-center bg-[var(--bg)]/72 px-4 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
      role="dialog" aria-modal="true"
    >
      <div className="shadow-anvil paper-texture relative w-full max-w-md border border-[var(--paper-edge)] bg-[var(--paper)]">
        <div className="border-b border-[var(--rule)] px-6 py-4">
          <h2 className="font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]">新建 workflow</h2>
          <p className="mt-1.5 font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">POST /api/workflows</p>
        </div>
        <div className="space-y-4 px-6 py-5">
          {error && (
            <div className="border border-[#f47171]/40 bg-[#f47171]/10 px-3 py-2 font-[family-name:var(--font-mono)] text-[11px] text-[#f47171]">{error}</div>
          )}
          <div>
            <label className="mb-1 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">id<span className="ml-1 text-[#f47171]">*</span></label>
            <input ref={idRef} type="text" value={id} onChange={(e) => setId(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
              placeholder="e.g. checkout-flow（建立後不能改）" className={fc} />
          </div>
          <div>
            <label className="mb-1 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">label</label>
            <input type="text" value={label} onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
              placeholder="顯示名稱（留空則用 id）" className={fc} />
          </div>
          <div>
            <label className="mb-1 block font-[family-name:var(--font-mono)] text-[10.5px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">description</label>
            <input type="text" value={description} onChange={(e) => setDescription(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
              placeholder="一句話描述" className={fc} />
          </div>
          <p className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">建立後進編輯模式加 stage · ↵ 提交 · esc 取消</p>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-[var(--rule)] bg-[var(--bg-elev)]/30 px-5 py-3">
          <button onClick={onCancel} className="border border-[var(--rule-dark)] bg-transparent px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-[#cdd4df] transition hover:border-[#404a5b] hover:bg-[var(--bg-elev)]">取消</button>
          <button onClick={submit} className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-1.5 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.2em] text-white transition hover:bg-[var(--polaris-hi)]">建立並編輯</button>
        </div>
      </div>
    </div>
  );
}

function WorkflowsView({
  workflows, agents, availableStages, stageInfo, onRefresh, onDelete, onSetError,
}: {
  workflows: Workflow[];
  agents: Agent[];
  availableStages: string[];
  stageInfo: Record<string, { label: string; description: string }>;
  onRefresh: () => Promise<void>;
  onDelete: (wf: Workflow) => void;
  onSetError: (msg: string | null) => void;
}) {
  const [pickedId, setPickedId] = useState<string>(() => workflows[0]?.id ?? "default");
  const [draft, setDraft] = useState<WorkflowDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [newModalOpen, setNewModalOpen] = useState(false);   // 缺口1：取代 window.prompt

  // 切到 user workflow → 建立 draft 副本（builtin 只 view）
  const picked = workflows.find((w) => w.id === pickedId) ?? workflows[0];
  const isEditable = picked?.source === "user";
  const inEditMode = draft !== null;

  // 切換 workflow 時清掉舊 draft —— 但若 draft 正是當前 pickedId（剛建的新 workflow），保留
  useEffect(() => {
    setDraft((d) => (d && d.id === pickedId ? d : null));
  }, [pickedId]);

  // 如果 list 變了但 picked 不在了，挑第一個（新建中的 draft 還沒存進 list，要排除）
  useEffect(() => {
    if (workflows.length > 0 && !workflows.find((w) => w.id === pickedId) && !(draft && draft.id === pickedId)) {
      setPickedId(workflows[0].id);
    }
  }, [workflows, pickedId]);

  const startEdit = () => {
    if (!picked) return;
    setDraft({
      id: picked.id, label: picked.label, description: picked.description,
      stages: picked.stages.map((s) => ({
        stage_id: s.stage_id,
        depends_on: [...s.depends_on],
        agent_bindings: s.agent_bindings.map((b) => ({ ...b })),
        collab_mode: s.collab_mode,
      })),
    });
  };

  // 缺口1：NewWorkflowModal submit → 建空白 draft 直接進編輯
  const onNewWorkflowSubmit = (id: string, label: string, description: string) => {
    setNewModalOpen(false);
    setDraft({ id, label, description, stages: [] });
    setPickedId(id);
  };

  const saveDraft = async () => {
    if (!draft) return;
    onSetError(null);
    setSaving(true);
    try {
      const exists = workflows.find((w) => w.id === draft.id && w.source === "user");
      if (exists) {
        await updateWorkflow(draft.id, draft);
      } else {
        await createWorkflow(draft);
      }
      await onRefresh();
      setDraft(null);
    } catch (e) {
      onSetError(`儲存失敗：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  // Stage manipulation helpers
  const moveStage = (idx: number, dir: -1 | 1) => {
    if (!draft) return;
    const next = [...draft.stages];
    const j = idx + dir;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    // depends_on 不能引用後面的 stage —— 移動時要過濾
    const ids = next.map((s) => s.stage_id);
    next.forEach((s, i) => {
      s.depends_on = s.depends_on.filter((d) => ids.indexOf(d) < i);
    });
    setDraft({ ...draft, stages: next });
  };

  const removeStage = (idx: number) => {
    if (!draft) return;
    const next = draft.stages.filter((_, i) => i !== idx);
    const removedId = draft.stages[idx].stage_id;
    next.forEach((s) => {
      s.depends_on = s.depends_on.filter((d) => d !== removedId);
    });
    setDraft({ ...draft, stages: next });
  };

  const addStage = (sid: string) => {
    if (!draft) return;
    if (draft.stages.find((s) => s.stage_id === sid)) return;
    const prev = draft.stages[draft.stages.length - 1]?.stage_id;
    setDraft({
      ...draft,
      stages: [...draft.stages, {
        stage_id: sid,
        depends_on: prev ? [prev] : [],
        agent_bindings: [],
        collab_mode: "single",
      }],
    });
  };

  const cycleCollab = (idx: number) => {
    if (!draft) return;
    const order: ApiCollabMode[] = ["single", "discussion", "dispatch"];
    const cur = draft.stages[idx].collab_mode;
    const next = order[(order.indexOf(cur) + 1) % order.length];
    const ns = [...draft.stages];
    ns[idx] = { ...ns[idx], collab_mode: next };
    setDraft({ ...draft, stages: ns });
  };

  // 缺口3：depends_on multi-select toggle（只能勾「排在前面」的 stage → 天然防環）
  const toggleDependency = (stageIdx: number, depId: string) => {
    if (!draft) return;
    const ns = [...draft.stages];
    const cur = ns[stageIdx].depends_on;
    const next = cur.includes(depId) ? cur.filter((d) => d !== depId) : [...cur, depId];
    ns[stageIdx] = { ...ns[stageIdx], depends_on: next };
    setDraft({ ...draft, stages: ns });
  };

  const setBindingRole = (stageIdx: number, bindIdx: number, role: ApiCollabRole) => {
    if (!draft) return;
    const ns = [...draft.stages];
    const newBindings = [...ns[stageIdx].agent_bindings];
    newBindings[bindIdx] = { ...newBindings[bindIdx], role };
    ns[stageIdx] = { ...ns[stageIdx], agent_bindings: newBindings };
    setDraft({ ...draft, stages: ns });
  };

  const removeBinding = (stageIdx: number, bindIdx: number) => {
    if (!draft) return;
    const ns = [...draft.stages];
    ns[stageIdx] = {
      ...ns[stageIdx],
      agent_bindings: ns[stageIdx].agent_bindings.filter((_, i) => i !== bindIdx),
    };
    setDraft({ ...draft, stages: ns });
  };

  const addBinding = (stageIdx: number, agentId: string) => {
    if (!draft) return;
    const stage = draft.stages[stageIdx];
    if (stage.agent_bindings.find((b) => b.agent_id === agentId)) return;
    const ns = [...draft.stages];
    ns[stageIdx] = {
      ...ns[stageIdx],
      agent_bindings: [...stage.agent_bindings, { agent_id: agentId, role: "lead" }],
    };
    setDraft({ ...draft, stages: ns });
  };

  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Workflows" sub="表單式編輯：有序 stage 清單 + 依賴推導（無 DAG canvas）" right={
        <button onClick={() => setNewModalOpen(true)} className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-white transition hover:bg-[var(--polaris-hi)]">
          ＋ new workflow
        </button>
      } />
      <NewWorkflowModal
        open={newModalOpen}
        existingIds={workflows.map((w) => w.id)}
        onSubmit={onNewWorkflowSubmit}
        onCancel={() => setNewModalOpen(false)}
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[420px] shrink-0 flex-col overflow-y-auto border-r border-[var(--rule-dark)] p-6 space-y-3">
          {workflows.length === 0 && (
            <div className="border border-dashed border-[var(--rule-dark)] py-8 text-center font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              無 workflow（loading…）
            </div>
          )}
          {workflows.map((w) => {
            const active = w.id === pickedId;
            return (
              <button key={w.id} onClick={() => setPickedId(w.id)}
                className={`flex flex-col items-stretch border p-4 text-left transition ${
                  active ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_6%,transparent)]" : "border-[var(--rule-dark)] bg-[var(--bg-elev)]/40 hover:border-[#4a5468]"
                }`}>
                <div className="mb-1 flex items-center justify-between">
                  <code className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{w.id}</code>
                  {w.source === "builtin" ? <Pill color="approved">BUILTIN</Pill> : <Pill color="muted">USER</Pill>}
                </div>
                <div className="font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">{w.label}</div>
                {w.description && <div className="mt-1 text-[12px] text-[#97a0b3]">{w.description}</div>}
                <div className="mt-3 flex flex-wrap items-center gap-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  {w.stages.map((s, i) => (
                    <span key={s.stage_id} className="flex items-center gap-1.5">
                      <span className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 text-[#cdd4df]">{s.stage_id}</span>
                      {i < w.stages.length - 1 && <span className="text-[var(--ink-muted)]">→</span>}
                    </span>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between border-t border-[var(--rule-dark)] pt-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  <span>source · <span className="text-[#cdd4df]">{w.source}{w.source_plugin ? ` · ${w.source_plugin}` : ""}</span></span>
                  <span>{w.stages.length} stages</span>
                </div>
              </button>
            );
          })}
        </div>

        <div className="flex min-w-0 flex-1 flex-col overflow-y-auto p-8">
          {!picked ? (
            <div className="grid flex-1 place-items-center font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              選一個 workflow
            </div>
          ) : (
            <>
              <div className="mb-2 flex items-baseline gap-3">
                <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                  {inEditMode ? "EDITING" : "VIEWING"} · {picked.id}
                </span>
                {picked.source === "builtin" && (
                  <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[#5e6878]">
                    （builtin · 唯讀）
                  </span>
                )}
              </div>
              {inEditMode ? (
                /* 缺口4：label / description inline edit */
                <div className="mt-1 space-y-3">
                  <div>
                    <SectionLabel>LABEL</SectionLabel>
                    <input
                      type="text"
                      value={draft!.label}
                      onChange={(e) => setDraft({ ...draft!, label: e.target.value })}
                      placeholder="workflow 顯示名稱"
                      className="w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-display)] text-[18px] font-semibold text-[#e6ecf5] outline-none focus:border-[var(--polaris)]"
                    />
                  </div>
                  <div>
                    <SectionLabel>DESCRIPTION</SectionLabel>
                    <input
                      type="text"
                      value={draft!.description}
                      onChange={(e) => setDraft({ ...draft!, description: e.target.value })}
                      placeholder="一句話描述這個 workflow"
                      className="w-full border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2 font-[family-name:var(--font-sans)] text-[13px] text-[#cdd4df] outline-none placeholder:text-[var(--ink-muted)] focus:border-[var(--polaris)]"
                    />
                  </div>
                </div>
              ) : (
                <>
                  <h2 className="font-[family-name:var(--font-display)] text-[24px] font-semibold leading-tight text-[#e6ecf5]">{picked.label}</h2>
                  {picked.description && <p className="mt-1 text-[13px] text-[#97a0b3]">{picked.description}</p>}
                </>
              )}

              <div className="mt-7">
                <SectionLabel>STAGES{inEditMode ? "（上下移 / 加減 / depends_on / binding / collab）" : ""}</SectionLabel>
                <WorkflowStageList
                  stages={inEditMode ? draft!.stages : picked.stages}
                  agents={agents}
                  stageInfo={stageInfo}
                  editable={inEditMode}
                  onMoveStage={moveStage}
                  onRemoveStage={removeStage}
                  onCycleCollab={cycleCollab}
                  onAddBinding={addBinding}
                  onRemoveBinding={removeBinding}
                  onSetBindingRole={setBindingRole}
                  onToggleDependency={toggleDependency}
                />
                {inEditMode && (
                  <AddStagePicker
                    availableStages={availableStages.filter((s) => !draft!.stages.find((x) => x.stage_id === s))}
                    stageInfo={stageInfo}
                    onAdd={addStage}
                  />
                )}
              </div>

              <div className="mt-8 flex justify-end gap-2">
                {!inEditMode && isEditable && (
                  <ToolBtn onClick={startEdit}>編輯</ToolBtn>
                )}
                {!inEditMode && picked.source === "user" && (
                  <ToolBtn onClick={() => onDelete(picked)}>刪除</ToolBtn>
                )}
                {inEditMode && (
                  <>
                    <ToolBtn onClick={() => setDraft(null)} disabled={saving}>取消</ToolBtn>
                    <ToolBtn primary onClick={saveDraft} disabled={saving}>
                      {saving ? "saving…" : "儲存 workflow"}
                    </ToolBtn>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function WorkflowStageList({
  stages, agents, stageInfo, editable,
  onMoveStage, onRemoveStage, onCycleCollab, onAddBinding, onRemoveBinding, onSetBindingRole,
  onToggleDependency,
}: {
  stages: WorkflowStage[];
  agents: Agent[];
  stageInfo: Record<string, { label: string; description: string }>;
  editable: boolean;
  onMoveStage: (idx: number, dir: -1 | 1) => void;
  onRemoveStage: (idx: number) => void;
  onCycleCollab: (idx: number) => void;
  onAddBinding: (stageIdx: number, agentId: string) => void;
  onRemoveBinding: (stageIdx: number, bindIdx: number) => void;
  onSetBindingRole: (stageIdx: number, bindIdx: number, role: ApiCollabRole) => void;
  onToggleDependency?: (stageIdx: number, depId: string) => void;
}) {
  if (stages.length === 0) {
    return (
      <div className="border border-dashed border-[var(--rule-dark)] py-8 text-center font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        無 stage{editable ? "，點下方加入第一個" : ""}
      </div>
    );
  }
  return (
    <ol className="space-y-2">
      {stages.map((s, i) => (
        <li key={s.stage_id} className="flex flex-col gap-3 border border-[var(--rule-dark)] bg-[var(--bg-elev)]/60 p-3">
          <div className="flex items-center gap-3">
            <span className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--ink-muted)]">{String(i + 1).padStart(2, "0")}</span>
            <code className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{s.stage_id}</code>
            {stageInfo[s.stage_id]?.label && <span className="text-[12px] text-[#cdd4df]">{stageInfo[s.stage_id].label}</span>}
            <span className="ml-auto flex items-center gap-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
              <span>collab</span>
              {editable ? (
                <button
                  onClick={() => onCycleCollab(i)}
                  title="點擊切換 collab_mode"
                  className="border border-[var(--polaris-dim)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--polaris)]"
                >
                  {s.collab_mode}
                </button>
              ) : <CollabModePill mode={s.collab_mode} />}
            </span>
            {!editable && (
              <span className="flex items-center gap-2 border-l border-[var(--rule-dark)] pl-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                <span>depends_on</span>
                {s.depends_on.length === 0 ? <span className="text-[#5e6878]">(root)</span> : s.depends_on.map((d) => (
                  <code key={d} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-1.5 py-0.5 text-[#cdd4df]">{d}</code>
                ))}
              </span>
            )}
            {editable && (
              <>
                <button title="上移" onClick={() => onMoveStage(i, -1)} className="grid h-6 w-6 place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">↑</button>
                <button title="下移" onClick={() => onMoveStage(i, 1)} className="grid h-6 w-6 place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]">↓</button>
                <button title="刪除" onClick={() => onRemoveStage(i)} className="grid h-6 w-6 place-items-center border border-[var(--rule-dark)] text-[var(--ink-muted)] transition hover:border-[#f47171] hover:text-[#f47171]">×</button>
              </>
            )}
          </div>
          {stageInfo[s.stage_id]?.description && (
            <p className="pl-7 text-[11.5px] leading-5 text-[var(--ink-muted)]">{stageInfo[s.stage_id].description}</p>
          )}
          {/* 缺口3：depends_on multi-select —— 只列「排在前面」的 stage（天然防環）*/}
          {editable && (
            <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--rule-dark)] pt-2 pl-8">
              <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">depends_on</span>
              {i === 0 ? (
                <span className="font-[family-name:var(--font-mono)] text-[10px] text-[#5e6878]">(root · 無前置 stage)</span>
              ) : (
                stages.slice(0, i).map((prev) => {
                  const checked = s.depends_on.includes(prev.stage_id);
                  return (
                    <button
                      key={prev.stage_id}
                      onClick={() => onToggleDependency?.(i, prev.stage_id)}
                      className={`inline-flex items-center gap-1 border px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] tracking-wider transition ${
                        checked
                          ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_18%,transparent)] text-[var(--polaris)]"
                          : "border-[var(--rule-dark)] text-[#cdd4df] hover:border-[#4a5468]"
                      }`}
                    >
                      <span className={`grid h-3 w-3 place-items-center border ${checked ? "border-[var(--polaris)] bg-[var(--polaris)]" : "border-[#2e3441]"}`}>
                        {checked && <span className="text-[8px] leading-none text-white">✓</span>}
                      </span>
                      {prev.stage_id}
                    </button>
                  );
                })
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5 pl-8">
            <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">agents</span>
            {s.agent_bindings.map((b, bi) => (
              <BindingChip
                key={b.agent_id + bi}
                binding={b}
                editable={editable}
                roleOptions={bindingRoleOrder(s.stage_id)}
                onSetRole={(role) => onSetBindingRole(i, bi, role)}
                onRemove={() => onRemoveBinding(i, bi)}
              />
            ))}
            {editable && (
              <BindingPicker
                agents={agents}
                excludeIds={s.agent_bindings.map((b) => b.agent_id)}
                onPick={(aid) => onAddBinding(i, aid)}
              />
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

// binding role 依 stage 區分詞彙：implement 是 pipeline 步驟（lead→rd→tester→reviewer），
// 其餘 stage 是 collab 角色（lead/peer/subagent）。決定 chip 點擊循環的順序。
function bindingRoleOrder(stageId: string): ApiCollabRole[] {
  return stageId === "implement"
    ? ["lead", "rd", "tester", "reviewer"]
    : ["lead", "peer", "subagent"];
}

const ROLE_COLOR: Record<string, string> = {
  lead: "#5b8cff", peer: "#f59e0b", subagent: "#a78bfa",
  rd: "#34d399", tester: "#22d3ee", reviewer: "#f472b6",
};

function BindingChip({ binding, editable, roleOptions, onSetRole, onRemove }: {
  binding: ApiAgentBinding;
  editable: boolean;
  roleOptions: ApiCollabRole[];
  onSetRole: (role: ApiCollabRole) => void;
  onRemove: () => void;
}) {
  const color = ROLE_COLOR[binding.role] ?? "#a78bfa";
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);
  return (
    <span ref={ref} className="relative inline-flex items-center gap-1 border px-2 py-0.5" style={{ borderColor: color }}>
      <code className="font-[family-name:var(--font-mono)] text-[10px] text-[#e6ecf5]">{binding.agent_id}</code>
      {editable ? (
        <button
          onClick={() => setOpen((o) => !o)}
          title="選擇 role"
          className="inline-flex items-center gap-0.5 border-b border-dotted font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider"
          style={{ color, borderColor: color }}
        >
          {binding.role}<span className="text-[7px] leading-none">▾</span>
        </button>
      ) : (
        <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider" style={{ color }}>{binding.role}</span>
      )}
      {editable && open && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-30 min-w-[120px] border border-[var(--paper-edge)] bg-[var(--paper)] shadow-anvil">
          {roleOptions.map((r) => (
            <button
              key={r}
              onClick={() => { onSetRole(r); setOpen(false); }}
              className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider transition hover:bg-[var(--bg-elev)] ${r === binding.role ? "bg-[var(--bg-elev)]/60" : ""}`}
              style={{ color: ROLE_COLOR[r] ?? "#a78bfa" }}
            >
              <span className="inline-block h-1.5 w-1.5" style={{ background: ROLE_COLOR[r] ?? "#a78bfa" }} />
              {r}
              {r === binding.role && <span className="ml-auto text-[var(--ink-muted)]">✓</span>}
            </button>
          ))}
        </div>
      )}
      {editable && (
        <button onClick={onRemove} title="移除" className="text-[var(--ink-muted)] hover:text-[#f47171]">×</button>
      )}
    </span>
  );
}

function BindingPicker({ agents, excludeIds, onPick }: {
  agents: Agent[];
  excludeIds: string[];
  onPick: (agentId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);
  const choices = agents.filter((a) => !excludeIds.includes(a.agent_id));
  return (
    <div ref={ref} className="relative inline-block">
      <button
        onClick={() => setOpen((o) => !o)}
        className="border border-dashed border-[var(--rule-dark)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]"
      >
        ＋ add
      </button>
      {open && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-30 max-h-[40vh] min-w-[220px] overflow-y-auto border border-[var(--paper-edge)] bg-[var(--paper)] shadow-anvil">
          {choices.length === 0 ? (
            <div className="px-3 py-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">無可加 agent</div>
          ) : choices.map((a) => (
            <button
              key={a.agent_id}
              onClick={() => { onPick(a.agent_id); setOpen(false); }}
              className="flex w-full items-baseline gap-2 px-3 py-2 text-left transition hover:bg-[var(--bg-elev)]"
            >
              <code className="font-[family-name:var(--font-mono)] text-[11px] text-[var(--polaris)]">{a.agent_id}</code>
              <span className="font-[family-name:var(--font-sans)] text-[11px] text-[#cdd4df]">{a.name}</span>
              <span className="ml-auto font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[var(--ink-muted)]">{a.role}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AddStagePicker({ availableStages, stageInfo, onAdd }: { availableStages: string[]; stageInfo: Record<string, { label: string; description: string }>; onAdd: (sid: string) => void }) {
  if (availableStages.length === 0) {
    return (
      <div className="mt-3 border border-dashed border-[var(--rule-dark)] py-2 text-center font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
        所有可用 stage 都已加入
      </div>
    );
  }
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)] self-center">＋ add stage</span>
      {availableStages.map((sid) => (
        <button
          key={sid}
          onClick={() => onAdd(sid)}
          title={stageInfo[sid]?.description || sid}
          className="border border-dashed border-[var(--rule-dark)] px-2.5 py-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[#cdd4df] transition hover:border-[var(--polaris)] hover:text-[var(--polaris)]"
        >
          {stageInfo[sid]?.label || sid}
        </button>
      ))}
    </div>
  );
}


// ============================== Agents view（M3：API-driven CRUD）==============================
const ROLE_COLOR_REAL: Record<string, string> = {
  lead: "#5b8cff",
  peer: "#f59e0b",
  subagent: "#a78bfa",
};

function AgentsView({ agents, onNew, onEdit, onDelete }: {
  agents: Agent[];
  onNew: () => void;
  onEdit: (a: Agent) => void;
  onDelete: (a: Agent) => void;
}) {
  // 用 role（綁的 stage_id）分組
  const roles = Array.from(new Set(agents.map((a) => a.role)));
  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Agents" sub="完整客製化 AI agent · 內建 seed + user 可覆寫" right={
        <button
          onClick={onNew}
          className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-white transition hover:bg-[var(--polaris-hi)]"
        >
          ＋ new agent
        </button>
      } />
      <div className="min-h-0 flex-1 space-y-7 overflow-y-auto p-8">
        {agents.length === 0 && (
          <div className="border border-dashed border-[var(--rule-dark)] py-12 text-center font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            無 agents（loading…）
          </div>
        )}
        {roles.map((role) => {
          const inRole = agents.filter((a) => a.role === role);
          return (
            <div key={role}>
              <div className="mb-3 flex items-baseline gap-3 border-b border-[var(--rule-dark)] pb-2">
                <span className="font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--polaris)]">
                  role · {role}
                </span>
                <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
                  {inRole.length} {inRole.length === 1 ? "agent" : "agents"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-4">
                {inRole.map((a) => (
                  <AgentCard key={a.agent_id} agent={a} onEdit={() => onEdit(a)} onDelete={() => onDelete(a)} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgentCard({ agent, onEdit, onDelete }: {
  agent: Agent;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const isUser = agent.source === "user";
  const initials = agent.name
    .split(/\s+|[-_]/)
    .filter(Boolean)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("") || agent.agent_id[0]?.toUpperCase();
  return (
    <div className={`flex flex-col border bg-[var(--bg-elev)]/40 p-5 ${agent.enabled ? "border-[var(--rule-dark)]" : "border-[var(--rule-dark)] opacity-60"}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center border border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] font-[family-name:var(--font-display)] text-[14px] font-bold text-[var(--polaris)]">
            {initials}
          </span>
          <div className="leading-tight">
            <div className="font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">{agent.name}</div>
            <code className="mt-1 inline-block font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[var(--ink-muted)]">{agent.agent_id}</code>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {isUser ? <Pill color="muted">USER</Pill> : <Pill color="approved">BUILTIN</Pill>}
          {agent.enabled ? <Pill color="approved">ENABLED</Pill> : <Pill color="muted">DISABLED</Pill>}
        </div>
      </div>
      <div className="mb-3 grid grid-cols-3 gap-3 border-y border-[var(--rule-dark)] py-3">
        <KV k="ROLE" v={agent.role} />
        <KV k="MODEL" v={agent.model_choice} />
        <KV k="MAX ITER" v={String(agent.max_iterations)} />
      </div>
      <div className="mb-3">
        <SectionLabel>SYSTEM PROMPT</SectionLabel>
        <div className="line-clamp-3 font-[family-name:var(--font-mono)] text-[12px] leading-5 text-[#cdd4df]">
          {agent.system_prompt || <span className="text-[var(--ink-muted)]">（未設定）</span>}
        </div>
      </div>
      {agent.tools.length > 0 && (
        <div className="mb-3">
          <SectionLabel>TOOLS</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {agent.tools.map((t) => (
              <span key={t} className="border border-[var(--paper-edge)] bg-[var(--bg)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--polaris)]">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
      {agent.skills && agent.skills.length > 0 && (
        <div className="mb-3">
          <SectionLabel>SKILLS</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {agent.skills.map((s) => (
              <span key={s.skill_id} className="border border-[var(--polaris-dim)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--polaris)]">
                {s.name}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="mt-auto flex items-center justify-between border-t border-[var(--rule-dark)] pt-3">
        <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
          source · {agent.source}
        </span>
        <div className="flex gap-2">
          <ToolBtn onClick={onEdit}>編輯</ToolBtn>
          {isUser && <ToolBtn onClick={onDelete}>刪除</ToolBtn>}
        </div>
      </div>
    </div>
  );
}

// ============================== Skills view ==============================
function SkillsView({ skills, onNew, onEdit, onDelete }: {
  skills: Skill[];
  onNew: () => void;
  onEdit: (s: Skill) => void;
  onDelete: (s: Skill) => void;
}) {
  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Skills" sub="可組合、可注入 prompt 的技能片段 · 綁到 agent 生效" right={
        <button
          onClick={onNew}
          className="border border-[var(--polaris)] bg-[var(--polaris)] px-4 py-2 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-white transition hover:bg-[var(--polaris-hi)]"
        >
          ＋ new skill
        </button>
      } />
      <div className="min-h-0 flex-1 overflow-y-auto p-8">
        {skills.length === 0 ? (
          <div className="border border-dashed border-[var(--rule-dark)] py-12 text-center font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            無 skills（按上方 ＋ new skill 新建）
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            {skills.map((s) => (
              <SkillCard key={s.skill_id} skill={s} onEdit={() => onEdit(s)} onDelete={() => onDelete(s)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SkillCard({ skill, onEdit, onDelete }: {
  skill: Skill;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const initials = skill.name
    .split(/\s+|[-_]/)
    .filter(Boolean)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("") || skill.skill_id[0]?.toUpperCase();
  return (
    <div className="flex flex-col border border-[var(--rule-dark)] bg-[var(--bg-elev)]/40 p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center border border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_10%,transparent)] font-[family-name:var(--font-display)] text-[14px] font-bold text-[var(--polaris)]">
            {initials}
          </span>
          <div className="leading-tight">
            <div className="font-[family-name:var(--font-display)] text-[16px] font-semibold text-[#e6ecf5]">{skill.name}</div>
            <code className="mt-1 inline-block font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[var(--ink-muted)]">{skill.skill_id}</code>
          </div>
        </div>
        <Pill color="muted">v{skill.version}</Pill>
      </div>
      {skill.description && (
        <div className="mb-3">
          <SectionLabel>DESCRIPTION</SectionLabel>
          <div className="line-clamp-2 font-[family-name:var(--font-sans)] text-[12px] leading-5 text-[#cdd4df]">{skill.description}</div>
        </div>
      )}
      <div className="mb-3">
        <SectionLabel>BODY（注入 prompt）</SectionLabel>
        <div className="line-clamp-3 font-[family-name:var(--font-mono)] text-[12px] leading-5 text-[#cdd4df]">
          {skill.body || <span className="text-[var(--ink-muted)]">（未設定）</span>}
        </div>
      </div>
      <div className="mt-auto flex items-center justify-end gap-2 border-t border-[var(--rule-dark)] pt-3">
        <ToolBtn onClick={onEdit}>編輯</ToolBtn>
        <ToolBtn onClick={onDelete}>刪除</ToolBtn>
      </div>
    </div>
  );
}

// ============================== Plugins view ==============================
// ============================== Plugins view（M4：API-driven enable/disable）==============================
function PluginsView({ plugins, onToggle }: {
  plugins: Plugin[];
  onToggle: (id: string, enabled: boolean) => void;
}) {
  const [showSystem, setShowSystem] = useState(false);
  const loaded = plugins.filter((p) => p.enabled && !p.load_error).length;
  // 「你的功能」= 提供你在 Workspace 操作的 stage / 流程；其餘 = 背景零件（agent / 模型 / 交付）
  const isFeature = (p: Plugin) => p.provides.stages.length > 0 || p.provides.workflows.length > 0;
  const features = plugins.filter(isFeature).sort((a, b) => Number(a.builtin) - Number(b.builtin));
  const system = plugins.filter((p) => !isFeature(p));
  return (
    <div className="rise-3 flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader title="Plugins · 擴充功能" sub="像手機 App：裝上去才有對應功能。下方「你的功能」是你會用到的分析能力；「系統零件」是背景機件，平常不用管。" right={
        <div className="flex items-center gap-3 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
          <span>{plugins.length} 已安裝</span>
          <span className="h-1 w-1 rounded-full bg-[var(--ink-muted)]" />
          <span className="text-[var(--approved)]">{loaded} 啟用中</span>
        </div>
      } />
      <div className="min-h-0 flex-1 overflow-y-auto p-8">
        {plugins.length === 0 ? (
          <div className="border border-dashed border-[var(--rule-dark)] py-12 text-center font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            無 plugins（loading…）
          </div>
        ) : (
          <>
            <SectionLabel>你的功能 · 你會用到的分析能力</SectionLabel>
            <div className="mt-3 grid grid-cols-2 gap-4">
              {features.map((p) => (
                <PluginCard key={p.id} plugin={p} onToggle={onToggle} />
              ))}
            </div>

            {system.length > 0 && (
              <>
                <button
                  onClick={() => setShowSystem((s) => !s)}
                  className="mt-8 flex w-full items-center gap-3 border-t border-[var(--rule-dark)] pt-5 text-left font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)] transition hover:text-[#cdd4df]"
                >
                  <span className="text-[var(--polaris)]">{showSystem ? "▾" : "▸"}</span>
                  <span>系統零件 · {system.length}</span>
                  <span className="normal-case tracking-normal text-[#5e6878]">共用的背景機件（模型接口 / 預設 agent / 交付整合）— 平常不用管</span>
                </button>
                {showSystem && (
                  <div className="mt-4 grid grid-cols-2 gap-4">
                    {system.map((p) => (
                      <PluginCard key={p.id} plugin={p} onToggle={onToggle} />
                    ))}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function PluginCard({ plugin, onToggle }: {
  plugin: Plugin;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  const p = plugin;
  const hasProvides = p.provides.stages.length || p.provides.workflows.length || p.provides.agents.length || p.provides.integrations.length;
  // builtin 不可停用；toggle 只在非 builtin 開放
  const canToggle = !p.builtin;
  return (
    <div className={`flex flex-col border p-5 ${p.enabled ? "border-[var(--rule-dark)] bg-[var(--bg-elev)]/40" : "border-[var(--rule-dark)] bg-[var(--bg-elev)]/20 opacity-70"}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <code className="font-[family-name:var(--font-mono)] text-[11px] tracking-wider text-[var(--polaris)]">{p.id}</code>
            {p.builtin ? <Pill color="approved">BUILTIN</Pill> : <Pill color="muted">3RD-PARTY</Pill>}
            <span className="border border-[var(--rule-dark)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-wider text-[var(--ink-muted)]">
              {p.discovery === "entry_point" ? "pip" : "dir"}
            </span>
          </div>
          <h3 className="mt-1 font-[family-name:var(--font-display)] text-[18px] font-semibold leading-tight text-[#e6ecf5]">{p.name}</h3>
          <div className="mt-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">version · <span className="text-[#cdd4df]">{p.version}</span></div>
        </div>
        <button
          onClick={() => canToggle && onToggle(p.id, !p.enabled)}
          disabled={!canToggle}
          title={canToggle ? (p.enabled ? "停用此 plugin" : "啟用此 plugin") : "內建 plugin 不可停用"}
          className={`flex shrink-0 items-center gap-2 ${canToggle ? "cursor-pointer" : "cursor-not-allowed opacity-50"}`}
        >
          <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{p.enabled ? "enabled" : "disabled"}</span>
          <span className={`relative inline-block h-4 w-7 transition ${p.enabled ? "bg-[var(--polaris)]" : "bg-[var(--rule-dark)]"}`}>
            <span className={`absolute top-0.5 h-3 w-3 bg-white transition-all ${p.enabled ? "left-3.5" : "left-0.5"}`} />
          </span>
        </button>
      </div>
      <p className="mb-4 text-[13px] leading-6 text-[#97a0b3]">{p.description}</p>
      {hasProvides ? (
        <div className="mb-4 space-y-2">
          <SectionLabel>PROVIDES</SectionLabel>
          <div className="flex flex-col gap-1.5">
            {(["stages", "workflows", "agents", "integrations"] as const).map((cat) =>
              p.provides[cat].length > 0 ? (
                <div key={cat} className="flex items-baseline gap-2">
                  <span className="w-24 shrink-0 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">{cat}</span>
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
      ) : (
        !p.enabled && (
          <div className="mb-4 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
            （停用中 · 啟用後顯示貢獻）
          </div>
        )
      )}
      {p.load_error && (
        <div className="mb-3 border-l-2 border-[#f59e0b] bg-[color-mix(in_oklab,#f59e0b_8%,transparent)] px-3 py-2 font-[family-name:var(--font-mono)] text-[10px] tracking-wider text-[#f59e0b]">
          load_error · {p.load_error}
        </div>
      )}
      <div className="mt-auto flex items-center justify-between border-t border-[var(--rule-dark)] pt-3 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
        <span>discovery · <span className="text-[#cdd4df]">{p.discovery}</span></span>
        {p.requires_rebuild && <Pill color="muted">requires rebuild</Pill>}
      </div>
    </div>
  );
}

// ============================== Chat panel ==============================
// ============================== Chat panel（真實 per-thread stage chat）==============================
function ChatPanel({ thread, stageId, stageLabel, modelChoice, onArtifactUpdated }: {
  thread: string | null;
  stageId: string;
  stageLabel: string;
  modelChoice: string;
  onArtifactUpdated?: (content: string) => void;
}) {
  const [msgs, setMsgs] = useState<StageChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // 載入該 thread + stage 的真實對話歷史（新 thread / 無對話 → 空）
  useEffect(() => {
    if (!thread) { setMsgs([]); return; }
    let on = true;
    fetchStageHistory(stageId, thread)
      .then((m) => { if (on) setMsgs(m); })
      .catch((e) => { if (on) setErr(`讀取對話失敗：${(e as Error).message}`); });
    return () => { on = false; };
  }, [thread, stageId]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy]);

  const send = async (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || !thread || busy) return;
    setErr(null);
    if (!override) setInput("");
    setMsgs((prev) => [...prev, { role: "user", content: text, created_at: null }]);
    setBusy(true);
    try {
      const r = await stageChat(stageId, thread, modelChoice, text);
      setMsgs((prev) => [...prev, { role: "assistant", content: r.ai_response, created_at: null }]);
      if (r.updated_content && onArtifactUpdated) onArtifactUpdated(r.updated_content);
    } catch (e) {
      const msg = (e as Error).message;
      setErr(`送出失敗：${msg}`);
      setMsgs((prev) => [...prev, { role: "assistant", content: `⚠ ${msg}`, created_at: null }]);
    } finally {
      setBusy(false);
    }
  };

  const disabled = !thread;

  return (
    <section className="rise-4 flex w-[400px] shrink-0 flex-col border-l border-[var(--rule-dark)] bg-[var(--bg-elev)]/40">
      <div className="border-b border-[var(--rule-dark)] px-6 py-4">
        <div className="flex items-center justify-between">
          <h3 className="font-[family-name:var(--font-display)] text-[17px] font-semibold text-[#e6ecf5]">{stageLabel}</h3>
          <span className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
            {msgs.length} {msgs.length === 1 ? "msg" : "msgs"} · {modelChoice}
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
        {msgs.length === 0 && !busy ? (
          <div className="grid h-full place-items-center px-4 text-center">
            <div>
              <div className="mb-1 font-[family-name:var(--font-display)] text-[14px] text-[#cdd4df]">尚無對話</div>
              <p className="text-[12px] leading-5 text-[var(--ink-muted)]">
                {disabled ? "選一個專案開始。" : "在下方輸入需求或要求修正，與此 stage 的 agent 討論。"}
              </p>
            </div>
          </div>
        ) : (
          <>
            {msgs.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[88%] whitespace-pre-wrap border-l-2 border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_12%,transparent)] px-4 py-2.5 text-[13px] leading-6 text-[#e6ecf5]">
                    {m.content}
                  </div>
                </div>
              ) : (
                <AssistantMessage
                  key={i}
                  content={m.content}
                  stageId={stageId}
                  onPick={(label) => void send(label)}
                  disabled={busy}
                />
              ),
            )}
            {busy && (
              <div className="flex items-center gap-2 font-[family-name:var(--font-mono)] text-[11px] text-[var(--ink-muted)]">
                <span className="pulse-star inline-block h-1.5 w-1.5 rounded-full bg-[var(--polaris)]" /> agent 回覆中…
              </div>
            )}
            <div ref={endRef} />
          </>
        )}
      </div>

      <div className="border-t border-[var(--rule-dark)] p-4">
        {err && <div className="mb-2 font-[family-name:var(--font-mono)] text-[11px] text-[#e0608a]">{err}</div>}
        <div className="flex items-end gap-2 border border-[var(--rule-dark)] bg-[var(--bg)] px-3 py-2.5 focus-within:border-[var(--polaris)]">
          <textarea
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(); } }}
            disabled={disabled || busy}
            placeholder={disabled ? "選一個專案……" : "補充需求 / 要求修正……"}
            className="flex-1 resize-none bg-transparent text-[13px] text-[#cdd4df] outline-none placeholder:text-[var(--ink-muted)] disabled:opacity-50"
          />
          <button
            onClick={() => void send()}
            disabled={disabled || busy || !input.trim()}
            className="grid h-7 w-7 shrink-0 place-items-center bg-[var(--polaris)] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-40"
          >
            <span className="-mt-0.5 text-sm">↵</span>
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
          <span>⌘↵ send · ↵ newline</span>
          <span>{stageId}</span>
        </div>
      </div>
    </section>
  );
}

// ============================== Chat questionnaire（quick-reply 卡片）==============================
type QQuestion = { id?: string; category?: string; question: string; options?: string[]; multi?: boolean };
type QObj = { title?: string; questions: QQuestion[] };

// 從 assistant 內容抽出 json-questionnaire block；解析失敗 / 無 block → questionnaire=null（容錯，純文字照顯示）
function parseQuestionnaire(content: string): { text: string; questionnaire: QObj | null } {
  const m = /```json-questionnaire\s*\n([\s\S]*?)\n```/.exec(content);
  if (!m) return { text: content, questionnaire: null };
  let q: QObj | null = null;
  try {
    const parsed = JSON.parse(m[1]);
    if (parsed && Array.isArray(parsed.questions)) q = parsed as QObj;
  } catch {
    q = null;
  }
  if (!q) return { text: content, questionnaire: null };
  const text = (content.slice(0, m.index) + content.slice(m.index + m[0].length)).trim();
  return { text, questionnaire: q };
}

function AssistantMessage({ content, stageId, onPick, disabled }: {
  content: string;
  stageId: string;
  onPick: (label: string) => void;
  disabled: boolean;
}) {
  const { text, questionnaire } = parseQuestionnaire(content);
  return (
    <div className="flex flex-col items-start gap-1.5">
      <span className="font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.2em] text-[var(--ink-muted)]">{stageId} · agent</span>
      {text && (
        <div className="max-w-[92%] whitespace-pre-wrap border-l-2 border-[color-mix(in_oklab,var(--polaris)_45%,transparent)] px-3 py-1 text-[13px] leading-6 text-[#cdd4df]">
          {text}
        </div>
      )}
      {questionnaire && <QuestionnaireCard q={questionnaire} onPick={onPick} disabled={disabled} />}
    </div>
  );
}

function QuestionnaireCard({ q, onPick, disabled }: {
  q: QObj;
  onPick: (label: string) => void;
  disabled: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [sent, setSent] = useState(false);
  const keyOf = (qq: QQuestion, idx: number) => qq.id ?? String(idx);
  // 多選判定：明確 multi 欄位優先，否則看題目文字（可多選 / 複選 / multiple / select all）
  const isMulti = (qq: QQuestion) => qq.multi ?? /多選|複選|multiple|multi-?select|select all/i.test(qq.question);
  const answeredCount = q.questions.filter((qq, idx) => (answers[keyOf(qq, idx)] ?? []).length > 0).length;

  // 點選 = 選取（不送出）；按「送出」才把所有已答題一次回給 agent。多選題以「、」串接。
  const submit = () => {
    if (disabled || sent) return;
    const lines = q.questions
      .map((qq, idx) => {
        const a = answers[keyOf(qq, idx)] ?? [];
        return a.length ? `${qq.question}：${a.join("、")}` : null;
      })
      .filter((x): x is string => x !== null);
    if (lines.length === 0) return;
    setSent(true);
    onPick(lines.join("\n"));
  };

  return (
    <div className="mt-1 w-[92%] space-y-3 border border-[var(--rule-dark)] bg-[var(--bg)]/40 px-3 py-3">
      {q.title && (
        <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">{q.title}</div>
      )}
      {q.questions.map((qq, idx) => {
        const key = keyOf(qq, idx);
        return (
          <div key={key} className="space-y-1.5">
            <div className="text-[12.5px] leading-5 text-[#cdd4df]">
              {qq.category && (
                <span className="mr-1.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-wider text-[var(--polaris)]">{qq.category}</span>
              )}
              {qq.question}
            </div>
            {qq.options && qq.options.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {qq.options.map((opt) => {
                  const selected = (answers[key] ?? []).includes(opt);
                  return (
                    <button
                      key={opt}
                      type="button"
                      disabled={disabled || sent}
                      onClick={() => setAnswers((a) => {
                        const c = a[key] ?? [];
                        if (isMulti(qq)) {                              // 多選 → toggle 進/出
                          return { ...a, [key]: c.includes(opt) ? c.filter((x) => x !== opt) : [...c, opt] };
                        }
                        return { ...a, [key]: c.includes(opt) ? [] : [opt] };   // 單選 → 取代/取消
                      })}
                      className={`border px-2.5 py-1 font-[family-name:var(--font-mono)] text-[11px] transition disabled:cursor-not-allowed disabled:opacity-50 ${
                        selected
                          ? "border-[var(--polaris)] bg-[color-mix(in_oklab,var(--polaris)_24%,transparent)] text-[#e6ecf5]"
                          : "border-[var(--polaris-dim)] bg-[color-mix(in_oklab,var(--polaris)_8%,transparent)] text-[var(--polaris)] hover:bg-[color-mix(in_oklab,var(--polaris)_18%,transparent)]"
                      }`}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      <div className="flex items-center justify-between border-t border-[var(--rule-dark)] pt-2">
        <span className="font-[family-name:var(--font-mono)] text-[10px] text-[var(--ink-muted)]">
          {sent ? "已送出" : `已答 ${answeredCount}/${q.questions.length}`}
        </span>
        <button
          type="button"
          disabled={disabled || sent || answeredCount === 0}
          onClick={submit}
          className="border border-[var(--polaris)] bg-[var(--polaris)] px-3 py-1 font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.18em] text-white transition hover:bg-[var(--polaris-hi)] disabled:opacity-40"
        >
          送出
        </button>
      </div>
    </div>
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

/**
 * BuildSeal —— 「印章」浮水印，顯示版本資訊。
 *
 * 問題：原本固定 right-5 bottom-3，會跟 ChatPanel footer 的「↵ send · ⌘↵ refine」
 * 與「tokens 1,234 / 200k」overlap（兩個都 absolute 在右下）。
 * 修法：workspace（有 ChatPanel）時不顯示；其他 view（workflows/agents/plugins）顯示。
 */
function BuildSeal({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div className="pointer-events-none absolute right-5 bottom-3 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.22em] text-[var(--ink-muted)] opacity-60">
      build · m2.2026.05 · fix/baseline-cleanup
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
