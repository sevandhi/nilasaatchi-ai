// Demo-mode owner masking (D-032/033). The API already masks owner names server-side by
// default (`masked: true` on evidence rows) — this is a client-side backstop so any owner
// string that slips through (e.g. inside free-text `row` payloads) is still masked whenever
// "Demo mask" is ON, which is the default.
const OWNER_KEYS = ["owner_name", "name", "owner", "father_name", "guardian_name"];

export function maskOwnerToken(name, position) {
  if (!name) return name;
  const suffix = position !== undefined && position !== null ? position : Math.abs(hashStr(String(name))) % 99;
  return `Owner ••${suffix}`;
}

function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i);
  return h;
}

/** Recursively mask likely-owner fields in an arbitrary JSON payload when demoMask is on. */
export function maskDeep(value, demoMask) {
  if (!demoMask) return value;
  if (Array.isArray(value)) return value.map((v) => maskDeep(v, demoMask));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = OWNER_KEYS.includes(k) && typeof v === "string" ? maskOwnerToken(v) : maskDeep(v, demoMask);
    }
    return out;
  }
  return value;
}
