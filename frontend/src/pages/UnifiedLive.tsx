import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, FlaskConical, Pencil } from "lucide-react";
import { api, unifiedUrl } from "../api";
import { useLive } from "../live";
import FlowGraph from "../components/FlowGraph";
import LiveCharts from "../components/LiveCharts";
import TestGateway from "../components/TestGateway";
import { ms, PageHeader, fmt } from "../components/ui";

/** Live view of the unified endpoint: the flow, stats and request tail of every gateway it exposes. */
export default function UnifiedLive() {
  const [uni, setUni] = useState<any>(null);
  const [gws, setGws] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [tail, setTail] = useState<any[]>([]);
  const { snapshot } = useLive();

  useEffect(() => { api("/api/unified").then(setUni); api("/api/gateways?all=true").then(setGws); }, []);
  const exposed: any[] = useMemo(() => uni?.gateways?.filter((r: any) => r.exposed) || [], [uni]);
  const internalId = gws.find((g) => g.slug === "__unified__")?.id;
  const ids = [...exposed.map((r) => r.id), ...((uni?.models || []).length && internalId ? [internalId] : [])].join(",");
  useEffect(() => {
    if (!uni) return;
    const load = () => api(`/api/stats?minutes=15&gateway_ids=${ids}`).then(setStats);
    load(); const t = setInterval(load, 3000); return () => clearInterval(t);
  }, [uni, ids]);

  // a gateway-shaped object for the flow graph: the models of all exposed gateways, tagged with their gateway
  const flow = useMemo(() => {
    // the exposed gateways, plus the hidden internal one that holds the directly exposed models
    const picked = gws.filter((g) => g.slug === "__unified__" ? (uni?.models || []).length > 0 : exposed.some((r) => r.id === g.id));
    return {
      id: "unified", slug: "unified", name: "Unified endpoint", strategy: "least_busy", gwIds: picked.map((g) => g.id),
      models: picked.flatMap((g) => g.models.map((m: any) => ({ ...m, gw_id: g.id, label: g.slug === "__unified__" ? m.public_name : picked.length > 1 ? `${g.name}: ${m.public_name}` : m.public_name, strategy: m.strategy || g.strategy }))),
    };
  }, [gws, exposed, uni]);
  const testGw = { unified: true, name: "Unified endpoint", models: (uni?.model_names || []).map((n: string) => ({ id: n, public_name: n, capabilities: [], members: [] })) };

  if (!uni) return null;
  const qd = flow.gwIds.reduce((a: number, i: number) => a + (snapshot?.queue?.[i] || 0), 0);
  const tps = stats ? (stats.completion_tokens / (15 * 60)).toFixed(1) : "–";
  const onEvent = (ev: any) => { if (ev.type !== "first_token") setTail((t) => [{ ...ev, key: Math.random() }, ...t].slice(0, 14)); };
  const url = unifiedUrl();

  return (
    <div className="fadein">
      <PageHeader title="Unified endpoint" sub={`${url}  ·  live traffic of the exposed gateways`}
        actions={<><button className="btn primary" onClick={() => setTesting(true)} disabled={testGw.models.length === 0}><FlaskConical size={15} /> Test</button><button className="btn" onClick={() => navigator.clipboard?.writeText(url)}><Copy size={15} /> Copy URL</button><Link to="/settings" className="btn"><Pencil size={15} /> Edit</Link></>} />
      {!uni.enabled && <div className="mb-5 rounded-md border border-white/10 p-3 text-sm text-[#8b91b5]">The unified endpoint is disabled. Enable it in <Link to="/settings" className="text-[#7d9be0] hover:underline">Settings</Link>.</div>}
      {exposed.length === 0 && !(uni.models || []).length ? (
        <div className="card card-pad text-sm text-[#8b91b5]">Nothing exposed yet. Choose gateways or models in <Link to="/settings" className="text-[#7d9be0] hover:underline">Settings</Link>.</div>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
          <FlowGraph gw={flow} onEvent={onEvent} />
          <div className="space-y-5">
            <div className="card card-pad">
              <h3 className="mb-1 text-sm font-semibold">Last 15 minutes</h3>
              <p className="mb-3 text-xs text-[#6b7196]">Includes calls made directly to these gateways.</p>
              <div className="grid grid-cols-2 gap-3">
                {[["Requests", stats ? fmt(stats.total) : "–"], ["Success", stats ? (stats.success_rate * 100).toFixed(1) + "%" : "–"], ["Latency p50", stats ? ms(stats.p50_ms) : "–"], ["TTFT p50", stats ? ms(stats.ttft_p50_ms) : "–"], ["Tokens/s", tps], ["Queue", String(qd)]].map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-white/5 bg-white/[0.02] p-3"><div className="text-xs text-[#8b91b5]">{k}</div><div className="mt-1 text-xl font-semibold">{v}</div></div>))}
              </div>
            </div>
            <div className="card card-pad">
              <h3 className="mb-3 text-sm font-semibold">Live requests</h3>
              <div className="space-y-1.5 text-xs">
                {tail.length === 0 && <div className="text-[#6b7196]">Waiting for traffic…</div>}
                {tail.map((e) => (
                  <div key={e.key} className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.02] px-2.5 py-1.5">
                    <span className="mono truncate">{e.type === "start" ? `→ ${e.upstream}` : e.type === "queued" ? "queued" : e.type === "retry" ? "↻ retry" : e.ok ? `✓ ${ms(e.total_ms)}` : `✗ ${e.status}`}</span>
                    <span className="shrink-0 text-[#6b7196]">{e.public_model}{e.requirements ? ` [${e.requirements}]` : ""}</span>
                  </div>))}
              </div>
            </div>
            <div className="card card-pad">
              <h3 className="mb-2 text-sm font-semibold">Use it</h3>
              <pre className="mono scroll-thin overflow-x-auto rounded-md bg-black/40 p-3 text-[11px] leading-relaxed text-[#9fb0ff]">{`curl ${url}/chat/completions \\
  -H "Authorization: Bearer <${uni.keys.length ? "unified API key" : "gateway API key, if the gateway has one"}>" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${uni.model_names[0] || "model"}",
       "messages":[{"role":"user","content":"Hi"}]}'`}</pre>
            </div>
          </div>
        </div>
      )}
      {flow.gwIds.length > 0 && <div className="mt-5"><LiveCharts ids={flow.gwIds} modelOrder={flow.models.flatMap((m: any) => m.members.map((x: any) => x.model_id))} /></div>}
      {testing && <TestGateway gw={testGw} onClose={() => setTesting(false)} />}
    </div>
  );
}
