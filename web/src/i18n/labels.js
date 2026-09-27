// Label-only EN/Tamil toggle (phase6 spec: "Include a Tamil/English label toggle"). This
// translates UI chrome (nav, headers, buttons, statuses) — not data values, which come from
// the API in whatever language the source document/model produced.
export const LABELS = {
  nav_overview: { en: "Overview", ta: "மேலோட்டம்" },
  nav_map: { en: "Map workspace", ta: "வரைபடம்" },
  nav_parcel: { en: "Parcel", ta: "நிலத்துண்டு" },
  nav_findings: { en: "Findings", ta: "கண்டறிதல்கள்" },
  nav_agent: { en: "Agent console", ta: "முகவர் பணிமேடை" },
  nav_documents: { en: "Documents", ta: "ஆவணங்கள்" },
  nav_models: { en: "Models & routing", ta: "மாதிரிகள் & வழிசெலுத்தல்" },
  nav_evaluation: { en: "Evaluation & honesty", ta: "மதிப்பீடு & நேர்மை" },
  nav_review: { en: "Review queue", ta: "மறுஆய்வு வரிசை" },

  demo_mask: { en: "Demo mask", ta: "மறைப்பு பயன்முறை" },
  lang_toggle: { en: "Language", ta: "மொழி" },
  loading: { en: "Loading…", ta: "ஏற்றுகிறது…" },
  empty: { en: "No data", ta: "தரவு இல்லை" },
  error: { en: "Something went wrong", ta: "பிழை ஏற்பட்டது" },
  not_implemented: { en: "Not available yet", ta: "இன்னும் கிடைக்கவில்லை" },
  retry: { en: "Retry", ta: "மீண்டும் முயற்சி" },
  confidence: { en: "Confidence", ta: "நம்பகத்தன்மை" },
  evidence: { en: "Evidence", ta: "ஆதாரம்" },
  verified: { en: "Accepted", ta: "ஏற்கப்பட்டது" },
  downgraded: { en: "Downgraded", ta: "தரம் குறைக்கப்பட்டது" },
  review: { en: "Needs review", ta: "மறுஆய்வு தேவை" },
  source: { en: "Source", ta: "மூலம்" },
  legend: { en: "Legend", ta: "விளக்கப்படம்" },
  season: { en: "Season", ta: "பருவம்" },
  play: { en: "Play", ta: "இயக்கு" },
  pause: { en: "Pause", ta: "இடைநிறுத்து" },
  caveats: { en: "Caveats", ta: "எச்சரிக்கைகள்" },
  limitations: { en: "Known limitations", ta: "வரம்புகள்" },

  readonly_banner: {
    en: "Read-only cloud demo: browse all data, maps, parcels and findings. Uploading documents, running the live AI agent and review actions are available in the full application.",
    ta: "படிக்க-மட்டும் கிளவுட் டெமோ: அனைத்து தரவு, வரைபடங்கள், நிலத்துண்டுகள் மற்றும் கண்டறிதல்களை உலாவலாம். ஆவணங்களை பதிவேற்றுவது, நேரடி AI முகவரை இயக்குவது மற்றும் மறுஆய்வு செயல்கள் முழு பயன்பாட்டில் மட்டுமே கிடைக்கும்.",
  },
};

export function t(key, lang) {
  const entry = LABELS[key];
  if (!entry) return key;
  return entry[lang] || entry.en;
}
