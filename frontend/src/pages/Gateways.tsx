import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Copy, Pencil, Plus, Trash2, Activity, FlaskConical, Gauge, Layers, Lock, Power, Users } from "lucide-react";
import { api, gwUrl, unifiedUrl, STRATEGIES } from "../api";
import { CapChip, Empty, PageHeader } from "../components/ui";
import TestGateway from "../components/TestGateway";
import StressTest from "../components/StressTest";

export default function Gateways() {
  const [gws, setGws] = useState<any[]>([]);
  const [testing, setTesting] = useState<any>(null);
  const [stressing, setStressing] = useState<any>(null);
  const [uni, setUni] = useState<any>(null);
  const [testingUni, setTestingUni] = useState(false);
  const nav = useNavigate();
  const load = () => { api("/api/gateways").then(setGws); api("/api/unified").then(setUni).catch(() => setUni(null)); };
  useEffect(() => { load(); }, []);
  const [eps, setEps] = useState<any[]>([]);
  useEffect(() => { api("/api/endpoints").then(setEps).catch(() => {}); }, []);
  const gwName = (_g: any, r?: any) => {
    if (!r) return "?";
    if (r.model_id) { for (const e of eps) { const m = e.models.find((x: any) => x.id === r.model_id); if (m) return `${e.name}/${m.upstream_id}`; } return `model ${r.model_id}`; }
    return `${gws.find((x) => x.id === r.gateway_id)?.name || "?"}/${r.model}`;
  };
  const del = async (g: any) => {
    if (!confirm(`Delete gateway "${g.name}"?`)) return;
    try { await api(`/api/gateways/${g.id}`, { method: "DELETE" }); }
    catch (e: any) { if (!confirm(`${e.message}\n\nDelete it anyway? Those councils will fail until you pick another model.`)) return; await api(`/api/gateways/${g.id}?force=true`, { method: "DELETE" }); }
    load();
  };

  const disableUni = async () => {
    if (confirm("Disable the unified endpoint? Clients using /unified/v1 stop working.")) { await api("/api/unified", { method: "PUT", json: { enabled: false } }); load(); }
  };
  const uniExposed: any[] = uni?.gateways?.filter((r: any) => r.exposed) || [];
  const uniGw = { unified: true, name: "Unified endpoint", models: (uni?.model_names || []).map((n: string) => ({ id: n, public_name: n, capabilities: [], members: [] })) };

  return (
    <div className="fadein">
      <PageHeader title="Smart Gateways" sub="Each gateway is a single OpenAI-compatible endpoint that fans out to your upstream models."
        actions={<><button className="btn" onClick={() => nav("/councils/new")} title="Several models answer, review each other and a chairman writes the final answer"><Users size={16} /> New council</button><button className="btn primary" onClick={() => nav("/gateways/new")}><Plus size={16} /> New gateway</button></>} />
      {gws.length === 0 && <Empty>No gateways yet. Create one to combine endpoint models behind a single API.</Empty>}
      <div className="grid gap-5 xl:grid-cols-2">
        {gws.map((g) => (
          <div key={g.id} className="card card-pad">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Link to={g.kind === "council" ? `/gateways/${g.id}/edit` : `/gateways/${g.id}`} className="text-base font-semibold hover:text-[#9fb0ff]">{g.name}</Link>
                <div className="mt-1 text-xs text-[#8b91b5]">{g.kind === "council" ? <><span className="chip mr-1" style={{ color: "#b79cf0", background: "#b79cf01f" }}>Council</span>{g.council?.councillors?.length || 0} councillors{g.council?.review_enabled === false ? "" : " · peer review"} · chairman {gwName(g, g.council?.chairman)}</> : <>{STRATEGIES[g.strategy]} · queue {g.queue_timeout}s</>} · {g.has_keys ? "API key required" : "open access"}</div>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <button className="btn whitespace-nowrap" onClick={() => setTesting(g)}><FlaskConical size={15} /> Test</button>
                {g.kind !== "council" && <button className="btn whitespace-nowrap" onClick={() => setStressing(g)} title="Measure peak throughput by ramping up concurrent requests"><Gauge size={15} /> Stress test</button>}
                {g.kind !== "council" && <Link to={`/gateways/${g.id}`} className="btn"><Activity size={15} /> Live</Link>}
                <Link to={`/gateways/${g.id}/edit`} className="btn"><Pencil size={15} /></Link>
                <button className="btn danger" onClick={() => del(g)}><Trash2 size={15} /></button>
              </div>
            </div>
            <div className="mono mt-4 flex items-center justify-between gap-2 rounded-md border border-white/5 bg-black/30 px-3 py-2 text-xs text-[#9fb0ff]">
              <span className="truncate">{gwUrl(g.slug)}</span>
              <button onClick={() => navigator.clipboard?.writeText(gwUrl(g.slug))} title="Copy"><Copy size={14} /></button>
            </div>
            <div className="mt-4 space-y-2">
              {g.models.map((m: any) => (
                <div key={m.id} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="mono font-medium">{m.public_name}</span>
                    <div className="flex flex-wrap gap-1">{m.capabilities.map((c: string) => <CapChip key={c} cap={c} />)}</div>
                  </div>
                  {g.kind === "council"
                    ? <div className="mt-1 text-xs text-[#8b91b5]">{(g.council?.councillors || []).map((r: any) => gwName(g, r)).join(", ")}</div>
                    : <div className="mt-1 text-xs text-[#8b91b5]">{m.members.length} upstream: {m.members.map((x: any) => `${x.upstream_id}@${x.endpoint_name}`).join(", ")}</div>}
                </div>
              ))}
              {g.models.length === 0 && <div className="text-sm text-[#8b91b5]">No models exposed yet.</div>}
            </div>
          </div>
        ))}
      </div>
      {uni?.enabled && (
        <section className="mt-10">
          <div className="mb-4">
            <h2 className="text-lg font-semibold tracking-tight">Unified endpoint</h2>
            <p className="mt-1 text-sm text-[#8b91b5]">One OpenAI-compatible URL in front of several gateways; clients pick a gateway by model name.</p>
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
          <div className="card card-pad">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Link to="/gateways/unified" className="flex items-center gap-2 text-base font-semibold hover:text-[#9fb0ff]"><Layers size={16} /> Unified endpoint</Link>
                <div className="mt-1 text-xs text-[#8b91b5]">{uniExposed.length} gateway{uniExposed.length === 1 ? "" : "s"}{uni.models?.length ? ` · ${uni.models.length} model${uni.models.length === 1 ? "" : "s"}` : ""} exposed · {uni.keys.length ? "API key required" : "open access"}</div>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <button className="btn whitespace-nowrap" onClick={() => setTestingUni(true)} disabled={uniGw.models.length === 0}><FlaskConical size={15} /> Test</button>
                <Link to="/gateways/unified" className="btn"><Activity size={15} /> Live</Link>
                <Link to="/settings" className="btn"><Pencil size={15} /></Link>
                <button className="btn danger" title="Disable" onClick={disableUni}><Power size={15} /></button>
              </div>
            </div>
            <div className="mono mt-4 flex items-center justify-between gap-2 rounded-md border border-white/5 bg-black/30 px-3 py-2 text-xs text-[#9fb0ff]">
              <span className="truncate">{unifiedUrl()}</span>
              <button onClick={() => navigator.clipboard?.writeText(unifiedUrl())} title="Copy"><Copy size={14} /></button>
            </div>
            <div className="mt-4 space-y-2">
              {uniExposed.map((r: any) => (
                <div key={r.id} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{r.name}</span>
                    {r.has_keys ? <span className="chip" style={{ color: "#c2a05a", background: "#c2a05a22" }}><Lock size={11} /> API key</span> : <span className="chip" style={{ color: "#8a92a6", background: "#8a92a622" }}>Open</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5 text-xs">{r.names.map((n: string) => <span key={n} className="chip mono" style={{ color: "#9fb0e8", background: "#3b6fd822" }}>{n}</span>)}</div>
                </div>
              ))}
              {(uni.models || []).map((m: any) => (
                <div key={m.model_id} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="mono font-medium">{m.alias || m.name}</span>
                    <span className="chip" style={{ color: "#8a92a6", background: "#8a92a622" }}>Model</span>
                  </div>
                  <div className="mt-1 text-xs text-[#8b91b5]"><span className="mono">{m.upstream_id}</span> @ {m.endpoint}</div>
                </div>
              ))}
              {uniExposed.length === 0 && !(uni.models || []).length && <div className="text-sm text-[#8b91b5]">Nothing exposed yet.</div>}
            </div>
          </div>
          </div>
        </section>
      )}
      {testingUni && <TestGateway gw={uniGw} onClose={() => setTestingUni(false)} />}
      {testing && <TestGateway gw={testing} onClose={() => setTesting(null)} />}
      {stressing && <StressTest gw={stressing} onClose={() => setStressing(null)} />}
    </div>
  );
}
