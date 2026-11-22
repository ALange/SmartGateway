export async function api<T = any>(path: string, opts: RequestInit & { json?: any } = {}): Promise<T> {
  const { json, ...rest } = opts;
  const r = await fetch(path, {
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers || {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (r.status === 401 && path.startsWith("/api/") && !path.startsWith("/api/login")) window.dispatchEvent(new Event("sg-unauth"));
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const d = await r.json(); msg = d.detail?.message || d.detail || d.error?.message || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

export const CAP_STYLE: Record<string, { color: string; label: string }> = {
  chat: { color: "#8e8e9a", label: "chat" },
  reasoning: { color: "#a78bd4", label: "reasoning" },
  vision: { color: "#5aa9d6", label: "vision" },
  audio: { color: "#d08f5a", label: "audio" },
  video: { color: "#c07b9d", label: "video" },
  ocr: { color: "#c4a84f", label: "ocr" },
  embedding: { color: "#5fb585", label: "embedding" },
  tools: { color: "#8aa0b8", label: "tools" },
  tts: { color: "#c9886f", label: "tts" },
  image_gen: { color: "#b58ad0", label: "image gen" },
};
export const ALL_CAPS = Object.keys(CAP_STYLE);
export const STRATEGIES: Record<string, string> = {
  least_busy: "Least busy (free slots / latency)",
  weighted_round_robin: "Weighted round-robin",
  priority_failover: "Priority with failover",
  fastest: "Fastest (lowest latency)",
  random: "Random",
  tiered: "Tiered (small → large by prompt complexity)",
};
export const PALETTE = ["#7c6cf0", "#4cc38a", "#e0a458", "#5fa3d9", "#d9776f", "#b58ae0", "#5fb5b0"];
export const gwUrl = (slug: string) => `${location.origin}/gw/${slug}/v1`;
export const unifiedUrl = () => `${location.origin}/unified/v1`;
export const adminGwUrl = (slug: string) => `${location.origin}/api/admin-gw/${slug}/v1`;
