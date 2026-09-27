---
name: phase6-workspace-ui
description: Phase 6 (Day 7–11) — the AI-first map workspace in React/Vite with JavaScript (JSX): MapLibre map with legends and season slider, Paper-vs-Planet timeline, evidence-pack panel, pdf.js evidence viewer, KPI cards, ECharts, TanStack table, live plan/route/cost/ledger panels, review queue, cross-filter synchronisation, NL command bar (EN/Tamil), workspace save/reopen/versions/export, demo mask, Playwright e2e. Load for any frontend work.
---

# Phase 6 — AI-first Workspace UI

**Owners:** frontend-engineer, and backend-engineer for types and exports.

**Principle:** the UI is a **renderer of `WorkspaceSpec`** plus a live **run viewer**. No screen is written for a particular query.

## Stack
- Vite + React 19 + **JavaScript (JSX), no TypeScript** (decision D-011). Use ESLint with `eslint-plugin-react` and `react-hooks`. Document component props with JSDoc.
- `maplibre-gl` with a free OSM/OpenFreeMap basemap and attribution
- `echarts-for-react`, `@tanstack/react-table`, `react-pdf`
- `zustand`, Tailwind + shadcn/ui, `@microsoft/fetch-event-source` for SSE
- Playwright for tests

API contracts: `make gen-schemas` generates zod schemas from the backend's OpenAPI into `web/src/api/schemas.js`. Validate every API response with them at runtime, and do not hand-write request or response shapes.

## Components
| Component | Behaviour |
|---|---|
| `CommandBar` | NL input (EN/TA), attach file (PDF/image/GeoJSON), example chips pulled from `GET /examples` (never hard-coded), history |
| `PlanPanel` | Live steps from SSE: tool/model, a *why* tooltip (router candidates and scores), status, ms, tokens, shadow $, privacy tier badge, attempts (fallbacks shown in amber) |
| `MapView` | Layers from `spec.map.layers`. Styles: by_stage (10-colour categorical, stage order), by_category (discrepancy), by_match (matched/unmatched/extent-diff/owner-diff), by_score (sequential). Legend always visible. Hover card. Click selects. `fit` to filters |
| `EvidenceViewer` | Page image (webp) with bbox overlays for the selected extraction(s). Next/prev evidence. Shows raw OCR text, engine/model, confidence and the verification badge |
| `KpiCards` | Value, label, verification badge (ACCEPT green, DOWNGRADE amber, REVIEW grey), confidence, and a click that applies the filter |
| `ChartGrid` | ECharts from `spec.charts`. Clicking a segment sets the `crossfilter` key |
| `ResultTable` | Columns from the spec. Sort and filter. A row click selects the parcel and opens its evidence. CSV export |
| `PaperPlanetTimeline` | **The signature view.** For the selected parcel, draw the smoothed NDVI (and BSI on a toggle) with valid observations as dots. Document events are vertical markers (3(1), award, payment, possession, mutation) with hover evidence. Season bands are shaded by land-use state, and findings sit as flags on the axis. Clicking a marker opens the document page; clicking a dot opens that date's chip |
| `SeasonSlider` | Scrubs through seasons 2019→now. The map recolours by land-use state for that season, and possession dates can be shown as a mask. Animated play button |
| `EvidencePackPanel` | Renders `EvidencePack`: Tamil page crops, chips grid, checks, critic challenge and result, verdict, caveats, ledger hash. Export to PDF |
| `SqlPanel` | Syntax-highlighted SQL/GIS operations per query_ref, with a copy button |
| `LedgerDrawer` | The run's hash chain, a verify button (calls the API) and the tamper-demo result |
| `ReviewQueue` | Items with a crop image, candidate values and accept/edit/reject actions |
| `CostPanel` | Routed vs baselines (from the eval report) and per-run actuals |
| Toggles | `Chaos` (sends chaos flags to the run), `Demo mask` (masks owner names as `Owner ••17`), `EN/தமிழ்` labels |

## State and synchronisation (Zustand)
- `selection: {kide?, doc?, extraction?, chartKey?}` and `filters: {village[], stage[], category[], …}`.
- Every widget subscribes to it. Changing a filter re-queries only the affected `query_ref`s through `POST /workspaces/{id}/query` (server-side filtering), or filters client-side when the result has fewer than 5k rows.
- Chains:
  - Selecting a parcel on the map highlights its table row, opens its related documents, and filters the charts.
  - Selecting a document reference flies the map to the parcel.
  - Selecting a chart bar filters the map and the table.

## Workspace lifecycle
- A follow-up NL request ("add an extent-by-stage chart", "only Block 5", "hide roads") posts `/runs` with `workspace_id`. The planner receives the current spec and returns a *patch*. The UI applies the new version with a smooth transition.
- Save as a named version. Reopen via a version list. Show diffs between versions (added/removed widgets).
- Exports: GeoJSON (the current map selection), CSV (tables), PDF report (server-side Playwright render of `/report/{workspace}/{v}`) and JSON spec.

## UX quality bar
- A loading skeleton on every widget while its step runs.
- An empty state with a reason ("0 parcels match: Block 11 does not exist in Peroorani").
- Errors that are human-readable and include a retry.
- Keyboard-accessible, contrast AA, and usable at a 1366×768 projector resolution.
- The first render of the workspace is under 2 s using cached layers. Simplify the FMB geometries for low zooms.

## Tests
- `make e2e` (Playwright, against fixtures mode `API_MODE=replay`):
  0. The Paper-vs-Planet parcel query → the timeline renders with document markers and NDVI → click the possession marker to open the LDR page with its bbox → the evidence pack exports as a PDF.
  1. Example A upload → map shows matched and unmatched parcels → click a parcel → the evidence bbox is visible.
  2. Lifecycle query → a chart bar click filters the table.
  3. Season slider across 2024→2026 recolours the map; the agri-claims GeoJSON upload returns a crop-presence table; NL modify adds a chart → save v2 → reopen v1 → export GeoJSON (file downloaded and valid).
  4. The chaos toggle shows an amber fallback step.
- Save screenshots to `docs/screens/` with demo mask ON.

## Exit gate
```
cd web && npm run build && npm run lint && cd .. && make e2e
```
The report covers the screenshots of the 4 flows and any spec features not yet rendered.
