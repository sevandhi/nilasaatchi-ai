import { useEffect, useState } from "react";
import { apiFetch } from "../api/client.js";

/**
 * Fetches `path` and re-fetches whenever `deps` changes. Returns a loading / empty / error /
 * not-implemented envelope every widget renders the same way (phase6 UX quality bar).
 *
 * @param {string|null} path pass null to skip fetching (e.g. waiting on a selection)
 * @param {{schema?: import("zod").ZodTypeAny, params?: object, isEmpty?: (data:any)=>boolean}} [opts]
 * @param {any[]} [deps]
 */
export function useApi(path, opts = {}, deps = []) {
  const { schema, params, isEmpty } = opts;
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!path) {
      setState({ status: "idle", data: null, error: null });
      return;
    }
    const controller = new AbortController();
    setState((s) => ({ ...s, status: "loading" }));
    apiFetch(path, { params, schema, signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        if (!res.ok) {
          setState({ status: res.kind === "not_implemented" ? "not_implemented" : "error", data: null, error: res });
          return;
        }
        const empty = isEmpty ? isEmpty(res.data) : Array.isArray(res.data?.items) ? res.data.items.length === 0 : false;
        setState({ status: empty ? "empty" : "ok", data: res.data, error: null, validated: res.validated });
      })
      .catch((e) => {
        if (e?.name === "AbortError") return;
        setState({ status: "error", data: null, error: { message: String(e) } });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, JSON.stringify(params), reloadToken, ...deps]);

  return { ...state, reload: () => setReloadToken((n) => n + 1) };
}
