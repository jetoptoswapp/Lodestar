// frontend/lib/api.ts
// M3：Workflow / Agent CRUD types + helpers。
//
// 抽到獨立模組讓 page.tsx 不再越長越大；統一 fetch 錯誤處理。

export const API_BASE =
  (typeof window !== "undefined" &&
    (window as Window & { __LODESTAR_API__?: string }).__LODESTAR_API__) ||
  "http://localhost:8723";

export type CollabMode = "single" | "discussion" | "dispatch";
export type CollabRole = "lead" | "peer" | "subagent";

export type AgentBinding = {
  agent_id: string;
  role: CollabRole;
};

export type WorkflowStage = {
  stage_id: string;
  depends_on: string[];
  agent_bindings: AgentBinding[];
  collab_mode: CollabMode;
};

export type Workflow = {
  id: string;
  label: string;
  description: string;
  stages: WorkflowStage[];
  source: "builtin" | "user";
  source_plugin: string | null;
  created_at: number | null;
};

export type Agent = {
  agent_id: string;
  name: string;
  role: string;
  system_prompt: string;
  model_choice: string;
  max_iterations: number;
  enabled: boolean;
  tools: string[];
  source: "builtin" | "user";
  created_at: number | null;
  updated_at: number | null;
};

export type PluginProvides = {
  stages: string[];
  workflows: string[];
  agents: string[];
  integrations: string[];
};

export type Plugin = {
  id: string;
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  provides: PluginProvides;
  requires_rebuild: boolean;
  load_error: string | null;
  builtin: boolean;
  discovery: "directory" | "entry_point";
};

// ============================================================
//  Fetch helper
// ============================================================
export async function apiCall<T = unknown>(path: string, init?: RequestInit): Promise<T> {
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
  // Handle 204 / empty
  const text = await r.text();
  return (text ? JSON.parse(text) : (undefined as unknown)) as T;
}

// ============================================================
//  Workflows CRUD
// ============================================================
export async function fetchWorkflows(): Promise<Workflow[]> {
  const r = await apiCall<{ workflows: Workflow[] }>("/api/workflows");
  return r.workflows;
}

export async function createWorkflow(wf: Omit<Workflow, "source" | "source_plugin" | "created_at">): Promise<Workflow> {
  return apiCall<Workflow>("/api/workflows", {
    method: "POST",
    body: JSON.stringify(wf),
  });
}

export async function updateWorkflow(id: string, wf: Omit<Workflow, "source" | "source_plugin" | "created_at">): Promise<Workflow> {
  return apiCall<Workflow>(`/api/workflows/${id}`, {
    method: "PUT",
    body: JSON.stringify(wf),
  });
}

export async function deleteWorkflowApi(id: string): Promise<void> {
  await apiCall(`/api/workflows/${id}`, { method: "DELETE" });
}

// ============================================================
//  Agents CRUD
// ============================================================
export async function fetchAgents(): Promise<Agent[]> {
  const r = await apiCall<{ agents: Agent[] }>("/api/agents");
  return r.agents;
}

export async function createAgent(agent: Omit<Agent, "source" | "created_at" | "updated_at">): Promise<Agent> {
  return apiCall<Agent>("/api/agents", {
    method: "POST",
    body: JSON.stringify(agent),
  });
}

export async function updateAgent(id: string, agent: Omit<Agent, "source" | "created_at" | "updated_at">): Promise<Agent> {
  return apiCall<Agent>(`/api/agents/${id}`, {
    method: "PUT",
    body: JSON.stringify(agent),
  });
}

export async function deleteAgentApi(id: string): Promise<void> {
  await apiCall(`/api/agents/${id}`, { method: "DELETE" });
}

// ============================================================
//  Per-thread workflow binding
// ============================================================
export async function setProjectWorkflow(threadId: string, workflowId: string | null): Promise<void> {
  await apiCall(`/api/projects/${threadId}/workflow`, {
    method: "POST",
    body: JSON.stringify({ workflow_id: workflowId }),
  });
}

// ============================================================
//  Plugins（M4）
// ============================================================
export async function fetchPlugins(): Promise<Plugin[]> {
  const r = await apiCall<{ plugins: Plugin[] }>("/api/plugins");
  return r.plugins;
}

export async function togglePlugin(id: string, enabled: boolean): Promise<Plugin> {
  return apiCall<Plugin>(`/api/plugins/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}
