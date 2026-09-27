import { READ_ONLY } from "../../api/client.js";
import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";

/**
 * Slim banner shown at the top of every page in the read-only cloud build
 * (`VITE_READ_ONLY=true`, see web/.env.cloud). Says what a visitor can and can't do here —
 * no mention of how the build is produced or served.
 */
export function ReadOnlyBanner() {
  const lang = useStore((s) => s.lang);
  if (!READ_ONLY) return null;
  return (
    <div
      className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-center text-xs font-medium text-amber-900"
      data-testid="readonly-banner"
      role="status"
    >
      {t("readonly_banner", lang)}
    </div>
  );
}
