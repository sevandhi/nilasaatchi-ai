import { NavLink } from "react-router-dom";
import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";

const ITEMS = [
  { to: "/", label: "nav_overview", num: 1 },
  { to: "/map", label: "nav_map", num: 2 },
  { to: "/parcel", label: "nav_parcel", num: 3 },
  { to: "/findings", label: "nav_findings", num: 4 },
  { to: "/agent", label: "nav_agent", num: 5 },
  { to: "/documents", label: "nav_documents", num: 6 },
  { to: "/models", label: "nav_models", num: 7 },
  { to: "/review", label: "nav_review", num: 8 },
];

export function NavRail() {
  const lang = useStore((s) => s.lang);
  return (
    <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-gray-200 bg-white p-2" aria-label="Primary">
      {ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className={({ isActive }) =>
            `flex items-center gap-2 rounded px-2 py-2 text-sm font-medium ${
              isActive ? "bg-emerald-600 text-white" : "text-gray-700 hover:bg-gray-100"
            }`
          }
        >
          <span className="w-4 text-xs opacity-60">{item.num}</span>
          {t(item.label, lang)}
        </NavLink>
      ))}
    </nav>
  );
}
