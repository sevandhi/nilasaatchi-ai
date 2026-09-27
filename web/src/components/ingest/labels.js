// EN/Tamil label pairs for the ingest UI's own chrome, kept local to this folder rather than in
// ../../i18n/labels.js so this feature doesn't touch a file another agent's pages read from.
// Follows the same pattern as t()/LABELS there: label-only translation, not a data translation.
const INGEST_LABELS = {
  upload_title: { en: "Upload a document", ta: "ஆவணத்தை பதிவேற்றவும்" },
  upload_drop: { en: "Drag a PDF here, or", ta: "PDF ஐ இங்கே இழுக்கவும், அல்லது" },
  upload_choose: { en: "choose a file", ta: "கோப்பைத் தேர்ந்தெடுக்கவும்" },
  upload_village: { en: "Village (optional)", ta: "கிராமம் (விருப்பம்)" },
  download_report: { en: "Download report (PDF)", ta: "அறிக்கையைப் பதிவிறக்கு (PDF)" },
  download_rows: { en: "Download rows (CSV)", ta: "வரிசைகளைப் பதிவிறக்கு (CSV)" },
  upload_doc_type: { en: "Document type (optional)", ta: "ஆவண வகை (விருப்பம்)" },
  upload_doc_type_auto: { en: "Let the AI decide", ta: "AI தீர்மானிக்கட்டும்" },
  upload_submit: { en: "Upload & process", ta: "பதிவேற்றி செயலாக்கு" },
  upload_uploading: { en: "Uploading…", ta: "பதிவேற்றுகிறது…" },
  upload_honesty: {
    en: "Extraction is AI-assisted. Values the model is unsure about go to the Review queue for a person to check.",
    ta: "பிரித்தெடுத்தல் AI உதவியுடன் செய்யப்படுகிறது. மாதிரி உறுதியாக இல்லாத மதிப்புகள் மறுஆய்வு வரிசைக்கு அனுப்பப்படும்.",
  },
  upload_service_unavailable: { en: "Upload service not available.", ta: "பதிவேற்ற சேவை இப்போது கிடைக்கவில்லை." },
  upload_invalid_type: { en: "Please choose a PDF file.", ta: "தயவுசெய்து PDF கோப்பைத் தேர்ந்தெடுக்கவும்." },
  upload_too_large: { en: "File is larger than 50 MB.", ta: "கோப்பு 50 MB க்கும் பெரியது." },
  duplicate_title: { en: "This document is already in the system.", ta: "இந்த ஆவணம் ஏற்கனவே கணினியில் உள்ளது." },
  duplicate_link: { en: "View it on the Documents page", ta: "ஆவணங்கள் பக்கத்தில் காண்க" },
  stage_title: { en: "Progress", ta: "முன்னேற்றம்" },
  result_title: { en: "Result", ta: "முடிவு" },
  failed_title: { en: "Processing failed", ta: "செயலாக்கம் தோல்வியடைந்தது" },
  linked_parcels: { en: "Linked parcels", ta: "இணைக்கப்பட்ட நிலத்துண்டுகள்" },
  new_findings: { en: "new findings", ta: "புதிய கண்டறிதல்கள்" },
  review_items: { en: "review items", ta: "மறுஆய்வு உருப்படிகள்" },
  view_findings: { en: "View findings", ta: "கண்டறிதல்களைக் காண்க" },
  view_review: { en: "View review queue", ta: "மறுஆய்வு வரிசையைக் காண்க" },
  recent_uploads: { en: "Recent uploads", ta: "சமீபத்திய பதிவேற்றங்கள்" },
  no_recent_uploads: { en: "No documents uploaded yet.", ta: "இதுவரை ஆவணங்கள் பதிவேற்றப்படவில்லை." },
  freshness_title: { en: "Satellite data freshness", ta: "செயற்கைக்கோள் தரவு புதுமை" },
  latest_scene: { en: "Latest image", ta: "சமீபத்திய படம்" },
  scene_count: { en: "Scenes", ta: "படங்கள்" },
  last_refresh: { en: "Last checked", ta: "கடைசியாக சரிபார்க்கப்பட்டது" },
  check_new_images: { en: "Check for new satellite images", ta: "புதிய செயற்கைக்கோள் படங்களை சரிபார்க்கவும்" },
  checking: { en: "Checking…", ta: "சரிபார்க்கிறது…" },
  refresh_running: { en: "A refresh is already running.", ta: "ஒரு புதுப்பித்தல் ஏற்கனவே இயங்குகிறது." },
  new_scenes: { en: "new scenes", ta: "புதிய படங்கள்" },
  parcels_updated: { en: "parcels updated", ta: "நிலத்துண்டுகள் புதுப்பிக்கப்பட்டன" },
};

export function it(key, lang) {
  const entry = INGEST_LABELS[key];
  if (!entry) return key;
  return entry[lang] || entry.en;
}
