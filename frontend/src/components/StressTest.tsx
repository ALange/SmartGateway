import { useEffect, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, CheckCircle2, Gauge, History, Loader2, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import { api } from "../api";
import FlowGraph from "./FlowGraph";
import { fmt, Modal } from "./ui";

const tip = { contentStyle: { background: "#181c25", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }, cursor: { stroke: "rgba(255,255,255,0.15)" } };
const PRESETS: Record<string, { label: string; levels: string; stage: number; note: string }> = {
  quick: { label: "Quick", levels: "1, 2, 4, 8", stage: 10, note: "about 1 minute" },
  standard: { label: "Standard", levels: "1, 2, 4, 8, 16", stage: 15, note: "about 1.5 minutes" },
  thorough: { label: "Thorough", levels: "1, 2, 4, 8, 16, 32, 64", stage: 20, note: "about 3 minutes" },
};
const PROMPTS: [string, number][] = [["Short (~100 tokens)", 100], ["Medium (~500 tokens)", 500], ["Long (~1,000 tokens)", 1000], ["Very long (~4,000 tokens)", 4000]];
const n1 = (v?: number | null) => (v == null ? "–" : v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1));
const ms = (v?: number | null) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
const dur = (s: number) => (s >= 90 ? `${(s / 60).toFixed(1)} min` : `${Math.round(s)} s`);

function Tile({ label, value, sub, tone }: { label: string; value: any; sub?: string; tone?: string }) {
  return <div className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3"><div className="text-xs text-[#8a92a6]">{label}</div><div className="mt-1 text-xl font-semibold" style={tone ? { color: tone } : undefined}>{value}</div>{sub && <div className="mt-0.5 text-[11px] text-[#6b7196]">{sub}</div>}</div>;
}

function Charts({ stages }: { stages: any[] }) {
  const data = stages.filter((s) => s.completed > 0).map((s) => ({ c: s.concurrency, agg: +s.agg_tps.toFixed(1), per: s.gen_tps ? +s.gen_tps.p50.toFixed(1) : null, ttft50: s.ttft_ms?.p50 ?? null, ttft95: s.ttft_ms?.p95 ?? null, q50: s.queue_ms?.p50 ?? 0 }));
  if (!data.length) return null;
  const axis = { tick: { fill: "#6b7196", fontSize: 11 }, axisLine: false, tickLine: false } as const;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-white/[0.08] p-3"><div className="mb-2 text-sm font-semibold">Throughput vs. concurrency</div>
        <div className="h-[220px]"><ResponsiveContainer><LineChart data={data}><CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey="c" {...axis} label={{ value: "concurrent requests", fill: "#6b7196", fontSize: 11, position: "insideBottom", offset: -2 }} />
          <YAxis yAxisId="l" {...axis} width={44} /><YAxis yAxisId="r" orientation="right" {...axis} width={40} /><Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 12 }} />
          <Line yAxisId="l" type="monotone" dataKey="agg" name="total tok/s" stroke="#3b6fd8" strokeWidth={2.5} dot />
          <Line yAxisId="r" type="monotone" dataKey="per" name="per request tok/s" stroke="#5fb585" strokeWidth={2} dot connectNulls /></LineChart></ResponsiveContainer></div></div>
      <div className="rounded-lg border border-white/[0.08] p-3"><div className="mb-2 text-sm font-semibold">Latency vs. concurrency</div>
        <div className="h-[220px]"><ResponsiveContainer><LineChart data={data}><CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey="c" {...axis} label={{ value: "concurrent requests", fill: "#6b7196", fontSize: 11, position: "insideBottom", offset: -2 }} />
          <YAxis {...axis} width={52} tickFormatter={(v) => (v >= 1000 ? `${v / 1000}s` : `${v}ms`)} /><Tooltip {...tip} formatter={(v: any) => ms(v)} /><Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="ttft50" name="first token p50" stroke="#7d9be0" strokeWidth={2} dot /><Line type="monotone" dataKey="ttft95" name="first token p95" stroke="#d1a553" strokeWidth={2} dot />
          <Line type="monotone" dataKey="q50" name="queue wait p50" stroke="#d9776f" strokeWidth={2} dot /></LineChart></ResponsiveContainer></div></div>
    </div>
  );
}

function StageTable({ stages, summary }: { stages: any[]; summary: any }) {
  return (
    <div className="overflow-x-auto scroll-thin"><table className="w-full text-sm">
      <thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">{["Concurrency", "Done", "Errors", "Total tok/s", "Per request tok/s", "Prompt tok/s", "First token p50 / p95", "Queue p50 / max", "Latency p95", "Spread"].map((h) => <th key={h} className="pb-2 pr-3 font-medium">{h}</th>)}</tr></thead>
      <tbody>{stages.map((s) => {
        const peak = summary?.peak?.concurrency === s.concurrency, sat = summary?.saturation_concurrency === s.concurrency, rec = summary?.recommended_concurrency === s.concurrency;
        return (
          <tr key={s.concurrency} className={`border-t border-white/5 ${peak ? "bg-[#5fb585]/[0.07]" : ""}`}>
            <td className="py-2 pr-3 font-medium">{s.concurrency}{peak && <span className="chip ml-2 text-[#7fd1a3]" style={{ background: "#5fb58522" }}>peak</span>}{rec && !peak && <span className="chip ml-2 text-[#7d9be0]" style={{ background: "#7d9be022" }}>comfortable</span>}{sat && <span className="chip ml-2 text-[#e6c27a]" style={{ background: "#d1a55322" }}>saturated</span>}{s.interrupted && <span className="chip ml-2 text-[#8a92a6]" style={{ background: "#ffffff0d" }}>partial</span>}</td>
            <td className="pr-3">{s.completed}{s.low_samples && <span title="Few completed requests: numbers are rough" className="text-[#d1a553]"> *</span>}</td>
            <td className={`pr-3 ${s.errors ? "text-[#d9776f]" : "text-[#6b7196]"}`} title={Object.entries(s.error_breakdown || {}).map(([k, v]) => `${k}: ${v}`).join(", ")}>{s.errors}</td>
            <td className="pr-3 font-semibold">{n1(s.agg_tps)}</td><td className="pr-3">{n1(s.gen_tps?.p50)}</td><td className="pr-3">{n1(s.prompt_tps?.p50)}</td>
            <td className="pr-3">{ms(s.ttft_ms?.p50)} / {ms(s.ttft_ms?.p95)}</td><td className="pr-3">{ms(s.queue_ms?.p50)} / {ms(s.queue_ms?.max)}</td><td className="pr-3">{ms(s.latency_ms?.p95)}</td>
            <td className="text-xs text-[#8a92a6]">{Object.entries(s.upstreams || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "–"}</td></tr>);
      })}</tbody></table></div>
  );
}

function Result({ snap }: { snap: any }) {
  const sm = snap.summary, stages = snap.stages || [];
  const peak = sm?.peak;
  return (
    <div className="space-y-4">
      {snap.status === "error" && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">The test failed: {snap.error}</div>}
      {snap.stop_reason && <div className="flex items-start gap-2 rounded-md border border-[#d1a553]/30 bg-[#d1a553]/10 p-3 text-sm text-[#e6c27a]"><AlertTriangle size={16} className="mt-0.5 shrink-0" />{snap.stop_reason.charAt(0).toUpperCase() + snap.stop_reason.slice(1)}.</div>}
      {peak ? (<>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Tile label="Peak throughput" value={<>{n1(peak.agg_tps)} <span className="text-sm font-normal text-[#8a92a6]">tok/s</span></>} sub={`at ${peak.concurrency} concurrent request${peak.concurrency > 1 ? "s" : ""}`} tone="#7fd1a3" />
          <Tile label="Single-stream speed" value={<>{n1(sm.single_stream_tps)} <span className="text-sm font-normal text-[#8a92a6]">tok/s</span></>} sub="one request at a time" />
          <Tile label="Comfortable concurrency" value={sm.recommended_concurrency ?? "–"} sub="each request keeps ≥70% of its speed, no queueing" />
          <Tile label="Saturation at" value={sm.saturation_concurrency ?? "not reached"} sub={sm.slots ? `upstream slots: ${sm.slots}` : undefined} tone={sm.saturation_concurrency ? "#e6c27a" : undefined} />
          <Tile label="First token at peak" value={ms(peak.ttft_ms_p50)} sub={`latency p95 ${ms(peak.latency_ms_p95)}`} />
        </div>
        <ul className="space-y-1 rounded-lg border border-white/[0.08] bg-white/[0.02] p-3 text-sm text-[#b8bddb]">{sm.verdict.map((v: string, i: number) => <li key={i} className="flex gap-2"><CheckCircle2 size={14} className="mt-1 shrink-0 text-[#5fb585]" />{v}</li>)}</ul>
      </>) : sm && <div className="rounded-md border border-[#d1a553]/30 bg-[#d1a553]/10 p-3 text-sm text-[#e6c27a]">{sm.verdict?.[0]}</div>}
      <Charts stages={stages} />
      {stages.length > 0 && <StageTable stages={stages} summary={sm} />}
      {stages.some((s: any) => s.low_samples) && <p className="text-xs text-[#d1a553]">* Few requests finished in that stage (long answers or short stages), so its per-request numbers are rough. Use a longer stage time or fewer output tokens for steadier results.</p>}
    </div>
  );
}

export default function StressTest({ gw, onClose }: { gw: any; onClose: () => void }) {
  const models: string[] = gw.models.map((m: any) => m.public_name);
  const [view, setView] = useState<"config" | "running" | "result">("config");
  const [model, setModel] = useState(models[0] || "");
  const [scenario, setScenario] = useState<"prose" | "code">("prose");
  const [preset, setPreset] = useState("standard");
  const [levels, setLevels] = useState(PRESETS.standard.levels);
  const [stage, setStage] = useState(PRESETS.standard.stage);
  const [promptTokens, setPromptTokens] = useState(500);
  const [maxTokens, setMaxTokens] = useState(128);
  const [ack, setAck] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [snap, setSnap] = useState<any>(null);
  const [hist, setHist] = useState<any[]>([]);
  const jobRef = useRef<string | null>(null);

  const loadHist = () => api(`/api/gateways/${gw.id}/stress/runs`).then(setHist).catch(() => {});
  useEffect(() => {
    loadHist();
    api("/api/stress/current").then((c) => { if (c) { jobRef.current = c.job_id; setSnap(c); setView("running"); } }).catch(() => {});
  }, []);
  useEffect(() => {
    if (view !== "running") return;
    const t = setInterval(async () => {
      if (!jobRef.current) return;
      try { const s = await api(`/api/stress/${jobRef.current}`); setSnap(s); if (s.status !== "running") { setView("result"); loadHist(); } } catch { setView("config"); }
    }, 1000);
    return () => clearInterval(t);
  }, [view]);

  const hosted: string[] = Array.from(new Set((gw.models || []).flatMap((m: any) => (m.members || []).map((x: any) => x.provider)).filter(Boolean))).map((p: any) => (p === "opencode-go" ? "OpenCode Go" : "OpenCode Zen"));
  const parsedLevels = levels.split(/[ ,;]+/).map((x) => parseInt(x, 10)).filter((x) => x > 0);
  const est = parsedLevels.length * (stage + 3);
  const pickPreset = (k: string) => { setPreset(k); if (PRESETS[k]) { setLevels(PRESETS[k].levels); setStage(PRESETS[k].stage); } };

  const start = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api(`/api/gateways/${gw.id}/stress`, { method: "POST", json: { model, scenario, levels: parsedLevels, stage_seconds: stage, prompt_tokens: promptTokens, max_tokens: maxTokens, acknowledge: ack } });
      jobRef.current = r.job_id; setSnap(null); setView("running");
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const stop = async () => { if (jobRef.current) await api(`/api/stress/${jobRef.current}/stop`, { method: "POST" }); };
  const openRun = async (id: number) => {
    const r = await api(`/api/stress/runs/${id}`);
    setSnap({ status: r.status, stop_reason: r.stop_reason, summary: r.result.summary, stages: r.result.stages, error: r.result.error, config: r.config, started: r.started_at, model: r.public_model });
    setView("result");
  };
  const delRun = async (id: number) => { await api(`/api/stress/runs/${id}`, { method: "DELETE" }); loadHist(); };

  return (
    <Modal title={`Stress test · ${gw.name}`} onClose={() => { if (view !== "running") onClose(); else if (confirm("The test keeps running in the background. Close this window?")) onClose(); }} wide>
      {view === "config" && (
        <div className="space-y-5">
          <p className="text-sm text-[#8a92a6]">Ramps up the number of simultaneous streaming requests in stages and measures how much the gateway and its upstream servers can deliver: peak throughput, per-request speed, time to first token and queueing. Requests go through the real routing pipeline; the response cache and compression are bypassed and the traffic is not written to the request log.</p>
          <div className="grid gap-4 md:grid-cols-3">
            <div><label className="lbl">Model</label><select className="input" value={model} onChange={(e) => setModel(e.target.value)}>{models.map((m) => <option key={m}>{m}</option>)}</select></div>
            <div><label className="lbl">Scenario</label><select className="input" value={scenario} onChange={(e) => setScenario(e.target.value as any)}>
              <option value="prose">Prose — writing &amp; chat</option><option value="code">Code — generation &amp; refactoring</option></select>
              <p className="mt-1 text-xs text-[#6b7196]">{scenario === "code" ? "Source code as context, asks for a complete new module (temperature 0.2)." : "Plain text as context, asks for a long written analysis (temperature 0.7)."}</p></div>
            <div><label className="lbl">Profile</label><select className="input" value={preset} onChange={(e) => pickPreset(e.target.value)}>{Object.entries(PRESETS).map(([k, p]) => <option key={k} value={k}>{p.label} — {p.note}</option>)}<option value="custom">Custom</option></select></div>
            <div><label className="lbl">Concurrency levels</label><input className="input mono" value={levels} onChange={(e) => { setLevels(e.target.value); setPreset("custom"); }} /></div>
            <div><label className="lbl">Time per level (seconds)</label><input type="number" min={5} max={120} className="input" value={stage} onChange={(e) => { setStage(+e.target.value); setPreset("custom"); }} /></div>
            <div><label className="lbl">Prompt size</label><select className="input" value={promptTokens} onChange={(e) => setPromptTokens(+e.target.value)}>{PROMPTS.map(([l, v]) => <option key={v} value={v}>{l}</option>)}</select></div>
            <div><label className="lbl">Max output tokens</label><input type="number" min={8} max={4096} className="input" value={maxTokens} onChange={(e) => setMaxTokens(+e.target.value)} /></div>
          </div>
          <div className="rounded-md border border-[#d1a553]/30 bg-[#d1a553]/10 p-3 text-sm text-[#e6c27a]">
            <b>This sends real load to your upstream servers</b> — up to {Math.max(0, ...parsedLevels)} simultaneous long requests for about {dur(est)}. It uses compute, can slow other users of those servers and may briefly mark an overloaded endpoint as failing. The test stops early if requests start failing, and you can stop it at any time.
          </div>
          {hosted.length > 0 && (
            <div className="rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">
              <b>This gateway uses a paid hosted service ({hosted.join(", ")}).</b> The test sends many long requests: on OpenCode Zen they are billed per token, on OpenCode Go they use up your 5-hour, weekly and monthly allowance. Prefer a short profile and a small prompt.
            </div>
          )}
          <label className="flex cursor-pointer items-center gap-3 text-sm"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I understand and want to run this test on “{gw.name}”</label>
          {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
          <div className="flex items-center justify-between">
            <span className="text-xs text-[#6b7196]">Estimated duration {dur(est)} · {parsedLevels.length} level{parsedLevels.length !== 1 ? "s" : ""}</span>
            <button className="btn primary" disabled={!ack || busy || !parsedLevels.length || !model} onClick={start}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Start stress test</button>
          </div>
          {hist.length > 0 && (
            <div className="border-t border-white/[0.08] pt-4"><h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><History size={14} /> Previous runs</h4>
              <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">{["When", "Model", "Levels", "Peak", "Single", "Comfortable", ""].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr></thead>
                <tbody>{hist.map((h) => (
                  <tr key={h.id} className="cursor-pointer border-t border-white/5 hover:bg-white/[0.03]" onClick={() => openRun(h.id)}>
                    <td className="py-2 pr-3">{new Date(h.started_at * 1000).toLocaleString()}</td><td className="pr-3"><span className="mono">{h.model}</span> <span className="chip text-[#8a92a6]" style={{ background: "#ffffff0d" }}>{h.config?.scenario || "prose"}</span></td><td className="pr-3 text-xs text-[#8a92a6]">{(h.config?.levels || []).join(", ")}</td>
                    <td className="pr-3">{h.summary?.peak ? `${n1(h.summary.peak.agg_tps)} tok/s @ ${h.summary.peak.concurrency}` : <span className="text-[#6b7196]">{h.status}</span>}</td>
                    <td className="pr-3">{n1(h.summary?.single_stream_tps)}</td><td className="pr-3">{h.summary?.recommended_concurrency ?? "–"}</td>
                    <td className="text-right"><button className="btn danger !p-1.5" onClick={(e) => { e.stopPropagation(); delRun(h.id); }}><Trash2 size={13} /></button></td></tr>))}</tbody></table></div>)}
        </div>
      )}

      {view === "running" && (
        <div className="space-y-4">
          {!snap ? <div className="flex items-center gap-2 text-sm text-[#8a92a6]"><Loader2 size={15} className="animate-spin" /> Starting…</div> : (<>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm text-[#b8bddb]">Testing <b>{snap.gateway}</b> / <span className="mono">{snap.model}</span> · {snap.config?.scenario || "prose"} scenario · {dur(snap.elapsed)} elapsed</div>
              <button className="btn danger" onClick={stop}><Square size={14} /> Stop test</button></div>
            <div className="h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full rounded-full transition-all" style={{ width: `${((snap.stage_index + (snap.current ? Math.min(1, snap.current.elapsed / snap.current.stage_seconds) : 0)) / snap.stage_count) * 100}%`, background: "#3b6fd8" }} /></div>
            {snap.current ? (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <Tile label="Stage" value={`${snap.stage_index + 1} / ${snap.stage_count}`} sub={`${Math.round(snap.current.elapsed)}s of ${snap.current.stage_seconds}s`} />
                <Tile label="Concurrency now" value={snap.current.concurrency} sub={`${snap.current.inflight} in flight`} />
                <Tile label="Tokens / s right now" value={n1(snap.current.tps_now)} sub="across all requests (≈ streamed chunks)" tone="#7fd1a3" />
                <Tile label="Completed" value={fmt(snap.current.completed)} sub="in this stage" /><Tile label="Errors" value={fmt(snap.current.errors)} tone={snap.current.errors ? "#d9776f" : undefined} sub="in this stage" /></div>
            ) : <p className="text-sm text-[#8a92a6]">Finishing…</p>}
            <FlowGraph gw={gw} />
            <Charts stages={snap.stages} />
            {snap.stages.length > 0 && <StageTable stages={snap.stages} summary={null} />}
            <p className="text-xs text-[#6b7196]">You can leave this window open or close it; the test keeps running on the server. The same flow is also on the gateway's Live page.</p></>)}
        </div>
      )}

      {view === "result" && snap && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-[#8a92a6]">{snap.model && <span className="mono">{snap.model}</span>} · <b>{snap.config?.scenario || "prose"}</b> · {snap.started ? new Date(snap.started * 1000).toLocaleString() : ""} · levels {(snap.config?.levels || []).join(", ")} · {snap.config?.stage_seconds}s each · prompt ~{snap.config?.prompt_tokens} tokens · up to {snap.config?.max_tokens} output tokens</div>
            <div className="flex gap-2"><button className="btn" onClick={() => setView("config")}><RotateCcw size={14} /> New test</button></div></div>
          <Result snap={snap} />
        </div>
      )}
    </Modal>
  );
}
