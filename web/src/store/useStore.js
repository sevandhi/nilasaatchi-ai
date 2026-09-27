import { create } from "zustand";

// One store for selection + filters + UI toggles (phase6-workspace-ui skill: "One Zustand
// store for selection and filters. A click on a parcel, a document reference, a chart segment
// or a table row updates every view.").
export const useStore = create((set, get) => ({
  // --- cross-filter selection ---
  selection: {
    parcelUid: null,
    documentId: null,
    page: null,
    extractionId: null,
    chartKey: null,
    findingId: null,
    reviewId: null,
    runId: null,
  },
  selectParcel: (parcelUid) => set((s) => ({ selection: { ...s.selection, parcelUid, chartKey: null } })),
  selectDocument: (documentId, page = 1, extractionId = null) =>
    set((s) => ({ selection: { ...s.selection, documentId, page, extractionId } })),
  selectExtraction: (extractionId) => set((s) => ({ selection: { ...s.selection, extractionId } })),
  selectChart: (chartKey) => set((s) => ({ selection: { ...s.selection, chartKey } })),
  selectFinding: (findingId) => set((s) => ({ selection: { ...s.selection, findingId } })),
  selectRun: (runId) => set((s) => ({ selection: { ...s.selection, runId } })),
  clearSelection: () =>
    set({ selection: { parcelUid: null, documentId: null, page: null, extractionId: null, chartKey: null, findingId: null, reviewId: null, runId: null } }),

  // --- filters (map / findings / documents share these where it makes sense) ---
  filters: { village: null, stage: null, category: null, severity: null, block: null, level: null, season: "2025-rabi" },
  setFilter: (key, value) => set((s) => ({ filters: { ...s.filters, [key]: value } })),
  resetFilters: () => set({ filters: { village: null, stage: null, category: null, severity: null, block: null, level: null, season: "2025-rabi" } }),

  // --- UI toggles ---
  demoMask: true,
  toggleDemoMask: () => set((s) => ({ demoMask: !s.demoMask })),
  lang: "en",
  setLang: (lang) => set({ lang }),
  chaos: false,
  toggleChaos: () => set((s) => ({ chaos: !s.chaos })),
  colorBy: "stage",
  setColorBy: (colorBy) => set({ colorBy }),

  // --- workspace lifecycle (agent console / map workspace) ---
  workspaceSpec: null,
  workspaceId: null,
  setWorkspaceSpec: (spec) => set({ workspaceSpec: spec, workspaceId: spec?.workspace_id ?? get().workspaceId }),
}));
