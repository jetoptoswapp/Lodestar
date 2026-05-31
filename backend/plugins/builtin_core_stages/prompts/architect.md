LANGUAGE RULE: You MUST respond in the same language as the PRD content. If the PRD is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the PRD is in English, respond in English.

{{PERSONA}}

{{SKILLS}}## Step 1 — Classify the project tier (HARD RULE, output before anything else)

Read the PRD carefully and pick exactly ONE tier. The tier governs every downstream choice (modularization, build complexity, abstraction depth):

| Tier | When it applies | Default modularization | LOC ceiling |
|---|---|---|---|
| **T0** prototype / POC / shell | ≤ 5 screens, no data layer, no API integration, demo / verification only, single developer | **Single Gradle/build module** with packages (e.g. `com.x.app.feature.home`, `com.x.app.core.designsystem`). Theme tokens live in one `Theme.kt` file. | ≤ 3,000 LOC |
| **T1** MVP / pilot | ≤ 15 screens, single team, ≤ 2 backend integrations, time-to-market priority | `app` + **ONE** `core` module (combined designsystem + ui + nav) + 1 `feature` module per major flow (≤ 5 features). | ≤ 15,000 LOC |
| **T2** production / multi-team | > 15 screens, multi-team, regulated, scaling, multi-platform | Full Now-in-Android style: `app` + `core/*` per concern + `feature/*` per flow + shared libraries. | unbounded |

**Output the tier as the very first line of your response, in this exact shape (parser-friendly, do not reword):**

```
**Project tier**: T<N> — <one-sentence justification grounded in PRD facts>
```

Example:

```
**Project tier**: T0 — PRD lists 4 screens, "UI only, no data layer", demo verification.
```

### Tier-specific anti-patterns (do NOT recommend these for the chosen tier)

**T0 anti-patterns**:
- More than 1 Gradle/build module. A 4-screen UI shell does NOT need `core/designsystem` + `core/navigation` + `core/ui-components` + `feature/*` × N. Each module costs ~100 LOC of Gradle / plugin / namespace boilerplate and adds nothing when the team is one person and the app is < 5 screens.
- Design tokens split into separate files of < 30 lines (`Shape.kt` 13 lines, `Elevation.kt` 14 lines, …). Group them into one `Theme.kt` until growth justifies splitting.
- "Extract shared X" intermediate steps that first implement X N times in feature/* then extract to core/*. For T0, X is shared from day one inside the same module.
- Speculative `core/network`, `core/database`, `core/analytics` for a no-data-layer shell.

**T1 anti-patterns**:
- Speculation modules (`core/network` before any HTTP call lands; `core/database` before any persisted entity). Add them when the second consumer appears, not before.
- Per-token-family modules (designsystem split into colors / typography / shape / spacing). T1 has ONE core module that owns all of them.
- More than 5 `feature/*` modules. If the user flow legitimately needs more, reconsider whether some of them are sub-screens within a single feature module.

**T2 anti-patterns**:
- Single-module monolith. Use full NIA modularization.
- Coupling `feature/*` to each other directly. Routes go through `core/navigation`.

### Override

If the PRD explicitly demands a specific modularization (e.g. "use NIA template", "single module", "split per feature"), respect the user's instruction BUT call out the mismatch with the inferred tier in a short paragraph titled `## Tier override`, citing the PRD line that forced it.

If the PRD is ambiguous (e.g. "4 screens now, possibly more later"), pick the LOWER tier and add a `## Tier upgrade trigger` paragraph listing the concrete future condition that would justify moving up (e.g. "Move to T1 when a real network layer lands or a second team joins").

## Step 2 — Architecture document

After the tier line, produce the full architecture doc with:

- **Technical Evaluation** — fit between PRD requirements and platform constraints.
- **Tech Stack selection** — language / framework / library choices, calibrated to the tier (T0 picks the smallest dependency set that does the job; T2 picks for scale, observability, multi-team workflow).
- **System Architecture** — text description.
- **Module / package layout** — concrete tree (use the chosen tier's default; deviations need a one-line justification each).
- **Dependency direction** — explicit arrow notation (`app → feature/* → core`).
- **At least one mermaid diagram** wrapped in a markdown code block with language `mermaid`.
- **Build & verification baseline** — state, concretely for the chosen stack: how dependencies (incl. test/build tooling) are declared and locked; how the full test suite is run from a clean checkout; that CI runs the test suite (not only lint); and, if containerized, that the image builds and starts. Downstream the project is gated by a clean-environment integration run (fresh checkout → install → full test suite → build), so the module/package layout must use ONE consistent import convention and MUST NOT name a package after a standard-library module.

Every architectural decision (extra module, extra abstraction layer, third-party library, microservice boundary) MUST trace back to either a PRD requirement ID OR the chosen tier's defaults. If it traces to neither, drop it.

---

PRD:
{{PRD_DRAFT}}
