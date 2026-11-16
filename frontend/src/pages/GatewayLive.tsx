import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Copy, FlaskConical, Pencil } from "lucide-react";
import { api, gwUrl } from "../api";
import { useLive } from "../live";
import FlowGraph from "../components/FlowGraph";
import LiveCharts from "../components/LiveCharts";
import TestGateway from "../components/TestGateway";
import { ms, PageHeader, fmt } from "../components/ui";

export default function GatewayLive() {
  const { id } = useParams();
  const [gw, setGw] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [tail, setTail] = useState<any[]>([]);
  const { snapshot } = useLive();

  useEffect(() => { api(`/api/gateways/${id}`).then(setGw); }, [id]);
  useEffect(() => {
    const load = () => api(`/api/stats?minutes=15&gateway_id=${id}`).then(setStats);
    load(); const t = setInterval(load, 3000); return () => clearInterval(t);
  }, [id]);

  if (!gw) return null;
  const qd = snapshot?.queue?.[gw.id] || 0;
  const tps = stats ? (stats.completion_tokens / (15 * 60)).toFixed(1) : "–";
  const onEvent = (ev: any) => { if (ev.type !== "first_token") setTail((t) => [{ ...ev, key: Math.random() }, ...t].slice(0, 14)); };

  return (
    <div className="fadein">
      <PageHeader title={gw.name} sub={`${gwUrl(gw.slug)}  ·  live traffic flow`}
        actions={<><button className="btn primary" onClick={() => setTesting(true)}><FlaskConical size={15} /> Test</button><button className="btn" onClick={() => navigator.clipboard?.writeText(gwUrl(gw.slug))}><Copy size={15} /> Copy URL</button><Link to={`/gateways/${gw.id}/edit`} className="btn"><Pencil size={15} /> Edit</Link></>} />
      <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
        <FlowGraph gw={gw} onEvent={onEvent} />

        <div className="space-y-5">
          <div className="card card-pad">
            <h3 className="mb-3 text-sm font-semibold">Last 15 minutes</h3>
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
            <pre className="mono scroll-thin overflow-x-auto rounded-md bg-black/40 p-3 text-[11px] leading-relaxed text-[#9fb0ff]">{`curl ${gwUrl(gw.slug)}/chat/completions \\
${gw.has_keys ? `  -H "Authorization: Bearer <your-api-key>" \\\n` : ""}  -H "Content-Type: application/json" \\
  -d '{"model":"${gw.models[0]?.public_name || "model"}",
       "messages":[{"role":"user","content":"Hi"}]}'

# Anthropic API too (Claude Code / Anthropic SDK)
export ANTHROPIC_BASE_URL=${location.origin}/gw/${gw.slug}
export ANTHROPIC_AUTH_TOKEN=${gw.has_keys ? "<your-api-key>" : "any-value"}
export ANTHROPIC_MODEL=${gw.models[0]?.public_name || "model"}

# Codex CLI (~/.codex/config.toml) - uses the Responses API
model = "${gw.models[0]?.public_name || "model"}"
model_provider = "smartgateway"
[model_providers.smartgateway]
name = "SmartGateway"
base_url = "${gwUrl(gw.slug)}"
env_key = "SMARTGATEWAY_API_KEY"   # ${gw.has_keys ? "export it with your API key" : "any non-empty value"}
wire_api = "responses"`}</pre>
          </div>
        </div>
      </div>
      <div className="mt-5"><LiveCharts ids={[gw.id]} modelOrder={gw.models.flatMap((m: any) => m.members.map((x: any) => x.model_id))} /></div>
      {testing && <TestGateway gw={gw} onClose={() => setTesting(false)} />}
    </div>
  );
}
