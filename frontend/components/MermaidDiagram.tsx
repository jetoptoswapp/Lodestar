"use client";

// MermaidDiagram —— 用 mermaid 套件渲染 markdown code-fence 內的 mermaid 文字。
// next/dynamic({ssr:false}) 因為 mermaid 依賴 window / document，不能在 SSR 渲染。

import { useEffect, useRef, useState } from "react";

type Props = {
  code: string;
  className?: string;
  /** Mermaid render id 前綴；避免多張圖同頁 id 衝突。 */
  idPrefix?: string;
};

export default function MermaidDiagram({ code, className, idPrefix = "mermaid" }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      setError(null);
      try {
        const mod = await import("mermaid");
        const mermaid = mod.default;
        // 主題對齊 Industrial Cobalt：dark base + polaris 5b8cff accent
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          theme: "base",
          themeVariables: {
            // Industrial Cobalt × Drafting Dusk
            background: "#1f2733",
            primaryColor: "#161c26",
            primaryTextColor: "#e6ecf5",
            primaryBorderColor: "#5b8cff",
            secondaryColor: "#11151c",
            secondaryTextColor: "#cdd4df",
            secondaryBorderColor: "#3a4054",
            tertiaryColor: "#1f2733",
            tertiaryTextColor: "#b8c0cf",
            tertiaryBorderColor: "#2a3242",
            lineColor: "#4a5468",
            textColor: "#cdd4df",
            edgeLabelBackground: "#11151c",
            clusterBkg: "#11151c",
            clusterBorder: "#2a3242",
            fontFamily: "var(--font-mono), ui-monospace, monospace",
            fontSize: "13px",
          },
        });

        const id = `${idPrefix}-${Math.random().toString(36).slice(2, 8)}`;
        // mermaid v10+：render(id, code) → { svg, bindFunctions }
        const { svg: outSvg } = await mermaid.render(id, code);
        if (!cancelled) {
          setSvg(outSvg);
          setReady(true);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setReady(true);
        }
      }
    }

    render();
    return () => { cancelled = true; };
  }, [code, idPrefix]);

  if (error) {
    return (
      <div
        className={
          "mermaid-error border border-[color-mix(in_oklab,#f59e0b_40%,transparent)] " +
          "bg-[color-mix(in_oklab,#f59e0b_10%,transparent)] px-3 py-2 " +
          "font-[family-name:var(--font-mono)] text-[11px] text-[#f59e0b]"
        }
      >
        Mermaid render error: {error}
      </div>
    );
  }

  if (!ready) {
    return (
      <div
        className={
          "grid place-items-center py-8 " +
          "font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.22em] text-[var(--ink-muted)]"
        }
      >
        rendering diagram…
      </div>
    );
  }

  return (
    <div
      ref={ref}
      className={(className ?? "") + " mermaid-shell flex justify-center"}
      // Inline SVG from mermaid render is safe — code is local content not user-supplied HTML.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
