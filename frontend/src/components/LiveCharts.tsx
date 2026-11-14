import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, Zap } from "lucide-react";
import { api } from "../api";
import { fmt } from "./ui";

// categorical slots 1-5 for the dark surface, assigned by model (never by rank), validated as a set
const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];
const ERR = "#e66767";
const INK2 = "#8b91b5", GRID = "rgba(255,255,255,0.06)";
const RANGES: [string, number][] = [["15m", 15], ["1h", 60], ["6h", 360]];

const num = (v?: number | null, d = 1) => (v == null ? "–" : v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(d));
const clock = (t: number, long: boolean) => new Date(t * 1000).toLocaleTimeString([], long ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : { hour: "2-digit", minute: "2-digit" });

function Seg<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <div className="inline-flex rounded-lg border border-white/10 bg-black/20 p-0.5 text-xs">
      {options.map(([v, l]) => (
        <button key={v} onClick={() => onChange(v)} className={`rounded-md px-2.5 py-1 transition-colors ${value === v ? "bg-white/10 text-[#e6e8f5]" : "text-[#8b91b5] hover:text-[#c9cfdd]"}`}>{l}</button>))}
    </div>
  );
}

function Tip({ active, payload, label, rows, unit }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-white/10 bg-[#181c25] px-3 py-2 text-xs shadow-xl">
      <div className="mb-1.5 text-[#8b91b5]">{clock(label, true)}</div>
      {rows(payload).map((r: any) => (
        <div key={r.name} className="flex items-center justify-between gap-5 py-0.5">
          <span className="flex items-center gap-1.5 text-[#c9cfdd]"><i className="inline-block h-2 w-2 rounded-full" style={{ background: r.color }} />{r.name}</span>
          <span className="mono font-medium text-[#e6e8f5]">{r.value}{unit ? <span className="text-[#6b7196]"> {unit}</span> : null}</span>
        </div>))}
    </div>
  );
}

/** Usage and per-model speed over time for one gateway (ids = [id]) or a set of gateways (unified endpoint). */
export default function LiveCharts({ ids, modelOrder = [] }: { ids: number[]; modelOrder?: number[] }) {
  const [range, setRange] = useState(30);
  const [usageMetric, setUsageMetric] = useState<"requests" | "in" | "out">("requests");
  const [speedMetric, setSpeedMetric] = useState<"gen" | "prompt">("gen");
  const [d, setD] = useState<any>(null);
  const key = ids.join(",");

  useEffect(() => {
    if (!key) return;
    const load = () => api(`/api/insights/live-series?minutes=${range}&buckets=30&gateway_ids=${key}`).then(setD).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, [key, range]);

  const step = d?.bucket_seconds || 1;
  const long = range <= 60;
  const colorOf = (mid: number) => { const i = modelOrder.indexOf(mid); return SERIES[(i >= 0 ? i : modelOrder.length + (d?.models || []).findIndex((m: any) => m.model_id === mid)) % SERIES.length]; };

  const usage = useMemo(() => d ? d.starts.map((t: number, i: number) => ({
    t, ok: d.usage.requests[i] - d.usage.errors[i], err: d.usage.errors[i], cached: d.usage.cached[i],
    in: d.usage.prompt_tokens[i] / step, out: d.usage.completion_tokens[i] / step,
  })) : [], [d, step]);
  const speed = useMemo(() => d ? d.starts.map((t: number, i: number) => {
    const row: any = { t };
    d.models.forEach((m: any) => { row[`m${m.model_id}`] = m[speedMetric][i]; });
    return row;
  }) : [], [d, speedMetric]);

  const totals = d ? { req: d.usage.requests.reduce((a: number, b: number) => a + b, 0), err: d.usage.errors.reduce((a: number, b: number) => a + b, 0),
    tin: d.usage.prompt_tokens.reduce((a: number, b: number) => a + b, 0), tout: d.usage.completion_tokens.reduce((a: number, b: number) => a + b, 0) } : null;
  const head = usageMetric === "requests" ? [fmt(totals?.req ?? 0), `requests${totals?.err ? ` · ${totals.err} failed` : ""}`]
    : usageMetric === "in" ? [fmt(totals?.tin ?? 0), "prompt tokens"] : [fmt(totals?.tout ?? 0), "generated tokens"];
  const axis = { stroke: INK2, fontSize: 11, tickLine: false, axisLine: false } as const;
  const xAxis = <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(t) => clock(t, false)} {...axis} minTickGap={48} />;
  const statKey = speedMetric === "gen" ? "gen_stats" : "prompt_stats";
  const measured = (d?.models || []).filter((m: any) => m[speedMetric].some((v: any) => v != null));

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-xs text-[#8b91b5]"><i className="relative inline-flex h-2 w-2"><i className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#5fb585] opacity-60" /><i className="relative inline-flex h-2 w-2 rounded-full bg-[#5fb585]" /></i>Live · updates every few seconds</span>
        <Seg value={String(range)} onChange={(v) => setRange(+v)} options={RANGES.map(([l, m]) => [String(m), l] as [string, string])} />
      </div>

      <div className="card card-pad">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold"><Activity size={15} /> Usage</h3>
            <div className="mt-2 flex items-baseline gap-2"><span className="text-3xl font-semibold tracking-tight">{head[0]}</span><span className="text-xs text-[#8b91b5]">{head[1]} · last {range >= 60 ? `${range / 60} h` : `${range} min`}</span></div>
          </div>
          <Seg value={usageMetric} onChange={setUsageMetric} options={[["requests", "Requests"], ["in", "Prompt tokens"], ["out", "Generated tokens"]]} />
        </div>
        <div className="h-52">
          <ResponsiveContainer width="100%" height="100%">
            {usageMetric === "requests" ? (
              <BarChart data={usage} margin={{ top: 4, right: 4, left: -18, bottom: 0 }} barCategoryGap="22%">
                <CartesianGrid stroke={GRID} vertical={false} />{xAxis}<YAxis allowDecimals={false} {...axis} />
                <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} content={<Tip rows={(p: any[]) => [{ name: "Succeeded", color: SERIES[0], value: p[0].payload.ok }, ...(p[0].payload.err ? [{ name: "Failed", color: ERR, value: p[0].payload.err }] : []), ...(p[0].payload.cached ? [{ name: "of which cached", color: INK2, value: p[0].payload.cached }] : [])]} />} />
                <Bar dataKey="ok" stackId="r" fill={SERIES[0]} stroke="#12151c" strokeWidth={2} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                <Bar dataKey="err" stackId="r" fill={ERR} stroke="#12151c" strokeWidth={2} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              </BarChart>
            ) : (
              <AreaChart data={usage} margin={{ top: 4, right: 4, left: -8, bottom: 0 }}>
                <defs><linearGradient id="ug" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={usageMetric === "in" ? SERIES[2] : SERIES[0]} stopOpacity={0.35} /><stop offset="100%" stopColor={usageMetric === "in" ? SERIES[2] : SERIES[0]} stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid stroke={GRID} vertical={false} />{xAxis}<YAxis tickFormatter={(v) => num(v, 0)} {...axis} />
                <Tooltip cursor={{ stroke: "rgba(255,255,255,0.2)" }} content={<Tip unit="tok/s" rows={(p: any[]) => [{ name: usageMetric === "in" ? "Prompt tokens" : "Generated tokens", color: usageMetric === "in" ? SERIES[2] : SERIES[0], value: num(p[0].value) }]} />} />
                <Area type="monotone" dataKey={usageMetric} stroke={usageMetric === "in" ? SERIES[2] : SERIES[0]} strokeWidth={2} fill="url(#ug)" isAnimationActive={false} dot={false} activeDot={{ r: 4, stroke: "#12151c", strokeWidth: 2 }} />
              </AreaChart>
            )}
          </ResponsiveContainer>
        </div>
        {usageMetric !== "requests" && <p className="mt-1 text-[11px] text-[#6b7196]">Average tokens per second in each {Math.round(step)} s slice.</p>}
      </div>

      <div className="card card-pad">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold"><Zap size={15} /> Speed by model</h3>
            <p className="mt-1 text-xs text-[#6b7196]">{speedMetric === "gen" ? "Tokens generated per second after the first token." : "Prompt tokens processed per second until the first token."} Median per slice, streaming requests answered by an upstream.</p>
          </div>
          <Seg value={speedMetric} onChange={setSpeedMetric} options={[["gen", "Generation"], ["prompt", "Prompt processing"]]} />
        </div>
        {measured.length === 0 ? (
          <div className="flex h-52 items-center justify-center rounded-lg border border-dashed border-white/10 text-sm text-[#6b7196]">{d ? "No measurable streaming requests in this window yet." : "Loading…"}</div>
        ) : (<>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={speed} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />{xAxis}<YAxis tickFormatter={(v) => num(v, 0)} {...axis} />
                <Tooltip cursor={{ stroke: "rgba(255,255,255,0.2)" }} content={<Tip unit="tok/s" rows={(p: any[]) => p.filter((x) => x.value != null).map((x) => { const m = d.models.find((m: any) => `m${m.model_id}` === x.dataKey); return { name: `${m.upstream} @ ${m.endpoint}`, color: x.stroke, value: num(x.value) }; })} />} />
                {measured.map((m: any) => (
                  <Line key={m.model_id} type="monotone" dataKey={`m${m.model_id}`} stroke={colorOf(m.model_id)} strokeWidth={2} connectNulls isAnimationActive={false}
                    dot={{ r: 3, fill: colorOf(m.model_id), stroke: "#12151c", strokeWidth: 2 }} activeDot={{ r: 5, stroke: "#12151c", strokeWidth: 2 }} />))}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-4 divide-y divide-white/5 rounded-lg border border-white/5">
            {measured.map((m: any) => { const s = m[statKey]; return (
              <div key={m.model_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3.5 py-2.5 text-sm">
                <span className="flex min-w-[220px] flex-1 items-center gap-2"><i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: colorOf(m.model_id) }} /><span className="mono truncate">{m.upstream}</span><span className="text-xs text-[#6b7196]">@ {m.endpoint}</span></span>
                <span className="text-lg font-semibold">{num(s?.p50)}<span className="ml-1 text-xs font-normal text-[#6b7196]">tok/s median</span></span>
                <span className="text-xs text-[#8b91b5]">p95 {num(s?.p95)} · max {num(s?.max)} · {s?.n ?? 0} measured</span>
              </div>); })}
          </div>
        </>)}
      </div>
    </div>
  );
}
