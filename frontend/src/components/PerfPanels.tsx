import { Link } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Database, Gauge } from "lucide-react";
import { PALETTE } from "../api";
import { fmt } from "./ui";

const tip = { contentStyle: { background: "#26262b", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }, cursor: { stroke: "rgba(255,255,255,0.15)" } };
const tps = (v?: number | null) => (v == null ? "–" : v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1));
const msf = (v?: number | null) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
const pct = (v?: number | null) => (v == null ? "–" : `${Math.round(v * 100)}%`);

function Cell({ s, fmtFn = tps, unit = "" }: { s?: any; fmtFn?: (v?: number | null) => string; unit?: string }) {
  if (!s) return <span className="text-[#5d5d68]" title="Needs streaming requests that produced enough tokens in this period">–</span>;
  return <span title={`avg ${fmtFn(s.avg)} · p95 ${fmtFn(s.p95)} · min ${fmtFn(s.min)} · max ${fmtFn(s.max)} · ${s.n} requests measured`}>
    <b className="font-semibold">{fmtFn(s.p50)}</b>{unit && <span className="text-xs text-[#787884]"> {unit}</span>} <span className="text-xs text-[#787884]">avg {fmtFn(s.avg)}</span></span>;
}

export function CachePanel({ perf }: { perf: any }) {
  const c = perf.totals.cache, live = perf.cache_live;
  const state = live?.backend === "redis" ? (live.state === "error" ? ["Redis unreachable → memory", "#d9776f"] : ["Redis", "#5fb585"]) : ["In-memory", "#8e8e9a"];
  return (
    <div className="card card-pad">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-base font-semibold"><Database size={16} /> Response cache</h3>
        <Link to="/settings?tab=general" className="chip" style={{ color: state[1], background: state[1] + "22" }} title="Cache backend (Settings)">{state[0]}</Link>
      </div>
      {c.eligible === 0 && c.skipped === 0 && c.bypassed === 0 ? (
        <p className="text-sm text-[#787884]">No requests used the cache in this period. Enable caching per model in a gateway's settings, then send identical requests (temperature 0 by default).</p>
      ) : (<>
        <div className="grid grid-cols-2 gap-3">
          {[["Hit rate", pct(c.hit_rate), `${fmt(c.tracked_hits)} of ${fmt(c.eligible)} cacheable requests`],
            ["Tokens saved", fmt(c.tokens_saved), "served without calling a model"],
            ["Hits", fmt(c.hits), c.stream_hits ? `${fmt(c.stream_hits)} replayed as streams` : "all non-streaming"],
            ["Misses", fmt(c.misses), "answered by a model, then stored"]].map(([k, v, sub]) => (
            <div key={k} className="rounded-lg border border-white/5 bg-white/[0.02] p-3"><div className="text-xs text-[#8e8e9a]">{k}</div><div className="mt-1 text-xl font-semibold">{v}</div><div className="mt-0.5 text-[11px] text-[#787884]">{sub}</div></div>))}
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/5" title="Hit rate"><div className="h-full rounded-full" style={{ width: `${(c.hit_rate || 0) * 100}%`, background: "#5fb585" }} /></div>
        {(c.skipped > 0 || c.bypassed > 0) && <p className="mt-3 text-xs text-[#787884]">{fmt(c.skipped)} requests were not cacheable (non-deterministic) · {fmt(c.bypassed)} bypassed the cache by header</p>}
      </>)}
      {live && <p className="mt-3 border-t border-white/5 pt-3 text-xs text-[#787884]">Right now: {fmt(live.entries ?? 0)} cached entries · {fmt(live.hits)} hits / {fmt(live.misses)} misses since start · default TTL {live.default_ttl}s{live.state === "error" && live.last_error ? <span className="text-[#d9776f]"> · {live.last_error}</span> : null}</p>}
    </div>
  );
}

export function PerformancePanel({ perf }: { perf: any }) {
  const gws = perf.gateways as any[];
  return (
    <div className="card card-pad">
      <div className="mb-1 flex items-center gap-2"><Gauge size={16} /><h3 className="text-base font-semibold">Speed by gateway</h3></div>
      <p className="mb-4 text-xs text-[#787884]">Measured on streaming requests answered by a model (not from cache). Median shown, hover for avg / p95 / min / max.</p>
      {gws.length === 0 ? <p className="text-sm text-[#787884]">No requests in this period.</p> : (
        <div className="overflow-x-auto scroll-thin"><table className="w-full text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wider text-[#787884]">
            <th className="pb-2 font-medium">Gateway</th><th className="pb-2 font-medium">Requests</th>
            <th className="pb-2 font-medium" title="Completion tokens per second after the first token">Generation</th>
            <th className="pb-2 font-medium" title="Prompt tokens per second of time-to-first-token">Prompt processing</th>
            <th className="pb-2 font-medium">First token</th><th className="pb-2 font-medium">Cache hit rate</th></tr></thead>
          <tbody>{gws.map((g) => (
            <tr key={g.gateway_id} className="border-t border-white/5">
              <td className="py-3 pr-3 font-medium">{g.name}</td>
              <td className="pr-3">{fmt(g.requests)}{g.errors > 0 && <span className="text-xs text-[#d9776f]"> · {g.errors} err</span>}<div className="text-[11px] text-[#787884]">{fmt(g.streamed)} streamed</div></td>
              <td className="pr-3"><Cell s={g.gen_tps} unit="tok/s" /></td>
              <td className="pr-3"><Cell s={g.prompt_tps} unit="tok/s" /></td>
              <td className="pr-3"><Cell s={g.ttft_ms} fmtFn={msf} /></td>
              <td>{g.cache.eligible ? <span title={`${g.cache.tracked_hits} hits / ${g.cache.misses} misses`}><b className="font-semibold">{pct(g.cache.hit_rate)}</b> <span className="text-xs text-[#787884]">{fmt(g.cache.tracked_hits)}/{fmt(g.cache.eligible)}</span></span> : <span className="text-[#5d5d68]" title="No cacheable requests">–</span>}</td>
            </tr>))}</tbody></table></div>)}
    </div>
  );
}

export function SpeedChart({ perf }: { perf: any }) {
  const { starts, series } = perf.timeline;
  const data = starts.map((t: number, i: number) => {
    const d = new Date(t * 1000);
    return { label: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), ...Object.fromEntries(series.map((s: any) => [s.name, s.points[i]])) };
  });
  const has = series.some((s: any) => s.points.some((p: any) => p != null));
  return (
    <div className="card card-pad">
      <h3 className="mb-3 text-base font-semibold">Generation speed over time</h3>
      {!has ? <p className="text-sm text-[#787884]">No measurable streaming requests in this period.</p> : (
        <div className="h-[240px]"><ResponsiveContainer><LineChart data={data}>
          <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "#787884", fontSize: 11 }} axisLine={false} tickLine={false} interval={Math.floor(data.length / 6)} />
          <YAxis tick={{ fill: "#787884", fontSize: 11 }} axisLine={false} tickLine={false} width={40} unit=" t/s" />
          <Tooltip {...tip} formatter={(v: any) => `${tps(v)} tok/s`} /><Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s: any, i: number) => <Line key={s.gateway_id} type="monotone" dataKey={s.name} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2} dot={false} connectNulls />)}
        </LineChart></ResponsiveContainer></div>)}
    </div>
  );
}

export function ModelSpeedTable({ perf }: { perf: any }) {
  const ms = perf.models as any[];
  return (
    <div className="card card-pad">
      <h3 className="mb-1 text-base font-semibold">Speed by upstream model</h3>
      <p className="mb-3 text-xs text-[#787884]">The same measurements per backing model, fastest first, so you can compare servers behind a gateway.</p>
      {ms.length === 0 ? <p className="text-sm text-[#787884]">No measurable streaming requests in this period.</p> : (
        <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wider text-[#787884]"><th className="pb-2 font-medium">Model</th><th className="pb-2 font-medium">Requests</th><th className="pb-2 font-medium">Generation</th><th className="pb-2 font-medium">Prompt processing</th></tr></thead>
          <tbody>{ms.map((m) => (
            <tr key={m.model_id} className="border-t border-white/5"><td className="py-2.5 pr-3"><span className="mono">{m.upstream}</span> <span className="text-xs text-[#787884]">@ {m.endpoint}</span></td><td className="pr-3">{fmt(m.requests)}</td>
              <td className="pr-3"><Cell s={m.gen_tps} unit="tok/s" /></td><td><Cell s={m.prompt_tps} unit="tok/s" /></td></tr>))}</tbody></table>)}
    </div>
  );
}
