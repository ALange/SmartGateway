import { useState } from "react";
import { Loader2, Play } from "lucide-react";
import { adminGwUrl, gwUrl } from "../api";
import { chatCompletion } from "../lib/chatStream";
import FlowGraph from "./FlowGraph";
import { CapChip, Modal, ms } from "./ui";

const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
type Row = { n: number; status?: number; total?: number; ttft?: number; upstream?: string; endpoint?: string; queue?: number; attempts?: string; cache?: string; text?: string; reasoning?: string; finish?: string; tokens?: string; error?: string; done: boolean };

export default function TestGateway({ gw, onClose }: { gw: any; onClose: () => void }) {
  const [model, setModel] = useState(gw.models[0]?.public_name || "");
  const caps: string[] = gw.models.find((m: any) => m.public_name === model)?.capabilities || [];
  const modes = [
    ...(caps.includes("embedding") && !caps.includes("chat") ? [] : [["chat", "Chat"], ["stream", "Chat (streaming)"]]),
    ...(caps.includes("vision") ? [["vision", "Vision (image input)"]] : []),
    ...(caps.includes("embedding") ? [["embed", "Embedding"]] : []),
  ];
  const [mode, setMode] = useState(modes[0]?.[0] || "chat");
  const [prompt, setPrompt] = useState("Say hello in one short sentence.");
  const [count, setCount] = useState(1);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [rows, setRows] = useState<Row[]>([]);
  const [running, setRunning] = useState(false);

  const onModel = (m: string) => {
    setModel(m);
    const c: string[] = gw.models.find((x: any) => x.public_name === m)?.capabilities || [];
    setMode(c.includes("chat") ? "chat" : "embed");
  };

  const one = async (n: number, update: (p: Partial<Row>) => void) => {
    const t0 = performance.now();
    const embed = mode === "embed";
    const content: any = mode === "vision" ? [{ type: "text", text: prompt }, { type: "image_url", image_url: { url: PNG } }] : prompt;
    if (embed) {
      try {
        const r = await fetch(`${adminGwUrl(gw.slug)}/embeddings`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model, input: prompt }) });
        update({ status: r.status, upstream: r.headers.get("X-SG-Upstream") || undefined, endpoint: r.headers.get("X-SG-Endpoint") || undefined, queue: +(r.headers.get("X-SG-Queue-Ms") || 0), attempts: r.headers.get("X-SG-Attempts") || undefined });
        const j = await r.json();
        update(r.ok ? { text: `vector of ${j.data?.[0]?.embedding?.length} dimensions` } : { error: j.error?.message || JSON.stringify(j) });
      } catch (e: any) { update({ error: e.message }); }
    } else {
      const { error } = await chatCompletion(
        { url: `${adminGwUrl(gw.slug)}/chat/completions`, body: { model, messages: [{ role: "user", content }], max_tokens: maxTokens, stream: mode === "stream" } },
        (m) => update({ status: m.status, upstream: m.upstream, endpoint: m.endpoint, queue: m.queue, attempts: m.attempts, cache: m.cache }),
        (u) => update({ ...u }),
      );
      if (error) update({ error });
    }
    update({ total: performance.now() - t0, done: true });
  };

  const run = async () => {
    setRunning(true);
    const init = Array.from({ length: count }, (_, i) => ({ n: i + 1, done: false } as Row));
    setRows(init);
    await Promise.all(init.map((_, i) => one(i, (p) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...p } : r))))));
    setRunning(false);
  };

  const dist = rows.reduce((a: Record<string, number>, r) => { if (r.upstream) { const k = `${r.upstream} @ ${r.endpoint}`; a[k] = (a[k] || 0) + 1; } return a; }, {});

  return (
    <Modal title={`Test gateway · ${gw.name}`} onClose={onClose} wide>
      {gw.models.length === 0 ? <div className="text-sm text-[#8a92a6]">This gateway has no public models yet.</div> : (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-4">
            <div><label className="lbl">Public model</label><select className="input" value={model} onChange={(e) => onModel(e.target.value)}>{gw.models.map((m: any) => <option key={m.id}>{m.public_name}</option>)}</select></div>
            <div><label className="lbl">Request type</label><select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>{modes.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
            <div><label className="lbl">Max tokens</label><input type="number" min={1} className="input" value={maxTokens} onChange={(e) => setMaxTokens(Math.max(1, +e.target.value || 1))} /></div>
            <div><label className="lbl">Parallel requests</label><input type="number" min={1} max={30} className="input" value={count} onChange={(e) => setCount(Math.max(1, Math.min(30, +e.target.value || 1)))} /></div>
          </div>
          <div className="flex flex-wrap gap-1 text-xs text-[#8a92a6]">Capabilities: {caps.map((c) => <CapChip key={c} cap={c} />)}</div>
          <div><label className="lbl">{mode === "embed" ? "Input text" : "Prompt"}</label><textarea className="input" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></div>
          <div className="flex items-center justify-between">
            <span className="mono text-xs text-[#8a92a6]">POST {gwUrl(gw.slug)}/{mode === "embed" ? "embeddings" : "chat/completions"}</span>
            <button className="btn primary" disabled={running || !prompt} onClick={run}>{running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Run test</button>
          </div>
          <FlowGraph gw={gw} />
          {rows.length > 0 && (
            <div className="space-y-2">
              {count > 1 && Object.keys(dist).length > 0 && (
                <div className="rounded-md border border-white/[0.08] p-3 text-xs"><div className="mb-1 text-[#8a92a6]">Distribution across upstreams</div>
                  {Object.entries(dist).map(([k, v]) => <div key={k} className="flex justify-between"><span className="mono">{k}</span><span>{v}</span></div>)}</div>
              )}
              <div className="max-h-[320px] space-y-2 overflow-auto scroll-thin">
                {rows.map((r) => (
                  <div key={r.n} className="rounded-md border border-white/[0.08] bg-[#0c0e13] p-3 text-xs">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span className="chip" style={{ color: r.error ? "#d9776f" : r.done ? "#5fb585" : "#d1a553", background: (r.error ? "#d9776f" : r.done ? "#5fb585" : "#d1a553") + "22" }}>{r.error ? `error ${r.status || ""}` : r.done ? `${r.status} OK` : "running…"}</span>
                      {r.upstream && <span>→ <span className="mono">{r.upstream}</span> <span className="text-[#8a92a6]">@ {r.endpoint}</span></span>}
                      {r.total != null && <span className="text-[#8a92a6]">{ms(r.total)}{r.ttft ? ` · TTFT ${ms(r.ttft)}` : ""}</span>}
                      {!!r.queue && <span className="text-[#d1a553]">queued {ms(r.queue)}</span>}
                      {r.tokens && <span className="text-[#8a92a6]">tokens {r.tokens}</span>}
                      {r.finish && <span className={r.finish === "length" ? "text-[#d1a553]" : "text-[#8a92a6]"}>finish: {r.finish}</span>}
                      {r.cache && <span className="chip" style={{ color: r.cache === "hit" ? "#5fb585" : "#d1a553", background: (r.cache === "hit" ? "#5fb585" : "#d1a553") + "22" }}>cache {r.cache}</span>}
                      {r.attempts && +r.attempts > 1 && <span className="text-[#d1a553]">{r.attempts} attempts</span>}
                    </div>
                    {r.reasoning && <details className="mt-2 text-[#8a92a6]"><summary className="cursor-pointer">Reasoning ({r.reasoning.length} chars)</summary><div className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap scroll-thin italic">{r.reasoning}</div></details>}
                    {(r.text || r.error) && <div className={`mt-2 whitespace-pre-wrap ${r.error ? "text-red-300" : "text-[#dde1ea]"}`}>{r.error || r.text}</div>}
                    {r.done && !r.error && !r.text && mode !== "embed" && <div className="mt-2 text-[#d1a553]">No answer text returned{r.finish === "length" ? " — the token limit was reached while the model was still reasoning. Increase Max tokens." : "."}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
