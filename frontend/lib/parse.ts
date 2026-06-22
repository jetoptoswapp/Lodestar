// frontend/lib/parse.ts
// Markdown parsing helpers for PRD / Architecture / Stories views.
// Lightweight, regex-based — 對 LLM 生成的 heading-shaped markdown 夠用。
// Goals: 不依賴外部 markdown 套件（避免 bundle 變大）；preserve raw body 給 caller 自由渲染。

// ============================================================
//  PRD：sections（# / ##）+ requirement IDs（FR-N / NFR-N / OPS-N）
// ============================================================
export type PrdSection = { id: string; heading: string; body: string };
export type PrdRequirement = { code: string; text: string };

export type PrdParsed = {
  title: string | null;
  sections: PrdSection[];
  requirements: PrdRequirement[];
  raw: string;
};

const REQ_LINE = /^\s*[-*]\s*`?(FR-\d+|NFR-\d+|OPS-\d+)`?\s*[:：]\s*(.+)$/gim;

export function parsePrd(md: string): PrdParsed {
  const lines = md.split(/\r?\n/);
  let title: string | null = null;
  const sections: PrdSection[] = [];
  let cur: { num: string; heading: string; bodyLines: string[] } | null = null;

  const push = () => {
    if (cur) {
      sections.push({
        id: cur.num || cur.heading.toLowerCase().replace(/\s+/g, "-"),
        heading: cur.heading,
        body: cur.bodyLines.join("\n").trim(),
      });
      cur = null;
    }
  };

  for (const ln of lines) {
    const h1 = /^#\s+(.+?)\s*$/.exec(ln);
    const h2 = /^##\s+(?:(\d+)\.?\s+)?(.+?)\s*$/.exec(ln);
    if (h1 && !title) {
      title = h1[1].trim();
      continue;
    }
    if (h2) {
      push();
      cur = { num: (h2[1] || "").trim(), heading: h2[2].trim(), bodyLines: [] };
      continue;
    }
    if (cur) cur.bodyLines.push(ln);
  }
  push();

  // Extract requirements globally (any FR-N / NFR-N / OPS-N pattern)
  const requirements: PrdRequirement[] = [];
  const seen = new Set<string>();
  for (const m of md.matchAll(REQ_LINE)) {
    const code = m[1].toUpperCase();
    if (seen.has(code)) continue;
    seen.add(code);
    requirements.push({ code, text: m[2].trim() });
  }

  return { title, sections, requirements, raw: md };
}

// ============================================================
//  Architecture：tier line + mermaid blocks + sections
// ============================================================
export type ArchTier = "T0" | "T1" | "T2" | null;
export type ArchSection = { id: string; heading: string; body: string };

export type ArchParsed = {
  tier: ArchTier;
  tierJustification: string | null;
  sections: ArchSection[];
  mermaids: string[];
  raw: string;
};

// **Project tier**: T<N> — <justification>
const TIER_LINE = /\*\*Project\s+tier\*\*:\s*(T[0-2])\s*[—–-]\s*(.+?)$/im;
const MERMAID_FENCE_RE = /```\s*mermaid\s*\n([\s\S]*?)```/gim;

export function parseArchitecture(md: string): ArchParsed {
  const tierMatch = TIER_LINE.exec(md);
  const tier = (tierMatch?.[1] as ArchTier) ?? null;
  const tierJustification = tierMatch?.[2].trim() ?? null;

  // Collect mermaid blocks (preserve order)
  const mermaids: string[] = [];
  for (const m of md.matchAll(MERMAID_FENCE_RE)) mermaids.push(m[1].trim());

  // For sections，刪掉 tier line + mermaid fences 後分段
  const stripped = md
    .replace(TIER_LINE, "")
    .replace(MERMAID_FENCE_RE, "\n");

  const sections = sectionizeByH2(stripped);

  return { tier, tierJustification, sections, mermaids, raw: md };
}

// ============================================================
//  UI Design：sections（理念 / tokens）+ screens（## Screen: + ```html 原型）
// ============================================================
export type UiScreen = { name: string; description: string; html: string | null };

export type UiDesignParsed = {
  title: string | null;
  sections: { id: string; heading: string; body: string }[]; // 非 Screen 的 H2（Design Direction / Tokens）
  screens: UiScreen[];
  raw: string;
};

const UI_TITLE_RE = /^#\s+(.+?)\s*$/m;
const SCREEN_NAME_RE = /^Screen\s*[:：]\s*(.+)$/i;            // 套在 H2 heading 文字上
const HTML_FENCE_RE = /```\s*html\s*\n([\s\S]*?)```/i;        // 每個 Screen 章節內取第一個
const HTML_FENCE_ALL_RE = /```\s*html\s*\n[\s\S]*?```/gi;

export function parseUiDesign(md: string): UiDesignParsed {
  const title = UI_TITLE_RE.exec(md)?.[1].trim() ?? null;
  const sections: { id: string; heading: string; body: string }[] = [];
  const screens: UiScreen[] = [];

  for (const sec of sectionizeByH2(md)) {
    const nameMatch = SCREEN_NAME_RE.exec(sec.heading);
    if (nameMatch) {
      // Screen 章節：抽 html 原型（缺 fence → html: null，preview 顯示空狀態不 crash）
      const htmlMatch = HTML_FENCE_RE.exec(sec.body);
      screens.push({
        name: nameMatch[1].trim(),
        description: sec.body.replace(HTML_FENCE_ALL_RE, "").trim(),
        html: htmlMatch ? htmlMatch[1].trim() : null,
      });
    } else {
      sections.push(sec);   // 理念 / tokens 等，保留 css fence 供文件 view 顯示
    }
  }
  return { title, sections, screens, raw: md };
}

// ============================================================
//  Stories：title / milestones / epics / stories with body fields
// ============================================================
export type StoryAC = { code: string | null; text: string };

export type Story = {
  num: string;           // "1.1"
  title: string;
  asA: string | null;
  iWant: string | null;
  soThat: string | null;
  ac: StoryAC[];
  requirements: string[];
  estimate: string | null;
  dependsOn: string | null;
  raw: string;           // 原始 markdown body（包含 heading 後到下一個 ### 之前的所有內容）
};

export type Epic = {
  num: string;           // "1"
  title: string;
  stories: Story[];
};

export type Milestone = {
  num: string;           // "1"
  title: string;
};

export type StoriesParsed = {
  title: string | null;
  milestones: Milestone[];
  epics: Epic[];
  raw: string;
};

const STORY_HEADING = /^###\s+Story\s+(\d+\.\d+)\s+[—–-]\s+(.+?)\s*$/i;
const EPIC_HEADING = /^##\s+Epic\s+(\d+)\s*[:：]\s*(.+?)\s*$/i;
const MILESTONE_HEADING = /^##\s+Milestone\s+(\d+)\s+[—–-]\s+(.+?)\s*$/i;
const TITLE_HEADING = /^#\s+(.+?)\s+[—–-]\s+(?:user\s+stories|使用者故事)\s*$/i;

export function parseStories(md: string): StoriesParsed {
  const lines = md.split(/\r?\n/);
  let title: string | null = null;
  const milestones: Milestone[] = [];
  const epics: Epic[] = [];
  let curEpic: Epic | null = null;
  let curStoryHead: { num: string; title: string; lineStart: number } | null = null;
  let storyBodyLines: string[] = [];

  const flushStory = () => {
    if (curStoryHead && curEpic) {
      const body = storyBodyLines.join("\n").trim();
      curEpic.stories.push(parseStoryBody(curStoryHead, body));
    }
    curStoryHead = null;
    storyBodyLines = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];

    const t = TITLE_HEADING.exec(ln);
    if (t && !title) { title = t[1].trim(); continue; }

    const mile = MILESTONE_HEADING.exec(ln);
    if (mile) {
      milestones.push({ num: mile[1], title: mile[2].trim() });
      continue;
    }

    const e = EPIC_HEADING.exec(ln);
    if (e) {
      flushStory();
      curEpic = { num: e[1], title: e[2].trim(), stories: [] };
      epics.push(curEpic);
      continue;
    }

    const s = STORY_HEADING.exec(ln);
    if (s) {
      flushStory();
      curStoryHead = { num: s[1], title: s[2].trim(), lineStart: i };
      continue;
    }

    if (curStoryHead) storyBodyLines.push(ln);
  }
  flushStory();

  return { title, milestones, epics, raw: md };
}

function parseStoryBody(
  head: { num: string; title: string; lineStart: number },
  body: string,
): Story {
  // As a / I want / so that —— 用 [\s\S]+? 取代 . + s flag（ES2017 相容）
  const asA = capture(body, /\*\*As\s+a(?:n)?\*\*\s*([\s\S]+?)(?=,?\s*\*\*I\s+want\*\*|$)/i);
  const iWant = capture(body, /\*\*I\s+want\*\*\s*([\s\S]+?)(?=,?\s*\*\*so\s+that\*\*|$)/i);
  const soThat = capture(body, /\*\*so\s+that\*\*\s*([\s\S]+?)(?=\n\n|$)/i);

  // Acceptance Criteria
  const acBlock = capture(
    body,
    /\*\*Acceptance\s+Criteria\*\*\s*\n([\s\S]*?)(?=\n\*\*[A-Za-z]|\n##\s|\n###\s|$)/i,
  );
  const ac: StoryAC[] = [];
  if (acBlock) {
    for (const m of acBlock.matchAll(/^\s*[-*]\s+(?:(AC-\d+)\s*[:：]\s*)?(.+)$/gim)) {
      ac.push({ code: m[1] || null, text: m[2].trim() });
    }
  }

  // Requirement IDs
  const reqsLine = capture(body, /\*\*Requirement\s+IDs?\*\*\s*[:：]?\s*(.+?)(?=\n|$)/i);
  const requirements: string[] = [];
  if (reqsLine) {
    const seen = new Set<string>();   // 去重：LLM 偶爾把同一碼列兩次（如 NFR-6, NFR-6），避免重複 chip 與 React key 撞號
    for (const m of reqsLine.matchAll(/(FR-\d+|NFR-\d+|OPS-\d+)/gi)) {
      const code = m[1].toUpperCase();
      if (!seen.has(code)) { seen.add(code); requirements.push(code); }
    }
  }

  // Senior RD Estimate
  let estimate: string | null = null;
  const estBlock = capture(
    body,
    /\*\*Senior\s+RD\s+Estimate\*\*\s*\n?\s*(?:-\s*)?(.+?)(?=\n\*\*|\n##|\n###|$)/i,
  );
  if (estBlock) estimate = estBlock.split(/\n/)[0].trim();

  // Depends on
  const dependsOn = capture(body, /\*\*Depends\s+on\*\*\s*[:：]?\s*(.+?)(?=\n|$)/i);

  return {
    num: head.num,
    title: head.title,
    asA,
    iWant,
    soThat,
    ac,
    requirements,
    estimate,
    dependsOn,
    raw: body,
  };
}

function capture(text: string, re: RegExp): string | null {
  const m = re.exec(text);
  return m ? m[1].trim() : null;
}

// ============================================================
//  Stories 前段截斷偵測（與後端 delivery_parser.detect_truncated_stories 對齊）
// ============================================================
// 完整 backlog 一定以 `# … — User Stories` 起手、首個故事為 Story 1.1。開頭非標題、
// 或最小 Epic/Story 編號 > 1 → 前段（標題 + 前面 Epic/Story）疑似生成時被截斷。
export function detectTruncatedStories(md: string): string | null {
  if (!md || !md.trim()) return null;
  const storyRe = /^###\s+Story\s+(\d+)\.\d+\s+[—–-]/gim;
  const stories = [...md.matchAll(storyRe)].map((m) => parseInt(m[1], 10));
  if (stories.length === 0) return null;
  // 第一個 Story 之前應有 markdown 標題；允許標題前有 AI 開場白等 prose（與後端對齊）。
  const firstStory = md.search(/^###\s+Story\s+\d+\.\d+\s+[—–-]/im);
  const head = firstStory >= 0 ? md.slice(0, firstStory) : md;
  if (!/^#{1,3}\s+\S/m.test(head)) {
    return "stories 文件開頭被截斷（未以標題起手），缺少前段 Epic／Story";
  }
  const epicNums = [...md.matchAll(/^##\s+Epic\s+(\d+)\s*[:：]/gim)].map((m) => parseInt(m[1], 10));
  if (epicNums.length && Math.min(...epicNums) > 1) {
    return `stories 缺少前面的 Epic（最小 Epic 為 ${Math.min(...epicNums)}，應從 Epic 1 開始），前段疑似被截斷`;
  }
  if (Math.min(...stories) > 1) {
    return `stories 缺少 Epic 1 的故事（最小故事為 Story ${Math.min(...stories)}.x），前段疑似被截斷`;
  }
  return null;
}

// ============================================================
//  Shared：把 markdown 用 H2 切分（用於 PRD / Arch sections）
// ============================================================
function sectionizeByH2(md: string): { id: string; heading: string; body: string }[] {
  const lines = md.split(/\r?\n/);
  const out: { id: string; heading: string; body: string }[] = [];
  let cur: { id: string; heading: string; bodyLines: string[] } | null = null;

  const push = () => {
    if (cur) {
      out.push({ id: cur.id, heading: cur.heading, body: cur.bodyLines.join("\n").trim() });
      cur = null;
    }
  };

  for (const ln of lines) {
    const h2 = /^##\s+(.+?)\s*$/.exec(ln);
    if (h2) {
      push();
      const heading = h2[1].trim();
      cur = {
        id: heading.toLowerCase().replace(/[^\w一-鿿]+/g, "-").slice(0, 60),
        heading,
        bodyLines: [],
      };
      continue;
    }
    if (cur) cur.bodyLines.push(ln);
  }
  push();
  return out;
}

// ============================================================
//  Helpers：char count + requirement count（給 UI footer 用）
// ============================================================
export function countRequirements(md: string): { fr: number; nfr: number; ops: number } {
  return {
    fr: (md.match(/\b(?:`?)FR-\d+/gi) || []).length,
    nfr: (md.match(/\b(?:`?)NFR-\d+/gi) || []).length,
    ops: (md.match(/\b(?:`?)OPS-\d+/gi) || []).length,
  };
}

export function countStoriesAndEstimate(md: string): { stories: number; epics: number; hours: number } {
  const stories = (md.match(/^###\s+Story\s+\d+\.\d+\s+[—–-]/gim) || []).length;
  const epics = (md.match(/^##\s+Epic\s+\d+\s*[:：]/gim) || []).length;
  let hours = 0;
  for (const m of md.matchAll(/\*\*Senior\s+RD\s+Estimate\*\*\s*\n?\s*-?\s*(\d+(?:\.\d+)?)/gi)) {
    hours += parseFloat(m[1]);
  }
  return { stories, epics, hours };
}
