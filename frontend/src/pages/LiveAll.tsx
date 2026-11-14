import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Layers, Network } from "lucide-react";
import { api, gwUrl, unifiedUrl } from "../api";
import { useLive } from "../live";
import FlowGraph from "../components/FlowGraph";
import { fmt, ms, PageHeader } from "../components/ui";

const RANGES: [string, number][] = [["15 min", 15], ["1 h", 60], ["24 h", 1440]];
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

/** A tiny request-count bar chart. */
function Spark({ data }: { data: number[] }) {
  const max = Math.max(1, ...data), w = 120, h = 28, bw = w / data.length;
  return (
    <svg width={w} height={h} className="shrink-0" aria-hidden>
      {data.map((v, i) => <rect key={i} x={i * bw + 0.5} y={h - Math.max(v ? 2 : 1, (v / max) * h)} width={Math.max(1, bw - 1.5)} height={Math.max(v ? 2 : 1, (v / max) * h)} rx={1} fill={v ? "#3987e5" : "rgba(255,255,255,0.08)"} />)}
    </svg>
  );
}

/** Live view of every gateway (and the unified endpoint), the most used first. */
export default function LiveAll() {
  const [minutes, setMinutes] = useState(15);
  const [gws, setGws] = useState<any[]>([]);
  const [uni, setUni] = useState<any>(null);
  const [stats, setStats] = useState<Record<string, any>>({});
  const { snapshot } = useLive();

  useEffect(() => {
    const load = () => { api("/api/gateways?all=true").then(setGws); api("/api/unified").then(setUni).catch(() => setUni(null)); };
    load(); const t = setInterval(load, 15000); return () => clearInterval(t);
  }, []);
  useEffect(() => {
    const load = () => api(`/api/stats/by-gateway?minutes=${minutes}`).then((d) => setStats(d.gateways || {})).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, [minutes]);

  const items = useMemo(() => {
    const internal = gws.find((g) => g.slug === "__unified__");
    const list: any[] = gws.filter((g) => g.slug !== "__unified__").map((g) => ({
      key: `g${g.id}`, name: g.name, to: `/gateways/${g.id}`, url: gwUrl(g.slug), unified: false, ids: [g.id], flow: g,
    }));
    if (uni?.enabled) {
      const exposed = (uni.gateways || []).filter((r: any) => r.exposed);
      const picked = gws.filter((g) => g.slug === "__unified__" ? (uni.models || []).length > 0 : exposed.some((r: any) => r.id === g.id));
      list.push({
        key: "unified", name: "Unified endpoint", to: "/gateways/unified", url: unifiedUrl(), unified: true, ids: picked.map((g) => g.id),
        flow: {
          id: "unified", slug: "unified", name: "Unified endpoint", strategy: "least_busy", gwIds: picked.map((g) => g.id),
          models: picked.flatMap((g) => g.models.map((m: any) => ({ ...m, gw_id: g.id, label: g.id === internal?.id ? m.public_name : `${g.name}: ${m.public_name}`, strategy: m.strategy || g.strategy }))),
        },
      });
    }
    return list.map((it) => {
      const parts = it.ids.map((i: number) => stats[String(i)]).filter(Boolean);
      const requests = sum(parts.map((p: any) => p.requests));
      const w = (k: string) => (requests ? sum(parts.map((p: any) => p[k] * p.requests)) / requests : 0);   // request-weighted
      return {
        ...it, requests, errors: sum(parts.map((p: any) => p.errors)), tps: sum(parts.map((p: any) => p.tokens_per_s)),
        p50: w("p50_ms"), ttft: w("ttft_p50_ms"),
        timeline: parts.length ? parts[0].timeline.map((_: number, i: number) => sum(parts.map((p: any) => p.timeline[i]))) : [],
        queue: sum(it.ids.map((i: number) => snapshot?.queue?.[i] || 0)),
      };
    }).sort((a, b) => b.requests - a.requests || a.name.localeCompare(b.name));
  }, [gws, uni, stats, snapshot]);

  return (
    <div className="fadein">
      <PageHeader title="Live" sub="Traffic of every gateway as it happens, the most used first."
        actions={
          <div className="inline-flex rounded-lg border border-white/10 bg-black/20 p-0.5 text-xs">
            {RANGES.map(([l, m]) => <button key={m} onClick={() => setMinutes(m)} className={`rounded-md px-2.5 py-1 transition-colors ${minutes === m ? "bg-white/10 text-[#e6e8f5]" : "text-[#8b91b5] hover:text-[#c9cfdd]"}`}>{l}</button>)}
          </div>} />
      {items.length === 0 && <div className="card card-pad text-sm text-[#8b91b5]">No gateways yet.</div>}
      <div className="grid gap-5 2xl:grid-cols-2">
        {items.map((it, rank) => (
          <div key={it.key} className={`card card-pad ${it.requests === 0 ? "opacity-75" : ""}`}>
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="mono text-xs text-[#6b7196]">#{rank + 1}</span>
                  <Link to={it.to} className="flex items-center gap-2 truncate text-base font-semibold hover:text-[#9fb0ff]">{it.unified ? <Layers size={16} /> : <Network size={16} />}{it.name}</Link>
                  {it.unified && <span className="chip" style={{ color: "#8a8fc4", background: "#8a8fc422" }}>Unified</span>}
                </div>
                <div className="mono mt-1 truncate text-xs text-[#6b7196]">{it.url}</div>
              </div>
              <div className="flex items-end gap-4">
                <Spark data={it.timeline} />
                <div className="text-right"><div className="text-2xl font-semibold leading-none">{fmt(it.requests)}</div><div className="mt-1 text-[11px] text-[#8b91b5]">requests · {RANGES.find(([, m]) => m === minutes)?.[0]}</div></div>
              </div>
            </div>
            <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
              {[["Success", it.requests ? ((1 - it.errors / it.requests) * 100).toFixed(1) + "%" : "–"], ["Latency p50", it.requests ? ms(it.p50) : "–"], ["TTFT p50", it.requests ? ms(it.ttft) : "–"], ["Tokens/s", it.requests ? it.tps.toFixed(1) : "–"], ["Queue", String(it.queue)]].map(([k, v]) => (
                <div key={k} className="rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2"><div className="text-[11px] text-[#8b91b5]">{k}</div><div className="mt-0.5 text-base font-semibold">{v}</div></div>))}
            </div>
            {it.flow.models.length > 0 ? <FlowGraph gw={it.flow} /> : <div className="rounded-lg border border-dashed border-white/10 p-6 text-center text-sm text-[#6b7196]">No models configured.</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
