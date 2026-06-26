// 共用：抽 markdown 內的 ```mermaid 區塊，用真實 mermaid.parse() 驗證語法。
//
// 前端是 mermaid 唯一能在真 DOM 下可靠跑全部圖種（flowchart / sequence / state…）的地方——
// 後端（Python）跑 JS parser 需綁 node + jsdom，跨 runtime 又脆。故「發佈/同步前不讓壞圖上 wiki」
// 的權威守門放這：MermaidDiagram 渲染用同一個套件，這裡只 parse 不 render。

export type MermaidIssue = { index: number; message: string };

const FENCE = /```\s*mermaid\b[^\n]*\n([\s\S]*?)```/gi;

/** 依出現順序抽出所有 mermaid code-fence 的內文。 */
export function extractMermaidBlocks(md: string): string[] {
  const out: string[] = [];
  FENCE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = FENCE.exec(md ?? "")) !== null) out.push(m[1]);
  return out;
}

/** 對 markdown 內每張 mermaid 圖跑 parse；回有語法錯誤的圖（1-based index + 首行錯誤訊息）。 */
export async function validateMermaidMarkdown(md: string): Promise<MermaidIssue[]> {
  const blocks = extractMermaidBlocks(md);
  if (blocks.length === 0) return [];
  const mermaid = (await import("mermaid")).default;
  mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
  const issues: MermaidIssue[] = [];
  for (let i = 0; i < blocks.length; i++) {
    try {
      await mermaid.parse(blocks[i]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      issues.push({ index: i + 1, message: msg.split("\n").slice(0, 2).join(" ").trim() });
    }
  }
  return issues;
}

export type DocMermaidResult = { stage: string; label: string; issues: MermaidIssue[] };

/** 抓指定 stage 的 artifact 並驗證其 mermaid；只回有壞圖的 doc。
 *  抓取失敗（網路）視為無法判定 → 不擋（避免假性卡關；後端仍會發佈）。 */
export async function validateStagesMermaid(
  apiBase: string,
  thread: string,
  stages: { id: string; label: string }[],
): Promise<DocMermaidResult[]> {
  const results = await Promise.all(
    stages.map(async (s) => {
      try {
        const r = await fetch(`${apiBase}/api/stage/${s.id}/${thread}`);
        if (!r.ok) return { stage: s.id, label: s.label, issues: [] as MermaidIssue[] };
        const data = await r.json();
        return { stage: s.id, label: s.label, issues: await validateMermaidMarkdown(data.artifact ?? "") };
      } catch {
        return { stage: s.id, label: s.label, issues: [] as MermaidIssue[] };
      }
    }),
  );
  return results.filter((x) => x.issues.length > 0);
}
