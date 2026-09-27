import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";
import { READ_ONLY } from "../../api/client.js";

export function TopBar() {
  const { demoMask, toggleDemoMask, lang, setLang, chaos, toggleChaos } = useStore((s) => s);
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  function submit(e) {
    e.preventDefault();
    if (!q.trim()) return;
    navigate(`/agent?q=${encodeURIComponent(q)}`);
  }

  return (
    <header className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-2">
      <span className="text-lg font-semibold text-emerald-700">NilaSaatchi AI</span>
      <span className="hidden text-xs text-gray-400 sm:inline">Paper vs Planet</span>
      {READ_ONLY ? (
        <div className="flex-1" />
      ) : (
        <form onSubmit={submit} className="mx-4 flex-1">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask the agent (EN/தமிழ்)… e.g. Verify Melathattaparai survey 233"
            className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-emerald-500 focus:outline-none"
            data-testid="command-bar-input"
          />
        </form>
      )}
      {!READ_ONLY && (
        <label className="flex items-center gap-1 text-xs font-medium text-gray-600" title="Chaos toggle: simulate a model outage to demonstrate fallback">
          <input type="checkbox" checked={chaos} onChange={toggleChaos} data-testid="chaos-toggle" />
          Chaos
        </label>
      )}
      <label className="flex items-center gap-1 text-xs font-medium text-gray-600" title="Mask landowner names (ON by default)">
        <input type="checkbox" checked={demoMask} onChange={toggleDemoMask} data-testid="demo-mask-toggle" />
        {t("demo_mask", lang)}
      </label>
      <div className="flex rounded border border-gray-300 text-xs" role="group" aria-label={t("lang_toggle", lang)}>
        <button
          onClick={() => setLang("en")}
          className={`px-2 py-1 ${lang === "en" ? "bg-emerald-600 text-white" : "text-gray-600"}`}
          data-testid="lang-en"
        >
          EN
        </button>
        <button
          onClick={() => setLang("ta")}
          className={`px-2 py-1 ${lang === "ta" ? "bg-emerald-600 text-white" : "text-gray-600"}`}
          data-testid="lang-ta"
        >
          தமிழ்
        </button>
      </div>
    </header>
  );
}
