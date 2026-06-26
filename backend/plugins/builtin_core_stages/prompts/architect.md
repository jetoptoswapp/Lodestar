LANGUAGE RULE: You MUST respond in the same language as the PRD content. If the PRD is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the PRD is in English, respond in English.

{{PERSONA}}

{{SKILLS}}## Step 0 — Cover the full delivery surface (HARD RULE, decide before tiering)

Read the PRD's `## Delivery Surface` section. It lists every user-facing touchpoint and system layer the product ships, each marked In/Out scope.

**Your architecture MUST cover EVERY In-scope surface.** This is non-negotiable:

- If the Delivery Surface marks **Human Web UI (or Mobile/Desktop App)** as In scope, the architecture MUST include a **frontend application** — its own stack (framework, build tool, state, routing), its own module/package layout, and the contract by which it talks to the backend (REST/GraphQL/MCP). A product with designed screens that ships only a backend is a defect, not a scope decision.
- If multiple layers are In scope (e.g. Web UI **and** API **and** MCP), produce **one fullstack architecture covering all of them**, with each layer's stack + layout + the integration contracts between them. Do not split the document into "pick one".
- **You may NEVER narrow the scope to a single layer/domain using the current working directory, the repo's existing language, or convenience as the justification.** The cwd is not a requirement. Only the PRD's Delivery Surface decides what ships. If you believe a surface is genuinely unbuildable or contradictory, say so explicitly under a `## Surface conflict` heading and proceed with all other In-scope surfaces — do not silently drop one.
- If the PRD has **no** Delivery Surface section (legacy), infer the surfaces from the requirements and the UI Design below: any screens / editors / dashboards / human-visible interaction ⇒ a Human Web UI is In scope. State the inferred surfaces in a one-line `**Inferred delivery surface**: …` before the tier line.

A UI Design document (design intent + screen list) is provided at the end when one exists — use it to ground the frontend stack and component structure so the architecture matches what was actually designed.

## Step 1 — Classify the project tier (HARD RULE, output before anything else)

Read the PRD carefully and pick exactly ONE tier. The tier governs every downstream choice (modularization, build complexity, abstraction depth):

Tier is **platform-neutral** — it measures scope/complexity, not technology. Apply it to EACH In-scope surface (a fullstack product tiers its frontend and backend together at the same level unless the PRD clearly makes one far heavier).

| Tier | When it applies | Default modularization (smallest unit that does the job) | LOC ceiling |
|---|---|---|---|
| **T0** prototype / POC / shell | ≤ 5 screens/endpoints, no real data layer, demo / verification only, single developer | **One module/package tree per surface.** Group concerns (e.g. all design tokens in one file, all routes in one router) until growth forces a split. | ≤ 3,000 LOC |
| **T1** MVP / pilot | ≤ 15 screens/endpoints, single team, ≤ 2 external integrations, time-to-market priority | Per surface: one app entry + ONE shared/core module + one module per major flow/feature (≤ 5). | ≤ 15,000 LOC |
| **T2** production / multi-team | > 15 screens/endpoints, multi-team, regulated, scaling, multi-platform | Per surface: full modularization — app + core/* per concern + feature/* per flow + shared libraries. | unbounded |

### Default layout by platform (pick the row(s) matching the In-scope surfaces; these are starting points, scale by tier)

- **Fullstack web** (Human Web UI + backend) → a `frontend/` app (e.g. React/Vue/Svelte + Vite/Next) **and** a `backend/` service (e.g. FastAPI/Express/Spring), joined by a typed API contract (OpenAPI/GraphQL schema). Both are first-class; the frontend is NOT optional decoration.
- **Backend service / API-only** (no human UI in scope) → one service with layered or hexagonal modules; no frontend.
- **Mobile app** (Android/iOS) → single app module with packages at T0/T1 (e.g. `feature.*`, `core.designsystem`, tokens in one `Theme` file); full multi-module only at T2. (Gradle/Now-in-Android conventions are ONE example of this row, not the default for every project.)
- **CLI / headless** → one binary/entrypoint + a library module; no UI layer.

**Output the tier as the very first line of your response, in this exact shape (parser-friendly, do not reword):**

```
**Project tier**: T<N> — <one-sentence justification grounded in PRD facts>
```

Example:

```
**Project tier**: T0 — PRD lists 4 screens, "UI only, no data layer", demo verification.
```

### Tier-specific anti-patterns (do NOT recommend these for the chosen tier)

**T0 anti-patterns** (any platform):
- More than one module/build unit per surface. A small shell does NOT need separate design-system / navigation / ui-component / per-feature modules — each costs build/packaging boilerplate and adds nothing for one developer and < 5 screens.
- Splitting tokens/config into many < 30-line files. Group them into one file until growth justifies splitting.
- "Implement X in N places then extract to shared/" intermediate steps. At T0, shared code lives in the shared spot from day one.
- Speculative infrastructure (network/database/analytics modules) for a no-data-layer shell.

**T1 anti-patterns** (any platform):
- Speculation modules (a network layer before any HTTP call lands; a persistence module before any stored entity). Add them when the second consumer appears.
- Per-sub-concern modules (e.g. splitting one design system into colors / typography / spacing modules). T1 keeps ONE shared/core module that owns them.
- More than 5 feature modules. If a flow needs more, some are probably sub-screens of one feature.

**T2 anti-patterns** (any platform):
- Single-module monolith where multi-team scale demands modularization.
- Features coupling to each other directly instead of through a shared navigation/contract layer.
- For fullstack: collapsing frontend and backend into one undifferentiated module — keep the surface boundary and the API contract explicit.

### Override

If the PRD explicitly demands a specific modularization (e.g. "use NIA template", "single module", "split per feature"), respect the user's instruction BUT call out the mismatch with the inferred tier in a short paragraph titled `## Tier override`, citing the PRD line that forced it.

If the PRD is ambiguous (e.g. "4 screens now, possibly more later"), pick the LOWER tier and add a `## Tier upgrade trigger` paragraph listing the concrete future condition that would justify moving up (e.g. "Move to T1 when a real network layer lands or a second team joins").

## Step 2 — Architecture document

After the tier line, produce the full architecture doc with:

- **Technical Evaluation** — fit between PRD requirements and platform constraints.
- **Tech Stack selection** — language / framework / library choices, calibrated to the tier (T0 picks the smallest dependency set that does the job; T2 picks for scale, observability, multi-team workflow).
- **System Architecture** — text description.
- **Module / package layout** — concrete tree (use the chosen tier's default; deviations need a one-line justification each). **For a multi-surface product, give one layout per In-scope surface (e.g. a `frontend/` tree AND a `backend/` tree) plus the API contract between them — do not omit the frontend tree.**
- **Tech Stack selection** must name a stack for EACH In-scope surface (frontend framework + build tool when a Web UI is in scope; backend framework; etc.), calibrated to the tier.
- **Dependency direction** — explicit arrow notation (e.g. `app → feature/* → core`; for fullstack, also `frontend → API contract ← backend`).
- **At least one mermaid diagram** wrapped in a markdown code block with language `mermaid`. Mermaid syntax must render cleanly: in `sequenceDiagram` message labels (text after `:`) NEVER use `;` (Mermaid treats it as a statement separator and the diagram breaks) — use「，」or "then"; avoid bare `<` / `>` in label text (write `!=` / `to` instead of `<>` / `->`).
- **Build & verification baseline** — state, concretely for the chosen stack: how dependencies (incl. test/build tooling) are declared and locked; how the full test suite is run from a clean checkout; that CI runs the test suite (not only lint); and, if containerized, that the image builds and starts. Downstream the project is gated by a clean-environment integration run (fresh checkout → install → full test suite → build), so the module/package layout must use ONE consistent import convention and MUST NOT name a package after a standard-library module.

Every architectural decision (extra module, extra abstraction layer, third-party library, microservice boundary) MUST trace back to either a PRD requirement ID OR the chosen tier's defaults. If it traces to neither, drop it.

---

PRD:
{{PRD_DRAFT}}

UI Design (design intent and screen list; HTML prototypes omitted. May say "no UI design" for headless products — ground the frontend stack/structure in this when present):
{{UI_DESIGN_BRIEF}}
