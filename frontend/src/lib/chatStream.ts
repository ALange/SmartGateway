export interface ChatMeta { status: number; upstream?: string; endpoint?: string; queue?: number; attempts?: string; cache?: string; replay?: string }
export interface ChatUpdate { text?: string; reasoning?: string; ttft?: number; finish?: string; tokens?: string; council?: any }

/** POST a chat completion (streaming or not) and report progress. Resolves with an error message if something failed. */
export async function chatCompletion(
  o: { url: string; body: any; headers?: Record<string, string>; signal?: AbortSignal },
  onMeta: (m: ChatMeta) => void,
  onUpdate: (u: ChatUpdate) => void,
): Promise<{ error?: string }> {
  const t0 = performance.now();
  try {
    const r = await fetch(o.url, { method: "POST", signal: o.signal, headers: { "Content-Type": "application/json", ...(o.headers || {}) }, body: JSON.stringify(o.body) });
    onMeta({
      status: r.status, upstream: r.headers.get("X-SG-Upstream") || undefined, endpoint: r.headers.get("X-SG-Endpoint") || undefined,
      queue: +(r.headers.get("X-SG-Queue-Ms") || 0), attempts: r.headers.get("X-SG-Attempts") || undefined, cache: r.headers.get("X-SG-Cache") || undefined, replay: r.headers.get("X-SG-Replay") || undefined,
    });
    if (o.body.stream && r.ok && r.body) {
      const reader = r.body.getReader(); const dec = new TextDecoder();
      let buf = "", text = "", think = "", first = 0, finish = "", tokens = "", council: any;
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() || "";
        for (const l of lines) {
          if (!l.startsWith("data:") || l.includes("[DONE]")) continue;
          try {
            const j = JSON.parse(l.slice(5)); const ch = j?.choices?.[0];
            const d = ch?.delta?.content, th = ch?.delta?.reasoning_content || ch?.delta?.reasoning;
            if ((d || th) && !first) first = performance.now() - t0;
            if (d) text += d;
            if (th) think += th;
            if (ch?.finish_reason) finish = ch.finish_reason;
            if (j?.usage) tokens = `${j.usage.prompt_tokens}→${j.usage.completion_tokens}`;
            if (j?.sg_council) council = j.sg_council;
            onUpdate({ text, reasoning: think, ttft: first, finish, tokens, council });
          } catch { /* partial line */ }
        }
      }
      return {};
    }
    const j = await r.json();
    if (!r.ok) return { error: j.error?.message || JSON.stringify(j) };
    const msg = j.choices?.[0]?.message;
    onUpdate({ text: msg?.content || "", reasoning: msg?.reasoning_content || msg?.reasoning, finish: j.choices?.[0]?.finish_reason,
      tokens: j.usage ? `${j.usage.prompt_tokens}→${j.usage.completion_tokens}` : undefined, ttft: performance.now() - t0, council: j.sg_council });
    return {};
  } catch (e: any) {
    return { error: e.name === "AbortError" ? "stopped" : e.message };
  }
}
