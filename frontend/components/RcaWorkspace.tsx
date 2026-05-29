"use client";
// RcaWorkspace —— RCA 領域的 catalog-driven 工作區（與 PRD/arch/stories 流程並行、互不干擾）。
// 由 page.tsx 在「thread 綁定的 workflow 是 rca_*」時渲染。自足：用 lib/api 的通用 stage helpers。
//
// 定位：AI = Copilot, not Judge。候選根因表 + 信心刻度 + 「待工程師確認」護欄帶 +
// 每列 Confirm/Reject/Needs-more-data（PoC 諮詢用、local state）。causal 用 Mermaid。
// rca_plan：顯示 AI 規劃的 workflow，核准後 Apply 成真 workflow 執行。

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  applyRcaPlan,
  fetchStageState,
  fetchStageStatuses,
  fetchStages,
  stageApprove,
  stageGenerate,
  stageRefine,
  type StageCatalogItem,
  type Workflow,
} from "@/lib/api";

const MermaidDiagram = dynamic(() => import("@/components/MermaidDiagram"), {
  ssr: false,
  loading: () => <div style={{ color: "var(--ink-muted)", fontSize: 12 }}>loading diagram…</div>,
});

// ---------- 小工具 ----------
const CANDIDATE_STAGES = new Set(["rca_analysis", "rca_synthesis"]);
const GUARD_STAGES = new Set(["rca_analysis", "rca_synthesis", "rca_plan"]);

function confColor(text: string): string {
  const t = text.toLowerCase();
  if (t.includes("medium-high") || t.includes("中高")) return "#e6c07a";
  if (t.includes("high") || t.includes("高")) return "#f0a868";
  if (t.includes("medium") || t.includes("中")) return "#7fa6ff";
  if (t.includes("low") || t.includes("低")) return "#6b7488";
  return "#7fa6ff";
}

// 行內：**bold** 與 `code`
function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[2] !== undefined) {
      nodes.push(<strong key={`${keyBase}-b${i}`} style={{ color: "var(--ink)" }}>{m[2]}</strong>);
    } else if (m[3] !== undefined) {
      nodes.push(
        <code key={`${keyBase}-c${i}`} style={{ fontFamily: "var(--font-mono)", color: "var(--polaris-hi)", fontSize: "0.92em" }}>{m[3]}</code>,
      );
    }
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "table"; headers: string[]; rows: string[][] }
  | { kind: "code"; lang: string; body: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "para"; text: string };

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}
const isSep = (line: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-");

function parseBlocks(md: string): Block[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    // code / mermaid fence
    const fence = line.match(/^```\s*(\w+)?/);
    if (fence) {
      const lang = (fence[1] || "").toLowerCase();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) { body.push(lines[i]); i++; }
      i++; // skip closing ```
      blocks.push({ kind: "code", lang, body: body.join("\n") });
      continue;
    }
    // table
    if (line.trim().startsWith("|") && i + 1 < lines.length && isSep(lines[i + 1])) {
      const headers = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(splitRow(lines[i])); i++; }
      blocks.push({ kind: "table", headers, rows });
      continue;
    }
    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { blocks.push({ kind: "heading", level: h[1].length, text: h[2] }); i++; continue; }
    // blockquote
    if (line.trim().startsWith(">")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      blocks.push({ kind: "quote", text: buf.join(" ") });
      continue;
    }
    // list
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "")); i++;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    // paragraph (collect until blank)
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*\||\s*>|\s*([-*]|\d+\.)\s)/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    blocks.push({ kind: "para", text: buf.join(" ") });
  }
  return blocks;
}

function isCandidateTable(headers: string[]): boolean {
  const h = headers.join(" ").toLowerCase();
  return h.includes("candidate") || h.includes("root cause") || h.includes("候選") || h.includes("根因");
}

// ---------- 候選根因表（含信心 chip + 諮詢鈕）----------
function CandidateTable({ headers, rows, idBase }: { headers: string[]; rows: string[][]; idBase: string }) {
  const confIdx = headers.findIndex((h) => /confidence|信心/i.test(h));
  const evidIdx = headers.findIndex((h) => /evidence|證據/i.test(h));
  const [verdicts, setVerdicts] = useState<Record<number, "confirm" | "reject" | "more">>({});
  return (
    <div style={{ overflowX: "auto", margin: "10px 0 18px" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th key={i} style={{ textAlign: "left", padding: "8px 10px", borderBottom: "2px solid var(--polaris-dim)",
                color: "#e6ecf5", fontFamily: "var(--font-display)", fontWeight: 600, whiteSpace: "nowrap" }}>{h}</th>
            ))}
            <th style={{ padding: "8px 10px", borderBottom: "2px solid var(--polaris-dim)", color: "var(--ink-muted)",
              fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase" }}>工程師裁決</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => {
            const v = verdicts[ri];
            return (
              <tr key={ri} style={{ background: v === "confirm" ? "color-mix(in oklab, var(--approved) 9%, transparent)"
                : v === "reject" ? "color-mix(in oklab, #e07a7a 8%, transparent)" : "transparent" }}>
                {r.map((c, ci) => {
                  if (ci === confIdx) {
                    const col = confColor(c);
                    return (
                      <td key={ci} style={{ padding: "9px 10px", borderBottom: "1px solid var(--rule)", verticalAlign: "top" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 8px", borderRadius: 5,
                          fontFamily: "var(--font-mono)", fontSize: 10.5, fontWeight: 600, color: col,
                          border: `1px solid color-mix(in oklab, ${col} 38%, transparent)`, background: `color-mix(in oklab, ${col} 10%, transparent)` }}>
                          <span style={{ width: 6, height: 6, borderRadius: "50%", background: col }} />{c}
                        </span>
                      </td>
                    );
                  }
                  return (
                    <td key={ci} style={{ padding: "9px 10px", borderBottom: "1px solid var(--rule)", verticalAlign: "top",
                      color: ci === evidIdx ? "var(--ink)" : "#cdd4df",
                      fontFamily: ci === evidIdx ? "var(--font-mono)" : "inherit",
                      fontSize: ci === evidIdx ? 11.5 : 13, lineHeight: 1.5 }}>
                      {renderInline(c, `${idBase}-${ri}-${ci}`)}
                    </td>
                  );
                })}
                <td style={{ padding: "9px 10px", borderBottom: "1px solid var(--rule)", whiteSpace: "nowrap", verticalAlign: "top" }}>
                  {(["confirm", "reject", "more"] as const).map((k) => {
                    const label = k === "confirm" ? "✓" : k === "reject" ? "✕" : "?";
                    const col = k === "confirm" ? "var(--approved)" : k === "reject" ? "#e07a7a" : "var(--ink-muted)";
                    const on = v === k;
                    return (
                      <button key={k} title={k} onClick={() => setVerdicts((p) => ({ ...p, [ri]: on ? undefined as never : k }))}
                        style={{ marginRight: 4, width: 24, height: 24, borderRadius: 5, cursor: "pointer",
                          border: `1px solid ${on ? col : "var(--rule)"}`, background: on ? `color-mix(in oklab, ${col} 16%, transparent)` : "transparent",
                          color: on ? col : "var(--ink-muted)", fontSize: 12 }}>{label}</button>
                    );
                  })}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------- 一般 markdown 渲染 ----------
function Markdown({ md, idBase, candidates }: { md: string; idBase: string; candidates: boolean }) {
  const blocks = useMemo(() => parseBlocks(md), [md]);
  return (
    <div style={{ color: "#cdd4df", fontSize: 14, lineHeight: 1.65 }}>
      {blocks.map((b, i) => {
        if (b.kind === "heading") {
          const size = b.level <= 1 ? 20 : b.level === 2 ? 16 : 14;
          return <div key={i} style={{ fontFamily: "var(--font-display)", fontWeight: 600, color: "var(--ink)", fontSize: size, margin: "16px 0 8px" }}>{renderInline(b.text, `${idBase}-h${i}`)}</div>;
        }
        if (b.kind === "code") {
          if (b.lang === "mermaid") return <div key={i} style={{ margin: "10px 0" }}><MermaidDiagram code={b.body} idPrefix={`${idBase}-mm${i}`} /></div>;
          return <pre key={i} style={{ background: "var(--bg-elev)", border: "1px solid var(--rule-dark)", borderRadius: 7, padding: "10px 12px", overflowX: "auto", fontFamily: "var(--font-mono)", fontSize: 12, color: "#cdd4df", margin: "10px 0" }}>{b.body}</pre>;
        }
        if (b.kind === "table") {
          if (candidates && isCandidateTable(b.headers)) return <CandidateTable key={i} headers={b.headers} rows={b.rows} idBase={`${idBase}-ct${i}`} />;
          return (
            <div key={i} style={{ overflowX: "auto", margin: "10px 0 16px" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead><tr>{b.headers.map((h, j) => <th key={j} style={{ textAlign: "left", padding: "7px 10px", borderBottom: "2px solid var(--polaris-dim)", color: "#e6ecf5", fontFamily: "var(--font-display)", fontWeight: 600 }}>{renderInline(h, `${idBase}-th${i}-${j}`)}</th>)}</tr></thead>
                <tbody>{b.rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci} style={{ padding: "7px 10px", borderBottom: "1px solid var(--rule)", color: "#cdd4df", verticalAlign: "top" }}>{renderInline(c, `${idBase}-td${i}-${ri}-${ci}`)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        if (b.kind === "list") {
          const Tag = b.ordered ? "ol" : "ul";
          return <Tag key={i} style={{ margin: "8px 0 14px", paddingLeft: 22 }}>{b.items.map((it, j) => <li key={j} style={{ margin: "3px 0" }}>{renderInline(it, `${idBase}-li${i}-${j}`)}</li>)}</Tag>;
        }
        if (b.kind === "quote") {
          return <div key={i} style={{ borderLeft: "3px solid color-mix(in oklab, var(--approved) 50%, transparent)", background: "color-mix(in oklab, var(--approved) 7%, transparent)", padding: "10px 14px", borderRadius: "0 7px 7px 0", margin: "12px 0", color: "#c8d6cd", fontSize: 13 }}>{renderInline(b.text, `${idBase}-q${i}`)}</div>;
        }
        return <p key={i} style={{ margin: "8px 0" }}>{renderInline(b.text, `${idBase}-p${i}`)}</p>;
      })}
    </div>
  );
}

// ---------- 護欄帶 ----------
function GuardBanner() {
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "flex-start", borderRadius: 9, padding: "12px 15px", marginBottom: 16,
      background: "linear-gradient(100deg, color-mix(in oklab,#f0a868 11%,transparent), color-mix(in oklab,#f0a868 4%,transparent))",
      border: "1px solid color-mix(in oklab,#f0a868 30%,transparent)" }}>
      <span style={{ fontSize: 18 }}>🧭</span>
      <div>
        <div style={{ fontFamily: "var(--font-display)", fontStyle: "italic", fontWeight: 600, color: "#f6c79a", fontSize: 14 }}>候選假設 · 待工程師確認，非結論</div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-muted)", marginTop: 2 }}>candidate hypotheses for engineer confirmation, not conclusions</div>
      </div>
    </div>
  );
}

// ---------- plan 視圖 ----------
function PlanView({ artifact, idBase }: { artifact: string; idBase: string }) {
  const plan = useMemo(() => {
    const m = artifact.match(/\[PLAN_START\]\s*([\s\S]*?)\s*\[PLAN_END\]/);
    const blob = m ? m[1] : artifact.slice(artifact.indexOf("{"), artifact.lastIndexOf("}") + 1);
    try { return JSON.parse(blob); } catch { return null; }
  }, [artifact]);
  if (!plan) return <Markdown md={artifact} idBase={idBase} candidates={false} />;
  return (
    <div>
      {plan.rationale && <p style={{ color: "#cdd4df", margin: "0 0 14px" }}><span style={{ color: "var(--ink-muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>RATIONALE　</span>{plan.rationale}</p>}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(plan.stages || []).map((s: { stage_id: string; agent_bindings?: { agent_id: string }[]; why?: string }, i: number) => (
          <div key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start", background: "var(--bg-elev)", border: "1px solid var(--rule)", borderRadius: 8, padding: "10px 13px" }}>
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--polaris-hi)", fontSize: 12 }}>{String(i + 1).padStart(2, "0")}</span>
            <div style={{ flex: 1 }}>
              <span style={{ color: "var(--ink)", fontWeight: 600 }}>{s.stage_id}</span>
              <span style={{ color: "var(--ink-muted)", fontFamily: "var(--font-mono)", fontSize: 11, marginLeft: 8 }}>{(s.agent_bindings || []).map((b) => b.agent_id).join(", ")}</span>
              {s.why && <div style={{ color: "#aeb6c6", fontSize: 12, marginTop: 2 }}>{s.why}</div>}
            </div>
          </div>
        ))}
      </div>
      <p style={{ color: "var(--ink-muted)", fontSize: 12, marginTop: 14 }}>核准此 plan 後按「套用 plan」→ 會建立對應的 workflow 並切換執行（可規劃 / 可派工 / 可追蹤）。</p>
    </div>
  );
}

// ---------- artifact 視圖 ----------
function RcaArtifactView({ stageId, artifact }: { stageId: string; artifact: string }) {
  if (!artifact.trim()) {
    return <div style={{ color: "var(--ink-muted)", fontStyle: "italic", padding: "20px 0" }}>尚未產生內容。按「Generate」讓 RCA copilot 產出。</div>;
  }
  return (
    <div>
      {GUARD_STAGES.has(stageId) && <GuardBanner />}
      {stageId === "rca_plan"
        ? <PlanView artifact={artifact} idBase={stageId} />
        : <Markdown md={artifact} idBase={stageId} candidates={CANDIDATE_STAGES.has(stageId)} />}
    </div>
  );
}

// ---------- 主元件 ----------
type StageRow = { stage_id: string; status: string; label: string; ops: string[] };
type Busy = false | "generate" | "refine" | "approve" | "apply";

const RCA_WF_LABEL: Record<string, string> = {
  rca_single: "單代理 RCA", rca_chain: "多代理鏈", rca_planner: "Agentic 規劃",
  rca_panel: "Panel（討論）", rca_dispatch: "Dispatch（派工）",
};

export default function RcaWorkspace({
  thread, workflowId, workflows, modelChoice, onChangeWorkflow, onError, threadName,
}: {
  thread: string;
  workflowId: string | null;
  workflows: Workflow[];
  modelChoice: string;
  onChangeWorkflow: (id: string | null) => void;
  onError: (m: string | null) => void;
  threadName: string | null;
}) {
  const [catalog, setCatalog] = useState<Record<string, StageCatalogItem>>({});
  const [stages, setStages] = useState<StageRow[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [artifact, setArtifact] = useState<string>("");
  const [status, setStatus] = useState<string>("draft");
  const [busy, setBusy] = useState<Busy>(false);
  const [refineOpen, setRefineOpen] = useState(false);
  const [refineText, setRefineText] = useState("");

  useEffect(() => { fetchStages().then((rows) => setCatalog(Object.fromEntries(rows.map((r) => [r.id, r])))).catch(() => {}); }, []);

  const loadStages = useCallback(async () => {
    if (!thread) return;
    try {
      const statuses = await fetchStageStatuses(thread);
      const rows: StageRow[] = statuses.map((s) => ({
        stage_id: s.stage_id, status: s.status,
        label: catalog[s.stage_id]?.label || s.stage_id,
        ops: catalog[s.stage_id]?.operations || [],
      }));
      setStages(rows);
      setSelected((cur) => (cur && rows.some((r) => r.stage_id === cur) ? cur : rows[0]?.stage_id || ""));
    } catch (e) { onError(`讀取 RCA stages 失敗：${(e as Error).message}`); }
  }, [thread, catalog, onError]);

  useEffect(() => { loadStages(); }, [loadStages, workflowId]);

  const loadArtifact = useCallback(async (sid: string) => {
    if (!thread || !sid) return;
    try { const s = await fetchStageState(sid, thread); setArtifact(s.artifact || ""); setStatus(s.status || "draft"); }
    catch { setArtifact(""); setStatus("draft"); }
  }, [thread]);

  useEffect(() => { loadArtifact(selected); }, [selected, loadArtifact]);

  const sel = stages.find((r) => r.stage_id === selected);

  const doGenerate = async () => {
    if (!thread || busy) return; setBusy("generate"); onError(null);
    try { const d = await stageGenerate(selected, thread, modelChoice); setArtifact(d.artifact || ""); setStatus("draft"); await loadStages(); }
    catch (e) { onError(`生成失敗：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const doRefine = async () => {
    if (!thread || busy || !refineText.trim()) return; setBusy("refine"); onError(null); setRefineOpen(false);
    try { const d = await stageRefine(selected, thread, modelChoice, refineText.trim()); setArtifact(d.artifact || ""); setStatus("draft"); setRefineText(""); await loadStages(); }
    catch (e) { onError(`修訂失敗：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const doApprove = async () => {
    if (!thread || busy || !artifact.trim()) return; setBusy("approve"); onError(null);
    try { const r = await stageApprove(selected, thread); setStatus(r.status); await loadStages(); }
    catch (e) { onError(`核准失敗：${(e as Error).message}`); } finally { setBusy(false); }
  };
  const doApplyPlan = async () => {
    if (!thread || busy) return; setBusy("apply"); onError(null);
    try {
      if (status !== "approved") { await stageApprove("rca_plan", thread); setStatus("approved"); }
      const wf = await applyRcaPlan(thread);
      onChangeWorkflow(wf.id);   // 切到 plan 產生的真 workflow，RcaWorkspace 重載成鏈
    } catch (e) { onError(`套用 plan 失敗：${(e as Error).message}`); } finally { setBusy(false); }
  };

  const statusColor = (s: string) => s === "approved" ? "var(--approved)" : s === "needs_revision" ? "#f0a868" : "var(--ink-muted)";
  const rcaWorkflows = workflows.filter((w) => w.id.startsWith("rca") && !w.id.startsWith("rca_plan_"));

  return (
    <div className="paper-texture shadow-anvil" style={{ display: "flex", minHeight: 0, flex: 1, flexDirection: "column", background: "var(--paper)", overflow: "hidden" }}>
      {/* header */}
      <div style={{ padding: "16px 26px", borderBottom: "1px solid var(--paper-edge)", display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: ".22em", textTransform: "uppercase", color: "var(--polaris-hi)" }}>Manufacturing RCA · Copilot</div>
          <div style={{ fontFamily: "var(--font-display)", fontStyle: "italic", fontWeight: 600, color: "var(--ink)", fontSize: 22, marginTop: 2 }}>{threadName || "RCA"}</div>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-muted)" }}>
          WORKFLOW
          <select value={workflowId && rcaWorkflows.some((w) => w.id === workflowId) ? workflowId : ""} onChange={(e) => onChangeWorkflow(e.target.value || null)}
            style={{ background: "var(--bg-elev)", color: "var(--ink)", border: "1px solid var(--rule)", borderRadius: 6, padding: "5px 8px", fontFamily: "var(--font-sans)" }}>
            {workflowId && workflowId.startsWith("rca_plan_") && <option value={workflowId}>{`AI plan（${workflowId.replace("rca_plan_", "").slice(0, 8)}…）`}</option>}
            {rcaWorkflows.map((w) => <option key={w.id} value={w.id}>{RCA_WF_LABEL[w.id] || w.label}</option>)}
          </select>
        </label>
      </div>

      {/* stepper */}
      <div style={{ display: "flex", gap: 6, padding: "12px 26px", borderBottom: "1px solid var(--paper-edge)", overflowX: "auto" }}>
        {stages.map((r, i) => {
          const on = r.stage_id === selected;
          return (
            <button key={r.stage_id} onClick={() => setSelected(r.stage_id)}
              style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 12px", borderRadius: 7, whiteSpace: "nowrap", cursor: "pointer",
                border: `1px solid ${on ? "var(--polaris-dim)" : "var(--rule)"}`, background: on ? "var(--anvil)" : "transparent" }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-muted)" }}>{String(i + 1).padStart(2, "0")}</span>
              <span style={{ color: on ? "var(--ink)" : "#aeb6c6", fontSize: 13, fontWeight: on ? 600 : 400 }}>{r.label}</span>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: statusColor(r.status) }} title={r.status} />
            </button>
          );
        })}
      </div>

      {/* body */}
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "22px 30px" }}>
        {sel && <RcaArtifactView stageId={sel.stage_id} artifact={artifact} />}
      </div>

      {/* action bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 26px", borderTop: "1px solid var(--paper-edge)", flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: statusColor(status), textTransform: "uppercase", letterSpacing: ".1em" }}>● {status}</span>
        <div style={{ flex: 1 }} />
        {refineOpen && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flex: "1 1 100%", order: 9, marginTop: 8 }}>
            <input autoFocus value={refineText} onChange={(e) => setRefineText(e.target.value)} placeholder="例：第 1 候選請補充可量化的判定門檻；加一個 confounder…"
              onKeyDown={(e) => { if (e.key === "Enter") doRefine(); }}
              style={{ flex: 1, background: "var(--bg-elev)", color: "var(--ink)", border: "1px solid var(--rule)", borderRadius: 6, padding: "8px 10px", fontFamily: "var(--font-sans)" }} />
            <button onClick={doRefine} disabled={!refineText.trim()} style={btn("var(--polaris)")}>送出</button>
            <button onClick={() => setRefineOpen(false)} style={btn()}>取消</button>
          </div>
        )}
        {sel?.ops.includes("generate") && <button onClick={doGenerate} disabled={!!busy} style={btn("var(--polaris)")}>{busy === "generate" ? "生成中…" : artifact.trim() ? "重新生成" : "Generate"}</button>}
        {sel?.ops.includes("refine") && artifact.trim() && <button onClick={() => setRefineOpen((o) => !o)} disabled={!!busy} style={btn()}>Refine</button>}
        {selected === "rca_plan"
          ? <button onClick={doApplyPlan} disabled={!!busy || !artifact.trim()} style={btn("var(--approved)")}>{busy === "apply" ? "套用中…" : "核准並套用 plan"}</button>
          : artifact.trim() && status !== "approved" && <button onClick={doApprove} disabled={!!busy} style={btn("var(--approved)")}>{busy === "approve" ? "核准中…" : "Approve"}</button>}
      </div>
    </div>
  );
}

function btn(accent?: string): React.CSSProperties {
  return {
    padding: "7px 14px", borderRadius: 6, cursor: "pointer", fontFamily: "var(--font-sans)", fontSize: 13, fontWeight: 500,
    border: `1px solid ${accent ? `color-mix(in oklab, ${accent} 40%, transparent)` : "var(--rule)"}`,
    background: accent ? `color-mix(in oklab, ${accent} 14%, transparent)` : "transparent",
    color: accent || "var(--ink-soft)",
  };
}
