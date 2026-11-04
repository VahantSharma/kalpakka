const KEY = "kalpakka.anon_id";

/** Anonymous browser id (no account, no PII). Falls back to a per-page id if storage is blocked. */
export function anonId(): string {
  try {
    const existing = window.localStorage.getItem(KEY);
    if (existing) return existing;
    const id = `anon-${crypto.randomUUID()}`;
    window.localStorage.setItem(KEY, id);
    return id;
  } catch {
    return `anon-${crypto.randomUUID()}`;
  }
}
