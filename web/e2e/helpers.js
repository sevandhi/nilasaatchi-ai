const API_BASE = process.env.VITE_API_BASE || "http://localhost:8000";

/** Skips the current test with a clear reason if the API (or one endpoint on it) isn't up —
 * phase6 SKILL: "mark tests skipped with a reason if an endpoint isn't ready". */
export async function requireApi(test) {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) test.skip(true, `API at ${API_BASE} responded ${res.status} — run 'make api' first`);
  } catch {
    test.skip(true, `Cannot reach the API at ${API_BASE} — run 'make api' first`);
  }
}

export async function requireEndpoint(test, path) {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (res.status === 404) {
      const body = await res.json().catch(() => null);
      if (!body || body.detail === "Not Found") {
        test.skip(true, `${path} is not implemented on the API yet`);
      }
    }
  } catch {
    test.skip(true, `Cannot reach ${API_BASE}${path}`);
  }
}

export { API_BASE };
