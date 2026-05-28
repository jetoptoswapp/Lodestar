// frontend/lib/mocks.ts
// M2.2 mock：直接使用 M2.1 E2E 真實 claude-cli 生成內容當靜態假資料。
// M2.3 wire API 後改吃 /api/stage/{id}/{thread}。
// 内容均未經編修，保留原 prompt 引導的 heading shape。

export const MOCK_PRD_MARKDOWN = `# Product Requirements Document — 結帳重構（M2 E2E）

## 1. Overview
重構現有 B2C 電商網站的結帳流程，目標將轉換率自 54% 提升至 67%。範圍涵蓋 Web 桌機與行動瀏覽器，不含 iOS / Android 原生 App。

## 2. 目標使用者
- 主要：訪客（無帳號）、會員（有帳號）兩類消費者，月活 ~ 12 萬。
- 次要：客服（看訂單）、財會（對帳）。

## 3. Functional Requirements
- \`FR-1\`：訪客結帳——3 步內完成下單（商品確認 → 地址+運送 → 付款）。
- \`FR-2\`：支援信用卡、Apple Pay、LINE Pay 三種付款方式。
- \`FR-3\`：地址修改不需重新走完整流程，可在第 2 步原地編輯。
- \`FR-4\`：Apple Pay 在 iOS Safari 17.4+ 須帶出聯絡資訊（姓名、電話、Email）。

## 4. Non-Functional Requirements
- \`NFR-1\`：結帳 API P95 < 500ms；Apple Pay 喚起 < 1s。
- \`NFR-2\`：符合 PCI-DSS L1；3DS 2.0 強制開啟。
- \`NFR-3\`：99.95% uptime；金流斷線時可降級為「人工後補刷卡」。
- \`NFR-4\`：支援雙 11 預期尖峰並發 5,000 同時下單。

## 5. Operational Requirements
- \`OPS-1\`：採 feature flag 漸進式 rollout（5% → 25% → 100%）。
- \`OPS-2\`：A/B test 框架接 GA4 + Mixpanel；轉換率以「進入結帳頁 UV → 完成付款 UV」計算。

## 6. Out of Scope
購物車頁改版、會員系統重構、後台訂單管理 UI。
`;

export const MOCK_ARCH_MARKDOWN = `**Project tier**: T2 — PRD 要求 99.95% uptime、雙 11 尖峰 5,000 同時下單、PCI-DSS L1 合規、feature flag 漸進 rollout、A/B test 框架，屬多團隊協作的 production 級系統。

---

## Technical Evaluation

| PRD 需求 | 技術涵義 |
|---|---|
| FR-1 / FR-3 | 結帳流程需可組合的 step state machine，第 2 步地址需就地編輯 → 前端需 client-side state + 部分提交 API |
| FR-2 / FR-4 | 三種金流（信用卡 / Apple Pay / LINE Pay）需抽象為統一 PaymentProvider 介面；Apple Pay 走 Apple Pay JS + Merchant Validation |
| NFR-1 | P95 < 500ms → 結帳 API 不可同步呼叫所有下游（庫存 / 金流 / 風控）；需 async + cache |
| NFR-2 | PCI-DSS L1 → 卡號**永不落地**自有系統，全程 tokenization（金流 gateway 端） |
| NFR-3 | 99.95% + 金流降級 → 訂單服務與金流服務必須**鬆耦合**，金流失敗時訂單仍可建立為 \`pending_manual_charge\` 狀態 |
| NFR-4 | 5,000 並發 → 訂單寫入需 queue 緩衝；庫存扣減需分散式鎖或樂觀鎖 |
| OPS-1 / OPS-2 | feature flag + A/B 雙工具（GA4 + Mixpanel）→ 需獨立 experimentation 服務，事件 schema 雙寫 |

關鍵設計取捨：**訂單建立** 與 **付款授權** 解耦（saga / outbox pattern），是 NFR-3 降級能力的前提，也是 NFR-1 P95 達標的關鍵。

---

## Tech Stack

| 層 | 選型 | 理由（綁 PRD） |
|---|---|---|
| 前端 | Next.js 14（App Router）+ TypeScript + React Server Components | SSR/SSG 同時支援，桌機/行動共用一份代碼；Apple Pay JS SDK 須在瀏覽器端執行 |
| 前端狀態 | Zustand（checkout step machine）+ TanStack Query（server state） | 輕量；FR-1/FR-3 的 step 跳轉與就地編輯不需 Redux 重型方案 |
| 前端實驗 | GrowthBook SDK（client + edge） | OPS-1 feature flag、OPS-2 A/B 分桶可同源管理 |
| BFF | Next.js Route Handlers + tRPC | 收斂前端對後端的呼叫，型別端到端 |
| 後端 | Go 1.22 + Gin（checkout-api、order-api）；Node.js 20（payment-gateway-adapter） | Go 處理高並發訂單寫入（NFR-4）；Node 對接金流 SDK 生態（LINE Pay 官方 SDK 為 Node 較完整） |
| 資料庫 | PostgreSQL 16（訂單、地址）+ Redis 7（庫存暫扣 / session / idempotency key） | 訂單需強一致；庫存需高頻讀寫 |
| 訊息佇列 | Kafka（order-events、payment-events） | NFR-3 saga；NFR-4 削峰；OPS-2 事件雙寫 GA4/Mixpanel |
| 金流 | TapPay（信用卡 + 3DS 2.0）、Apple Pay JS、LINE Pay v3 API | NFR-2 PCI-DSS L1 由 TapPay 承擔；本系統只持有 token |
| 觀測 | OpenTelemetry → Grafana Tempo / Loki / Prometheus；Sentry | NFR-1 P95 監控、NFR-3 SLO 看板 |
| 部署 | Kubernetes（EKS）+ Argo CD + Istio | HPA 應對 NFR-4 尖峰；Istio 做漸進 rollout 流量切分（OPS-1） |
| 實驗平台 | GrowthBook（self-hosted）+ Segment → GA4 + Mixpanel | OPS-2 雙寫 |
| CI/CD | GitHub Actions + Trivy + Snyk | PCI-DSS L1 要求 SCA / 容器掃描 |

---

## System Architecture

\`\`\`
[Browser / Mobile Web]
      │  HTTPS
      ▼
[CDN (CloudFront) + WAF]
      │
      ▼
[Next.js BFF (SSR + tRPC)]
      │
      ├──► [checkout-api  (Go)]   ── Redis ──► 庫存暫扣、idempotency
      │         │
      │         ├─writes─► PostgreSQL (orders, addresses)
      │         └─emits──► Kafka: order.created
      │
      ├──► [payment-gateway-adapter (Node)]
      │         │
      │         ├─► TapPay (信用卡 + 3DS 2.0)
      │         ├─► Apple Pay JS validation endpoint
      │         └─► LINE Pay v3
      │         └─emits──► Kafka: payment.authorized / payment.failed
      │
      └──► [experiment-svc] ── GrowthBook ── Segment ──► GA4 + Mixpanel

[order-saga (Go consumer)]  ◄── Kafka
      └─► reconcile order ↔ payment（NFR-3 降級為 pending_manual_charge）

[ops-console (內部)] ── 客服查訂單、財會對帳
\`\`\`

**Saga（NFR-3 關鍵）**：\`checkout-api\` 寫入 \`orders\` 為 \`pending_payment\` 後立即回應前端 → 前端喚起金流 → \`payment-gateway-adapter\` 收到授權結果 emit Kafka → \`order-saga\` 更新訂單狀態。金流斷線時 saga 將訂單標記 \`pending_manual_charge\`，客服走人工後補刷卡。

\`\`\`mermaid
flowchart LR
    Browser[瀏覽器]
    BFF[Next.js BFF]
    CO[checkout-api Go]
    PG[payment-gateway-adapter Node]
    PSQL[(PostgreSQL)]
    Redis[(Redis)]
    K[Kafka]
    SAGA[order-saga]
    EXP[experiment-svc]
    Ext[TapPay / Apple Pay / LINE Pay]

    Browser --> BFF
    BFF --> CO
    BFF --> PG
    BFF --> EXP
    CO --> PSQL
    CO --> Redis
    CO -- order.created --> K
    PG --> Ext
    PG -- payment.authorized/failed --> K
    K --> SAGA
    SAGA --> PSQL
\`\`\`

---

## Module / Repo 結構

採 **polyrepo + 1 個前端 monorepo**（Turborepo），各後端服務各自 repo（PCI-DSS L1 對 \`payment-gateway-adapter\` 的稽核邊界要明確）。

### 前端 monorepo（\`checkout-web\`，Turborepo）

\`\`\`
checkout-web/
├── apps/
│   └── storefront/                 # Next.js app（結帳頁宿主）
├── packages/
│   ├── core-ui/                    # 共用元件、Design tokens
│   ├── core-design-system/         # tailwind preset + 主題
│   ├── core-analytics/             # Segment + GA4 + Mixpanel wrapper
│   ├── core-experiments/           # GrowthBook client wrapper
│   ├── core-api-client/            # tRPC client + 型別
│   ├── feature-checkout-step1/     # 商品確認（FR-1）
│   ├── feature-checkout-step2/     # 地址 + 運送（FR-1 / FR-3）
│   ├── feature-checkout-step3/     # 付款（FR-1 / FR-2 / FR-4）
│   └── feature-checkout-shell/     # step machine + 路由
└── turbo.json
\`\`\`

**依賴方向**：\`apps/storefront → feature/* → core/*\`，\`feature/*\` 之間禁止直接依賴。

### 後端服務（各自 repo）

\`\`\`
checkout-api/                       # Go，純訂單寫入
├── cmd/server/
├── internal/
│   ├── api/         # gin handlers
│   ├── domain/      # order, address, cart aggregate
│   ├── repository/  # postgres, redis
│   ├── inventory/   # 庫存暫扣（Redis Lua）
│   └── events/      # Kafka producer
├── migrations/
├── go.mod / go.sum
└── Dockerfile

payment-gateway-adapter/            # Node，金流整合（PCI 稽核邊界）
├── src/
│   ├── providers/
│   │   ├── tappay.ts
│   │   ├── apple-pay.ts            # FR-4 merchant validation
│   │   └── line-pay.ts
│   ├── ports/payment-provider.ts   # 統一介面（FR-2）
│   ├── routes/
│   └── kafka/
├── package.json / package-lock.json
└── Dockerfile

order-saga/                         # Go consumer
order-experiment-svc/               # Go，GrowthBook + Segment 代理
ops-console/                        # 內部後台（客服 / 財會）
\`\`\`

**依賴方向**：
- 服務間僅透過 **Kafka events** 或 **HTTP（同步、有 SLA）** 通訊。
- \`payment-gateway-adapter\` 不可直接讀寫 \`orders\` 表（PCI 邊界）。
- 共用 schema 透過 \`proto/\` repo（gRPC contract）或 OpenAPI 檔同步。

---

## Build & Verification Baseline

| 維度 | 規格 |
|---|---|
| 前端依賴鎖定 | \`pnpm-lock.yaml\` 提交入庫；CI 用 \`pnpm install --frozen-lockfile\` |
| 前端測試 | Vitest（unit）+ Playwright（E2E：訪客 3 步結帳 happy path + Apple Pay merchant validation mock）；\`turbo test\` 一鍵跑全 monorepo |
| 後端（Go）依賴 | \`go.mod\` + \`go.sum\`，CI 用 \`go mod verify\` |
| 後端測試 | \`go test ./... -race -count=1\`；整合測試用 testcontainers 起 Postgres + Redis + Kafka |
| 後端（Node）依賴 | \`package-lock.json\`；\`npm ci\` |
| 容器 | 每個服務 multi-stage Dockerfile；CI 必須 \`docker build\` 成功且 \`docker run\` healthcheck pass |
| CI 流程（GitHub Actions） | clean checkout → install (frozen) → lint → **full test suite** → build → Trivy 容器掃描 → Snyk SCA |
| 命名約束 | Python 不在技術棧；Go package 不得命名為 \`context\`、\`net\`、\`http\` 等標準庫名稱；Node 套件不得命名為 \`fs\`、\`path\`、\`crypto\` |
| Import 慣例 | 前端統一 \`@checkout/<package-name>\` path alias（TS paths + Turborepo）；Go 統一 module path \`github.com/<org>/<svc>/internal/...\` |
| Clean-env gate | PR 合併前須通過 fresh checkout → install → 完整測試 → docker build → 啟動 healthcheck 的整合 job |

---

## 關鍵架構決策追溯

| 決策 | 對應 PRD |
|---|---|
| 前端 monorepo + \`feature-checkout-step{1,2,3}\` 三模組 | FR-1 三步流程；FR-3 就地編輯（step2 獨立模組可控制 re-mount） |
| \`payment-gateway-adapter\` 獨立服務 + \`PaymentProvider\` port | FR-2 三種金流；NFR-2 PCI 稽核邊界 |
| Apple Pay 專用 provider + merchant validation endpoint | FR-4 |
| Saga + Kafka + \`pending_manual_charge\` 狀態 | NFR-3 降級 |
| checkout-api 用 Go + Redis 庫存暫扣 | NFR-1 P95、NFR-4 5k 並發 |
| GrowthBook + Segment 雙寫 GA4/Mixpanel | OPS-1、OPS-2 |
| Istio 流量切分 5/25/100 | OPS-1 |
| OpenTelemetry + SLO 看板 | NFR-1 / NFR-3 監控 |

每個 \`core/*\` 和 \`feature/*\` 模組皆有 ≥ 2 個消費者或 ≥ 1 個 PRD 需求支撐，無 speculation。`;

export const MOCK_STORIES_MARKDOWN = `# 電商結帳重構 — User Stories

## Milestone 1 — 基礎建設與骨架 (目標 ≤40h)

## Epic 1: 專案骨架與 CI/CD 基線

### Story 1.1 — 前端 monorepo (Turborepo) 骨架

**As a** 前端工程師, **I want** 一個可在 clean checkout 立即跑起來的 Turborepo monorepo, **so that** 後續所有 feature/core 套件都能在同一份 lockfile 下並行開發。

**Acceptance Criteria**
- AC-1: Given a clean checkout, When I run \`pnpm install --frozen-lockfile && pnpm turbo build\`, Then exit code is 0.
- AC-2: Given the repo root, When I list it, Then \`pnpm-lock.yaml\`, \`turbo.json\`, \`pnpm-workspace.yaml\`, \`apps/storefront/\`, \`packages/\` all exist.
- AC-3: Given \`apps/storefront\`, When I run \`pnpm --filter storefront dev\` and curl \`http://localhost:3000\`, Then HTTP 200.
- AC-4: Given \`tsconfig.base.json\`, When I import \`@checkout/core-ui\`, Then TypeScript resolves it via path alias.

**Reference**: Module 結構 §前端 monorepo；Build Baseline

**Requirement IDs**: FR-1, NFR-1

**Senior RD Estimate**
- 3

### Story 1.2 — 前端 CI workflow（lint + test + build）

**As a** 平台工程師, **I want** GitHub Actions 在每個 PR 跑完整測試套件, **so that** 不會有未測通的程式碼合進 main。

**Acceptance Criteria**
- AC-1: Given a PR is opened, When CI runs, Then \`pnpm install --frozen-lockfile\`, \`pnpm turbo lint\`, \`pnpm turbo test\`, \`pnpm turbo build\` all execute in sequence.
- AC-2: Given any of those steps fails, When viewing PR checks, Then the PR is marked as failing and cannot merge.
- AC-3: Given the workflow file \`.github/workflows/web-ci.yml\`, When inspected, Then it pins Node 20 and pnpm version via \`packageManager\`.

**Reference**: Build Baseline §CI 流程

**Requirement IDs**: NFR-1, OPS-1

**Senior RD Estimate**
- 2

### Story 1.3 — checkout-api (Go) 服務骨架

**As a** 後端工程師, **I want** checkout-api 在 clean env 可 \`go build\` + \`go test ./...\` 通過, **so that** 後續 domain 邏輯有可運行的容器底座。

**Acceptance Criteria**
- AC-1: Given a clean checkout of \`checkout-api\`, When I run \`go mod verify && go test ./... -race -count=1\`, Then exit code is 0.
- AC-2: Given \`cmd/server/main.go\`, When \`go run\` is invoked, Then a Gin HTTP server listens on port 8080 and \`GET /healthz\` returns 200 with \`{"status":"ok"}\`.
- AC-3: Given the module path, When inspected in \`go.mod\`, Then it follows \`github.com/<org>/checkout-api\`.
- AC-4: Given Go package names, When inspected, Then no package is named \`context\`, \`net\`, \`http\`, \`time\`.

**Reference**: Module 結構 §checkout-api；Build Baseline §命名約束

**Requirement IDs**: NFR-1, NFR-4

**Senior RD Estimate**
- 3

### Story 1.4 — checkout-api Dockerfile 與 healthcheck

**As a** 平台工程師, **I want** checkout-api 有 multi-stage Dockerfile 且容器啟動後 healthcheck 通過, **so that** Kubernetes 可正確判斷 readiness。

**Acceptance Criteria**
- AC-1: Given the repo root, When I run \`docker build -t checkout-api:test .\`, Then exit code is 0 and image size < 50MB.
- AC-2: Given the built image, When I run it and wait 10s, Then \`docker inspect\` shows \`Health.Status == "healthy"\`.
- AC-3: Given the Dockerfile, When inspected, Then it uses a distroless or alpine final stage and runs as non-root UID.

**Reference**: Build Baseline §容器

**Requirement IDs**: NFR-1, NFR-3

**Senior RD Estimate**
- 2

### Story 1.5 — checkout-api CI（含 testcontainers 整合測試）

**As a** 後端工程師, **I want** checkout-api CI 跑整合測試（含 Postgres/Redis/Kafka）, **so that** 庫存與訂單寫入邏輯在 PR 階段就被驗證。

**Acceptance Criteria**
- AC-1: Given \`.github/workflows/checkout-api-ci.yml\`, When CI runs on a PR, Then it executes \`go mod verify\`, \`golangci-lint\`, \`go test ./... -race\`, \`docker build\`.
- AC-2: Given testcontainers config, When integration tests run, Then Postgres 16 / Redis 7 / Kafka containers start automatically.
- AC-3: Given the CI job, When inspected, Then Trivy scan of the built image runs and fails on HIGH+ vulnerabilities.

**Reference**: Build Baseline

**Requirement IDs**: NFR-2, NFR-4

**Senior RD Estimate**
- 3

### Story 1.6 — payment-gateway-adapter (Node) 服務骨架

**As a** 後端工程師, **I want** payment-gateway-adapter 在 clean env 跑得起來, **so that** 後續可逐步接入三種金流 provider。

**Acceptance Criteria**
- AC-1: Given a clean checkout, When I run \`npm ci && npm test\`, Then exit code is 0.
- AC-2: Given \`npm start\`, When the service boots, Then \`GET /healthz\` returns 200.
- AC-3: Given \`package.json\`, When inspected, Then no dependency or local module is named \`fs\`, \`path\`, \`crypto\`.
- AC-4: Given Dockerfile, When built and run, Then container healthcheck passes within 15s.

**Reference**: Module 結構 §payment-gateway-adapter；Build Baseline §命名約束

**Requirement IDs**: FR-2, NFR-2

**Senior RD Estimate**
- 3

### Story 1.7 — PostgreSQL schema migrations 骨架（orders / addresses）

**As a** 後端工程師, **I want** 用 golang-migrate 管理 orders / addresses schema, **so that** schema 變更可版本化且可在 CI 重放。

**Acceptance Criteria**
- AC-1: Given \`migrations/0001_init.up.sql\`, When applied to empty Postgres, Then tables \`orders\`, \`addresses\` exist with PK, FK, and \`order_status\` enum (\`pending_payment\`, \`paid\`, \`pending_manual_charge\`, \`cancelled\`).
- AC-2: Given \`0001_init.down.sql\`, When applied, Then both tables are dropped cleanly.
- AC-3: Given the \`orders\` table, When inspected, Then it has columns \`id (uuid)\`, \`user_id\`, \`status\`, \`total_amount\`, \`created_at\`, \`idempotency_key (unique)\`.

**Reference**: System Architecture §checkout-api

**Requirement IDs**: FR-1, NFR-3

**Senior RD Estimate**
- 2

### Story 1.8 — Kafka topic 與 event schema 定義

**As a** 後端工程師, **I want** \`order.created\` / \`payment.authorized\` / \`payment.failed\` event schema 用 protobuf 鎖定, **so that** 各服務的 producer/consumer 型別端到端對齊。

**Acceptance Criteria**
- AC-1: Given \`proto/events/order.proto\` and \`payment.proto\`, When \`protoc\` compiles them, Then Go and TypeScript stubs are generated without error.
- AC-2: Given \`order.created\`, When inspected, Then it includes \`order_id\`, \`user_id\`, \`amount\`, \`currency\`, \`idempotency_key\`, \`created_at\` fields.
- AC-3: Given \`payment.authorized\` and \`payment.failed\`, When inspected, Then both include \`order_id\`, \`provider\`, \`provider_txn_id\`, \`occurred_at\`.

**Reference**: System Architecture §Saga；Module 結構 §proto

**Requirement IDs**: NFR-3, OPS-2

**Senior RD Estimate**
- 2

## Epic 2: Design System 與 Core Packages

### Story 2.1 — \`core-design-system\` Tailwind preset 與 color tokens

**As a** 前端工程師, **I want** 一個 Tailwind preset 匯出品牌色票, **so that** 所有 feature 模組共用同一份色彩來源。

**Acceptance Criteria**
- AC-1: Given \`packages/core-design-system/tailwind.preset.ts\`, When imported into \`apps/storefront/tailwind.config.ts\`, Then classes \`bg-brand-primary\`, \`text-brand-foreground\` compile to defined hex values.
- AC-2: Given the preset, When inspected, Then it exports \`colors\`, \`fontFamily\`, \`spacing\` keys.

**Reference**: Module 結構 §core-design-system

**Requirement IDs**: FR-1

**Senior RD Estimate**
- 2

### Story 2.2 — \`core-ui\` Button / Input / FormField 元件

**As a** 前端工程師, **I want** 共用 Button、Input、FormField 元件, **so that** 三個 step 模組不重複實作表單原件。

**Acceptance Criteria**
- AC-1: Given \`<Button variant="primary" />\`, When rendered, Then it applies \`bg-brand-primary\` and emits \`data-testid="btn-primary"\`.
- AC-2: Given \`<FormField label="..." error="..." />\`, When \`error\` prop is set, Then error message renders with \`role="alert"\`.
- AC-3: Given vitest suite, When run, Then all three components have ≥1 unit test each.

**Reference**: Module 結構 §core-ui

**Requirement IDs**: FR-1, FR-3

**Senior RD Estimate**
- 3

### Story 2.3 — \`core-api-client\` tRPC client 骨架

**As a** 前端工程師, **I want** 一個型別安全的 tRPC client wrapper, **so that** feature 模組呼叫 BFF 時不需手寫 fetch。

**Acceptance Criteria**
- AC-1: Given \`packages/core-api-client/src/index.ts\`, When imported, Then it exports \`trpc\` proxy and \`TRPCProvider\` React component.
- AC-2: Given a mock BFF router, When \`trpc.checkout.getCart.useQuery()\` is called in a test, Then return type is inferred as \`Cart\`.

**Reference**: Tech Stack §BFF

**Requirement IDs**: FR-1, NFR-1

**Senior RD Estimate**
- 2

### Story 2.4 — \`core-analytics\` Segment + GA4 + Mixpanel wrapper

**As a** 前端工程師, **I want** 一個事件 wrapper 同時把事件雙寫 GA4 與 Mixpanel, **so that** OPS-2 不需各 feature 模組重複串。

**Acceptance Criteria**
- AC-1: Given \`track('checkout_step_viewed', { step: 1 })\`, When invoked, Then both \`window.gtag\` and \`window.mixpanel.track\` are called with normalized payload.
- AC-2: Given Segment is configured, When \`track\` is called, Then it routes through \`analytics.js\` SDK and not direct calls.
- AC-3: Given \`NEXT_PUBLIC_ANALYTICS_DISABLED=true\`, When \`track\` is called, Then no network requests are issued.

**Reference**: Module 結構 §core-analytics

**Requirement IDs**: OPS-2

**Senior RD Estimate**
- 3

### Story 2.5 — \`core-experiments\` GrowthBook client wrapper

**As a** 前端工程師, **I want** 一個 GrowthBook React hook 包裝, **so that** feature flag 與 A/B 變體取得邏輯統一。

**Acceptance Criteria**
- AC-1: Given \`useFeature('new_checkout_flow')\`, When called inside \`<ExperimentsProvider>\`, Then it returns \`{ on: boolean, value: unknown }\`.
- AC-2: Given a user is bucketed into variant B, When \`useExperiment('checkout_v2')\` is called, Then it returns \`'B'\` and emits an \`$experiment_started\` event through \`core-analytics\`.

**Reference**: Module 結構 §core-experiments

**Requirement IDs**: OPS-1, OPS-2

**Senior RD Estimate**
- 3

## Milestone 2 — 結帳核心功能 (目標 ≤60h)

## Epic 3: Checkout Shell & Step Machine

### Story 3.1 — \`feature-checkout-shell\` Zustand step state machine

**As a** 前端工程師, **I want** 結帳三步驟的 step machine, **so that** step1/2/3 可宣告式地切換且能就地回跳。

**Acceptance Criteria**
- AC-1: Given initial state, When \`goNext()\` is dispatched, Then \`currentStep\` transitions \`1 → 2 → 3\` and emits \`step_transitioned\` event.
- AC-2: Given \`currentStep === 3\`, When \`goTo(2)\` is dispatched, Then step 2 re-mounts and step 3 state is preserved (not reset).
- AC-3: Given the store, When inspected, Then it persists \`currentStep\` and \`cartId\` to \`sessionStorage\`.

**Reference**: 關鍵架構決策追溯 §step2 獨立模組可控制 re-mount

**Requirement IDs**: FR-1, FR-3

**Senior RD Estimate**
- 3

### Story 3.2 — 結帳頁路由與 step machine 整合

**As a** 訪客, **I want** \`/checkout\` 頁面渲染當前 step, **so that** 我可在同一 URL 內完成三步。

**Acceptance Criteria**
- AC-1: Given the user opens \`/checkout\`, When the page mounts, Then step 1 component is rendered.
- AC-2: Given \`currentStep === 2\`, When the URL is inspected, Then it is \`/checkout?step=2\` (deep-linkable).
- AC-3: Given the user reloads on \`?step=2\`, When the page mounts, Then step 2 is rendered (state is rehydrated from sessionStorage).

**Reference**: Module 結構 §feature-checkout-shell

**Requirement IDs**: FR-1

**Senior RD Estimate**
- 2

## Epic 4: Step 1 — 商品確認

### Story 4.1 — checkout-api: \`GET /carts/:id\` endpoint

**As a** 前端, **I want** 一個讀取購物車的 API, **so that** step1 可顯示商品明細與金額。

**Acceptance Criteria**
- AC-1: Given a valid \`cart_id\`, When \`GET /carts/{id}\` is called, Then response is 200 with \`{ items: [...], subtotal, currency }\`.
- AC-2: Given an unknown \`cart_id\`, When called, Then response is 404 with \`{ error: "cart_not_found" }\`.
- AC-3: Given P95 latency, When measured under 100 RPS load test, Then it is < 200ms.

**Reference**: NFR-1

**Requirement IDs**: FR-1, NFR-1

**Senior RD Estimate**
- 3

### Story 4.2 — \`feature-checkout-step1\` 商品確認 UI

**As a** 訪客, **I want** 在 step1 看到購物車品項、單價、小計、運費預估, **so that** 我能在進入地址前確認購買內容。

**Acceptance Criteria**
- AC-1: Given step1 mounts with a non-empty cart, When rendered, Then each item shows \`name\`, \`qty\`, \`unit_price\`, \`line_total\`.
- AC-2: Given the user clicks "下一步", When step1 form is valid, Then \`goNext()\` is dispatched and \`checkout_step1_completed\` event fires.
- AC-3: Given cart is empty, When step1 mounts, Then it shows "購物車是空的" CTA back to \`/\`.

**Reference**: Module 結構 §feature-checkout-step1

**Requirement IDs**: FR-1

**Senior RD Estimate**
- 3

## Epic 5: Step 2 — 地址與運送（就地編輯）

### Story 5.1 — checkout-api: \`PATCH /orders/:id/address\` 部分提交 endpoint

**As a** 前端, **I want** 一個只更新地址欄位的 API, **so that** step2 就地編輯不需重送整筆訂單。

**Acceptance Criteria**
- AC-1: Given an existing draft order, When \`PATCH /orders/{id}/address\` with valid body, Then response is 200 and only \`addresses\` row is updated.
- AC-2: Given invalid postcode, When called, Then response is 422 with \`{ field: "postcode", error: "invalid_format" }\`.
- AC-3: Given the same \`Idempotency-Key\` header is replayed, When called twice, Then second call returns identical response without re-writing DB.

**Reference**: FR-3 就地編輯；System Architecture §Redis idempotency

**Requirement IDs**: FR-1, FR-3, NFR-1

**Senior RD Estimate**
- 4

### Story 5.2 — \`feature-checkout-step2\` 地址表單 UI

**As a** 訪客, **I want** 在 step2 填寫收件地址與選運送方式, **so that** 我可進入付款步驟。

**Acceptance Criteria**
- AC-1: Given step2 mounts, When rendered, Then fields \`recipient_name\`, \`phone\`, \`postcode\`, \`address_line1\`, \`shipping_method\` are present.
- AC-2: Given user submits with missing required field, When click 下一步, Then inline error appears under that field via \`<FormField>\` and \`goNext\` is NOT called.
- AC-3: Given all fields valid, When 下一步 clicked, Then \`PATCH /orders/:id/address\` is called and on 200 state advances to step3.

**Reference**: Module 結構 §feature-checkout-step2

**Requirement IDs**: FR-1, FR-3

**Senior RD Estimate**
- 4

### Story 5.3 — step2 就地編輯（從 step3 回跳保留 step3 狀態）

**As a** 訪客, **I want** 在 step3 發現地址寫錯時可回到 step2 改, **so that** 不需從頭重填整單。

**Acceptance Criteria**
- AC-1: Given user is at step3 with selected payment method, When clicking "編輯地址", Then step2 re-mounts with prior address values pre-filled.
- AC-2: Given user updates address and clicks 下一步, When returning to step3, Then the previously selected payment method is still selected.
- AC-3: Given step2 was opened via 編輯, When 下一步 clicked, Then \`PATCH /orders/:id/address\` is sent (not POST).

**Reference**: FR-3

**Requirement IDs**: FR-3

**Senior RD Estimate**
- 3

## Epic 6: Step 3 — 付款（PaymentProvider 抽象）

### Story 6.1 — \`PaymentProvider\` port 介面定義

**As a** 後端工程師, **I want** 一個統一的 PaymentProvider 介面, **so that** 三種金流可以同一份契約對接。

**Acceptance Criteria**
- AC-1: Given \`src/ports/payment-provider.ts\`, When inspected, Then it exports interface with methods \`authorize(order, payload)\`, \`validateMerchant(domain)\`, \`getProviderName()\`.
- AC-2: Given the interface, When \`authorize\` is called, Then return type is \`Promise<{ status: 'authorized'|'failed', providerTxnId?: string, errorCode?: string }>\`.
- AC-3: Given vitest, When the interface contract test runs, Then a fake provider implementing it compiles and passes.

**Reference**: Module 結構 §ports/payment-provider.ts；FR-2

**Requirement IDs**: FR-2

**Senior RD Estimate**
- 2

### Story 6.2 — TapPay (信用卡 + 3DS 2.0) provider 實作

**As a** 訪客, **I want** 用信用卡付款且支援 3DS 2.0, **so that** 我能完成主流量金流路徑。

**Acceptance Criteria**
- AC-1: Given a TapPay prime token, When \`tappayProvider.authorize(order, { prime })\` is called, Then it calls TapPay Pay-by-Prime API and returns \`{ status: 'authorized', providerTxnId }\` on success.
- AC-2: Given TapPay returns 3DS challenge, When \`authorize\` is called, Then the response includes \`redirect_url\` for the 3DS flow.
- AC-3: Given TapPay returns failure code 10003, When called, Then provider returns \`{ status: 'failed', errorCode: '10003' }\` (no exception thrown).
- AC-4: Given the provider, When inspected, Then no raw PAN is ever logged or stored.

**Reference**: Tech Stack §TapPay；NFR-2

**Requirement IDs**: FR-2, NFR-2

**Senior RD Estimate**
- 4

### Story 6.3 — Apple Pay merchant validation endpoint

**As a** 訪客, **I want** Apple Pay 按鈕可成功 validate merchant, **so that** Apple 錢包浮窗能彈出。

**Acceptance Criteria**
- AC-1: Given \`POST /payments/apple-pay/validate-merchant\` with \`{ validationURL }\`, When called, Then service calls Apple's validation URL with merchant cert and returns the merchant session JSON.
- AC-2: Given Apple returns non-200, When called, Then response is 502 with \`{ error: "merchant_validation_failed" }\`.
- AC-3: Given the endpoint, When inspected, Then merchant cert is loaded from a secret (not committed to repo).

**Reference**: FR-4

**Requirement IDs**: FR-4

**Senior RD Estimate**
- 4

### Story 6.4 — Apple Pay provider 實作（PaymentProvider）

**As a** 訪客, **I want** Apple Pay token 能授權扣款, **so that** Apple Pay 付款流程能完整跑完。

**Acceptance Criteria**
- AC-1: Given an Apple Pay payment token, When \`applePayProvider.authorize(order, { paymentToken })\` is called, Then it forwards the token to TapPay's Apple Pay endpoint and returns authorization result.
- AC-2: Given a malformed payment token, When called, Then provider returns \`{ status: 'failed', errorCode: 'invalid_token' }\`.

**Reference**: FR-4；Tech Stack

**Requirement IDs**: FR-2, FR-4

**Senior RD Estimate**
- 3

### Story 6.5 — LINE Pay v3 provider 實作

**As a** 訪客, **I want** 用 LINE Pay 付款, **so that** 我有非信用卡的選項。

**Acceptance Criteria**
- AC-1: Given \`linePayProvider.authorize(order)\`, When called, Then it calls LINE Pay v3 Request API and returns \`{ status: 'authorized', redirect_url }\` on success.
- AC-2: Given LINE Pay returns \`returnCode !== '0000'\`, When called, Then provider returns \`{ status: 'failed', errorCode: returnCode }\`.
- AC-3: Given a confirm callback \`GET /payments/line-pay/confirm\`, When called with valid \`transactionId\`, Then it calls LINE Pay Confirm API and emits \`payment.authorized\` Kafka event.

**Reference**: Tech Stack §LINE Pay

**Requirement IDs**: FR-2

**Senior RD Estimate**
- 4

### Story 6.6 — \`feature-checkout-step3\` 付款方式選擇 UI

**As a** 訪客, **I want** 在 step3 選擇信用卡 / Apple Pay / LINE Pay, **so that** 我可用偏好的方式結帳。

**Acceptance Criteria**
- AC-1: Given step3 mounts on a supported browser, When rendered, Then 三個 radio 選項可見：信用卡、Apple Pay、LINE Pay。
- AC-2: Given the browser does NOT support Apple Pay (\`window.ApplePaySession\` is undefined), When step3 mounts, Then Apple Pay 選項自動隱藏。
- AC-3: Given the user selects 信用卡, When rendered, Then TapPay card field iframe 載入（卡號永不入主頁 DOM）。

**Reference**: NFR-2；FR-4

**Requirement IDs**: FR-1, FR-2, FR-4, NFR-2

**Senior RD Estimate**
- 4

### Story 6.7 — Step3 提交 → 喚起金流 → 回寫狀態的整合

**As a** 訪客, **I want** 在 step3 按「下單」後成功完成付款並看到結果頁, **so that** 我知道訂單已建立。

**Acceptance Criteria**
- AC-1: Given user selected 信用卡 and submitted, When TapPay returns \`authorized\`, Then \`/checkout/success?orderId=...\` 頁面顯示且 order 狀態為 \`paid\`。
- AC-2: Given user selected Apple Pay, When Apple Pay sheet returns payment token, Then \`payment-gateway-adapter\` 被呼叫且 step3 顯示 loading 狀態。
- AC-3: Given payment fails with non-retryable error, When step3 receives failure, Then 顯示「付款失敗，請改用其他方式」並 keep user on step3。

**Reference**: System Architecture §Saga

**Requirement IDs**: FR-1, FR-2, FR-4

**Senior RD Estimate**
- 4

## Epic 7: 訂單寫入、庫存、Idempotency

### Story 7.1 — checkout-api: \`POST /orders\` 建立 pending_payment 訂單

**As a** 前端, **I want** 一個建立訂單的 API, **so that** step3 喚起金流前先有 order_id。

**Acceptance Criteria**
- AC-1: Given a valid request with \`cart_id\`, \`address_id\`, \`Idempotency-Key\`, When \`POST /orders\` is called, Then response is 201 with \`{ order_id, status: 'pending_payment' }\`.
- AC-2: Given the same \`Idempotency-Key\` is replayed within 24h, When called, Then response returns the original \`order_id\` without creating a new row.
- AC-3: Given creation succeeds, When inspected, Then \`order.created\` Kafka event is emitted with the new \`order_id\`.

**Reference**: System Architecture §Saga；NFR-1

**Requirement IDs**: FR-1, NFR-1, NFR-3

**Senior RD Estimate**
- 4

### Story 7.2 — Redis Lua 庫存暫扣

**As a** 後端工程師, **I want** 用 Redis Lua 原子腳本扣庫存, **so that** 5,000 並發下不會超賣。

**Acceptance Criteria**
- AC-1: Given SKU 庫存為 1, When 100 concurrent reservation requests come in, Then exactly 1 succeeds and 99 receive \`out_of_stock\`.
- AC-2: Given a reservation succeeds, When inspected after 15 minutes without confirmation, Then the reservation auto-expires (TTL) and stock is released.
- AC-3: Given \`inventory.Reserve(sku, qty)\`, When called, Then it runs a single Lua script (verified by unit test using miniredis).

**Reference**: NFR-4；Module 結構 §inventory

**Requirement IDs**: NFR-4

**Senior RD Estimate**
- 4

### Story 7.3 — checkout-api 觀測埋點（OpenTelemetry）

**As a** SRE, **I want** checkout-api 所有 HTTP 入口有 OTel trace, **so that** P95 < 500ms SLO 可被監控。

**Acceptance Criteria**
- AC-1: Given a request to \`POST /orders\`, When traced, Then a span named \`POST /orders\` exists with attributes \`http.status_code\`, \`order.id\`.
- AC-2: Given OTLP exporter is configured, When the service runs, Then spans are sent to the OTel collector endpoint (env \`OTEL_EXPORTER_OTLP_ENDPOINT\`).
- AC-3: Given a 5xx response, When inspected, Then the span has \`error=true\` and the corresponding error is recorded.

**Reference**: NFR-1 監控

**Requirement IDs**: NFR-1

**Senior RD Estimate**
- 3

## Milestone 3 — Saga、降級、實驗、ops (目標 ≤40h)

## Epic 8: Order Saga 與降級

### Story 8.1 — \`order-saga\` 服務骨架（Kafka consumer）

**As a** 後端工程師, **I want** order-saga 可消費 Kafka events, **so that** order 與 payment 狀態可被異步對齊。

**Acceptance Criteria**
- AC-1: Given order-saga is running, When a \`payment.authorized\` event arrives, Then the consumer's handler is invoked with the deserialized event.
- AC-2: Given \`go test ./...\`, When run, Then a contract test using kafka testcontainer verifies the consumer reads from \`payment-events\` topic.
- AC-3: Given the saga, When started, Then it commits offsets only after successful DB update.

**Reference**: System Architecture §order-saga

**Requirement IDs**: NFR-3

**Senior RD Estimate**
- 3

### Story 8.2 — Saga: payment.authorized → order.status=paid

**As a** 客戶, **I want** 付款成功後訂單狀態自動變為 paid, **so that** 我能在訂單列表看到正確狀態。

**Acceptance Criteria**
- AC-1: Given an order in \`pending_payment\`, When \`payment.authorized\` event arrives, Then DB \`orders.status\` becomes \`paid\` with \`paid_at\` timestamp.
- AC-2: Given the same event is delivered twice (Kafka at-least-once), When processed, Then status is updated only once (idempotent via \`provider_txn_id\` unique constraint).

**Reference**: NFR-3

**Requirement IDs**: NFR-3

**Senior RD Estimate**
- 3

### Story 8.3 — Saga: payment 超時 → pending_manual_charge 降級

**As a** 系統, **I want** 金流斷線時將訂單標記為 pending_manual_charge, **so that** 客服可介入補刷卡而不丟失訂單。

**Acceptance Criteria**
- AC-1: Given an order has been in \`pending_payment\` for > 5 minutes with no payment event, When the timeout job runs, Then \`orders.status\` becomes \`pending_manual_charge\`.
- AC-2: Given an order becomes \`pending_manual_charge\`, When inspected, Then an internal alert event \`order.manual_charge_required\` is emitted to Kafka.

**Reference**: NFR-3 降級

**Requirement IDs**: NFR-3

**Senior RD Estimate**
- 3

### Story 8.4 — Saga: payment.failed → order.status=cancelled + 庫存釋放

**As a** 系統, **I want** 付款失敗時自動釋放庫存暫扣, **so that** 其他客戶可繼續購買該 SKU。

**Acceptance Criteria**
- AC-1: Given an order in \`pending_payment\`, When \`payment.failed\` event arrives, Then \`orders.status\` becomes \`cancelled\` AND a Redis call releases the reservation.
- AC-2: Given the release call to Redis fails, When inspected, Then the saga retries up to 3 times before pushing to a DLQ.

**Reference**: NFR-3, NFR-4

**Requirement IDs**: NFR-3, NFR-4

**Senior RD Estimate**
- 3

## Epic 9: Experimentation 與 OPS

### Story 9.1 — \`order-experiment-svc\` 骨架 + GrowthBook 代理

**As a** PM, **I want** 一個 server-side experiment 服務代理 GrowthBook, **so that** SSR 階段就能取得分桶結果。

**Acceptance Criteria**
- AC-1: Given \`POST /experiments/eval\` with \`{ userId, experimentKey }\`, When called, Then response is \`{ variant: 'A'|'B', payload?: object }\`.
- AC-2: Given GrowthBook is unreachable, When called, Then the service returns the default (control) variant within 50ms (fail-open).

**Reference**: Module 結構 §order-experiment-svc；OPS-1

**Requirement IDs**: OPS-1, OPS-2

**Senior RD Estimate**
- 3

### Story 9.2 — Segment → GA4 + Mixpanel 雙寫驗證

**As a** PM, **I want** 所有 checkout 事件雙寫 GA4 與 Mixpanel, **so that** 兩邊報表可交叉驗證。

**Acceptance Criteria**
- AC-1: Given \`track('checkout_completed', {...})\` is called once, When Segment debugger inspected, Then the event reaches both GA4 destination and Mixpanel destination.
- AC-2: Given an event lacks \`userId\`, When dispatched, Then it falls back to \`anonymousId\` and still reaches both destinations.

**Reference**: OPS-2

**Requirement IDs**: OPS-2

**Senior RD Estimate**
- 2

### Story 9.3 — Istio 流量切分 5/25/100 漸進 rollout 設定

**As a** SRE, **I want** Istio VirtualService 設定支援 5/25/100 流量切分, **so that** 新版結帳可漸進 rollout。

**Acceptance Criteria**
- AC-1: Given \`checkout-api-vs.yaml\` with weight \`v1:95, v2:5\`, When applied, Then ≈5% of requests reach v2 deployment (verified via Prometheus metric \`istio_requests_total{destination_version=...}\`).
- AC-2: Given the same VirtualService with weights \`v1:0, v2:100\`, When applied, Then 100% requests reach v2.

**Reference**: OPS-1；Tech Stack §Istio

**Requirement IDs**: OPS-1

**Senior RD Estimate**
- 2

### Story 9.4 — SLO 看板（P95 latency + error rate + 降級率）

**As a** SRE, **I want** 一個 Grafana SLO 看板, **so that** 99.95% 可用率與 P95 SLO 可被持續監控。

**Acceptance Criteria**
- AC-1: Given the dashboard JSON is imported, When opened, Then panels show: checkout-api P95 latency, 5xx rate, \`pending_manual_charge\` ratio, payment-gateway-adapter availability.
- AC-2: Given P95 exceeds 500ms for 5 minutes, When evaluated, Then a Prometheus alert fires to PagerDuty (alert rule committed in repo).

**Reference**: NFR-1, NFR-3 監控

**Requirement IDs**: NFR-1, NFR-3

**Senior RD Estimate**
- 3

## Epic 10: 安全、合規與 E2E

### Story 10.1 — PCI-DSS：禁止卡號落地的 lint / 稽核腳本

**As a** 安全工程師, **I want** CI 在 payment-gateway-adapter 偵測潛在卡號落地, **so that** PCI 邊界不被破壞。

**Acceptance Criteria**
- AC-1: Given a commit adds a log statement containing PAN-like patterns (\`/\\b\\d{13,19}\\b/\`), When CI runs the custom lint, Then the PR is blocked.
- AC-2: Given no PAN-like literal exists, When CI runs, Then lint passes.

**Reference**: NFR-2

**Requirement IDs**: NFR-2

**Senior RD Estimate**
- 2

### Story 10.2 — Trivy + Snyk 容器與 SCA 掃描

**As a** 安全工程師, **I want** 每個服務 CI 跑 Trivy + Snyk, **so that** 容器與依賴漏洞在合併前被攔截。

**Acceptance Criteria**
- AC-1: Given a CI run on any of the three services, When scans complete, Then HIGH or CRITICAL findings cause the job to fail.
- AC-2: Given a \`.snyk\` file with documented exceptions, When scans run, Then exception entries are honored.

**Reference**: Build Baseline；NFR-2

**Requirement IDs**: NFR-2

**Senior RD Estimate**
- 2

### Story 10.3 — Playwright E2E：訪客三步結帳 happy path

**As a** QA, **I want** 一個 Playwright 測試覆蓋訪客 step1 → step2 → step3 (信用卡) → 成功頁, **so that** 主流量回歸可自動化。

**Acceptance Criteria**
- AC-1: Given seed data with a one-item cart, When the spec runs against staging, Then test passes within 60s.
- AC-2: Given TapPay sandbox is configured, When step3 submits, Then the test asserts URL becomes \`/checkout/success?orderId=...\`.

**Reference**: Build Baseline §前端測試

**Requirement IDs**: FR-1, FR-2

**Senior RD Estimate**
- 3

### Story 10.4 — Playwright E2E：Apple Pay merchant validation mock

**As a** QA, **I want** Apple Pay merchant validation flow 有 mock 化 E2E, **so that** FR-4 在 CI 可被驗證（無需真實 Apple cert）。

**Acceptance Criteria**
- AC-1: Given \`window.ApplePaySession\` is mocked, When the user clicks Apple Pay button, Then \`POST /payments/apple-pay/validate-merchant\` is called and a fake merchant session is returned.
- AC-2: Given the mock token is submitted, When step3 receives \`payment.authorized\` mock event, Then the success page is reached.

**Reference**: Build Baseline；FR-4

**Requirement IDs**: FR-4

**Senior RD Estimate**
- 3

### Story 10.5 — [HUMAN] PCI-DSS L1 第三方稽核準備文件

**As a** 合規負責人, **I want** 一份 payment-gateway-adapter 的稽核邊界文件, **so that** 外部 QSA 稽核時能快速通過。

**Acceptance Criteria**
- AC-1: 文件描述卡號流向、tokenization 邊界、密鑰管理機制。
- AC-2: 文件由資安負責人簽核並存於 GRC 系統。
- AC-3: 文件包含與 TapPay 之間的責任分界 (RACI)。

**Reference**: NFR-2

**Requirement IDs**: NFR-2

**Senior RD Estimate**
- 4

### Story 10.6 — [HUMAN] 5,000 並發負載測試與 NFR-1/NFR-4 驗收

**As a** SRE, **I want** 在 staging 跑 k6 負載測試模擬雙 11 尖峰, **so that** NFR-1 P95 < 500ms 與 NFR-4 5,000 並發可被簽收。

**Acceptance Criteria**
- AC-1: k6 腳本模擬 5,000 同時下單持續 10 分鐘, P95 < 500ms 且錯誤率 < 0.1%。
- AC-2: 測試報告附上 HPA 擴容曲線與 Redis、Postgres 資源水位。
- AC-3: 報告由 SRE 與 PM 雙簽核。

**Reference**: NFR-1, NFR-4

**Requirement IDs**: NFR-1, NFR-4

**Senior RD Estimate**
- 4`;
