LANGUAGE RULE: You MUST respond in the same language as the PRD and Architecture content. If the content is in Chinese (Traditional or Simplified), your entire response must be in Chinese. If the content is in English, respond in English.

{{PERSONA}}

{{SKILLS}}## Output structure (HARD RULE — parsers depend on EXACT headings):

The Lodestar front-end and the publish-to-GitHub / GitLab / Jira pipeline both parse this document with strict regexes. **If you deviate from the heading shapes below, the front-end shows `0 STORIES / 0 SECTIONS` and the publish flow uploads zero issues — even though your prose is correct.**

You MUST emit headings in exactly these shapes (in this nesting order):

| Element | Markdown shape | Example |
|---|---|---|
| Document title | `# <project name> — User Stories` | `# MotoCam Android UI Port — User Stories` |
| Milestone (optional grouping) | `## Milestone N — <title>` | `## Milestone 1 — 首屏可視 (目標 ≤35h)` |
| Epic | `## Epic N: <title>` | `## Epic 1: 專案骨架 & 建置設定` |
| Story | `### Story N.M — <title>` | `### Story 1.1 — Gradle 專案骨架` |

Key shape constraints, each enforced by a parser regex you cannot see:

* Story heading is **`### Story` (H3)**, NOT `#### Story` (H4), NOT `**Story 1.1**` (bold paragraph), NOT `Story 1.1` (no markup). The literal text `### Story` must appear at the start of the line.
* Epic heading is **`## Epic` (H2)**, NOT `### Epic` (H3). The publish path groups stories by H2 Epic headings; H3 Epics are invisible to it.
* The em-dash separator (` — `) between number and title in story headings is what the title extractor strips. Use it.
* Milestones are optional — if you use them, they're H2 alongside Epics (same level), not a parent of Epics. Group epics under milestones by ordering, not nesting.

Story body fields keep their existing format (As a / I want / so that / **Acceptance Criteria** / **Reference** / **Requirement IDs** / **Senior RD Estimate** / **Depends on**) — those are loose-text fields, not heading-parsed.

## Output Format Requirements:
- Group stories under clearly labeled Epics (e.g., ## Epic 1: User Authentication)
- Each story must follow the format: **As a [role], I want [goal] so that [benefit]**
- Each story must include:
  - **Acceptance Criteria** (bulleted list of testable conditions)
    - **Preferred shape**: `AC-N: Given <precondition>, When <trigger>, Then <expected>`. This Gherkin form lets the implementation agent auto-generate an executable pytest stub that gates the fix-loop on real behaviour — not just LLM self-assessment.
    - Use the Gherkin form whenever the criterion describes a runtime behaviour (input → action → outcome). Examples: API responses, state transitions, validation errors, route guards.
    - Keep freeform bullets for criteria that genuinely cannot be tested by code (pixel diff thresholds, design-token equivalence, manual-only UX checks). The agent falls back to LLM verification for these.
  - **Requirement IDs** (list the original PRD requirement IDs such as `FR-1`, `NFR-2`, `OPS-1`)
  - **Senior RD Estimate** (ideal engineering hours for one senior RD; allow `0.5`–`4` hour values only)
- Cover all functional requirements from the PRD
- Include edge cases and error handling stories where relevant
- If the PRD already includes requirement IDs, every story must reference the matching IDs explicitly.
- Do not use Story Points unless the source material explicitly requires them for compatibility.

### Project bootstrap story (REQUIRED — first story of the first epic)

Emit one early "project scaffold" story whose acceptance criteria make the project **runnable in a clean environment**, because the implementation pipeline runs a clean-env integration gate (fresh checkout → install deps → full test suite → build) after the milestone. Its AC must include:
- All test/build dependencies are declared in the manifest and the lockfile is committed (e.g. `requirements-dev.txt` / `package-lock.json` / gradle wrapper). Don't assume tools are pre-installed.
- A CI workflow exists that **runs the test suite** (not only lint/format/guardrail).
- Test runner works from a bare invocation in a clean checkout (e.g. `pytest.ini`/`pyproject` sets `pythonpath`/`testpaths`; the gradle wrapper is committed).
- One consistent import / module-resolution convention across the repo.
- No package is named after a standard-library module (Python: `secrets`/`types`/`json`/…).
- If a `Dockerfile` is in scope, it COPYs every top-level module the app imports and the image starts (passes its healthcheck).

## Project tier propagation (HARD RULE — read this BEFORE story sizing)

The Architecture document's first line declares the project tier in the shape:

```
**Project tier**: T<N> — <justification>
```

Read this line. The tier governs how aggressively you split stories. Apply the matching rule below INSTEAD OF blindly applying the "Story sizing" defaults that follow.

- **T0** (prototype / shell, single module): a story can produce **one whole subsystem inside one file** — `Theme.kt` containing colours, typography, shape, spacing, elevation all together IS one story, not 4–5 stories. Splitting per-token-family is a T0 anti-pattern. Aim for ~ 8–15 total stories for the entire deliverable.
- **T1** (MVP, app + one core + few features): split per **screen** and per **shared subsystem**, but keep design tokens together in one `Theme.kt` story unless the file would exceed ~ 300 lines. Aim for ~ 15–35 total stories.
- **T2** (production, full modularization): the existing "Story sizing" rules below apply as written — per-token-family splits, per-component-extraction, per-route-wiring are all legitimate stories.

If the Architecture document does NOT have a tier line (legacy or skipped), infer the tier from PRD facts and apply the matching rule. Note this in the document title with `(tier inferred: TN)`.

The implementation agent budget (10–15 min × 2 attempts) still applies as a HARD ceiling. The tier rule decides where the ceiling sits inside that budget; it never lifts the ceiling.

## Story sizing (HARD RULE — affects whether the AI implementation agent can actually finish them):

Every story you emit will be implemented by an autonomous coding agent that runs Claude CLI in a fixed-budget loop (10–15 minutes per attempt, two attempts total before the story is marked failed and skipped). A story that takes a senior human "one day" or "two days" is, in practice, undeliverable in this loop and will be auto-cancelled.

Therefore:

1. **Maximum size per story: 4 engineering hours** (`Senior RD Estimate` ≤ `4`). If you find yourself writing `1 day` / `2 days` / `0.5 day` — STOP and split.

2. **One concrete subsystem per story.** Examples of stories that MUST be split:
   - "Establish MotoCamTheme (Color + Typography + Shape + Spacing)" → 4–5 stories, one per token family, plus one that wires them into a `MotoCamTheme` composable.
   - "Build Login + Signup + Forgot password" → 3 stories.
   - "Set up Gradle project + Version Catalog + ktlint + CI" → 4 stories (or treat scaffolding as one story and CI as another).

3. **Each Acceptance Criterion must be a single concrete testable assertion**, not a feature umbrella. "App uses MotoCamTheme, doesn't apply Material defaults" is an umbrella — split into:
   - "`MotoCamColors.kt` exports `lightScheme: ColorScheme` and `darkScheme: ColorScheme`."
   - "`MotoCamTheme` composable accepts `darkTheme: Boolean` and selects scheme."
   - "`MainActivity.setContent` wraps content in `MotoCamTheme`."

4. **Order stories so each can be implemented without merging in changes from a later story.** If story B truly depends on story A, list B AFTER A and call out the dependency in B's body ("Depends on: Story A.X — assumes `MotoCamColors.kt` already exists").

5. **Stories that the AI agent CANNOT do alone** (require running on a physical device, accessing an external CMS, designing visuals from scratch, doing manual QA, legal/license review) should still appear in the output BUT be tagged with a leading `[HUMAN]` in the story title so the user can filter them before shipping. Don't pretend they're AI-implementable just to keep the list short.

## Example stories

Example showing Gherkin AC (preferred shape for behavioural criteria — the implementation agent will auto-generate a pytest stub for each):

```
**As a** logged-out user, **I want** protected routes to redirect me to /login, **so that** I don't see other users' dashboards.

**Acceptance Criteria**
- AC-1: Given the user has no session cookie, When they request `/dashboard`, Then the response status is 302 and `Location` header is `/login`.
- AC-2: Given an expired session token, When the user requests `/dashboard`, Then the response status is 401 and the body contains `session expired`.
- AC-3: Given a valid active session, When the user requests `/dashboard`, Then the response status is 200.

**Requirement IDs**: FR-3, NFR-1

**Senior RD Estimate**
- 2
```

PRD:
{{PRD_DRAFT}}

System Architecture:
{{ARCHITECTURE_DRAFT}}
