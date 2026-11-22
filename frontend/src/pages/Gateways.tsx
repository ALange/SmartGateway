import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Copy, Pencil, Plus, Trash2, Activity, FlaskConical, Gauge } from "lucide-react";
import { api, gwUrl, STRATEGIES } from "../api";
import { CapChip, Empty, PageHeader } from "../components/ui";
import TestGateway from "../components/TestGateway";
import StressTest from "../components/StressTest";
import { GATEWAY_TABS, LinkTabs } from "../components/Tabs";

export default function Gateways({ kind = "router" }: { kind?: "router" | "council" }) {
  const council = kind === "council";
  const [gws, setGws] = useState<any[]>([]);
  const [testing, setTesting] = useState<any>(null);
  const [stressing, setStressing] = useState<any>(null);
  const nav = useNavigate();
  const load = () => { api("/api/gateways").then(setGws); };
  useEffect(() => { load(); }, []);
  const shown = gws.filter((g) => (g.kind || "router") === kind);
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
    catch (e: any) { if (!confirm(`${e.message}\n\nDelete it anyway? Councils using it will fail until you pick another model.`)) return; await api(`/api/gateways/${g.id}?force=true`, { method: "DELETE" }); }
    load();
  };


  return (
    <div className="fadein">
      <PageHeader title="Gateways"
        sub={council ? "A council asks several models, has them review each other anonymously, and a chairman writes the final answer."
          : "Each gateway is a single OpenAI-compatible endpoint that fans out to your upstream models."}
        actions={council
          ? <button className="btn primary" onClick={() => nav("/councils/new")}><Plus size={16} /> New council</button>
          : <button className="btn primary" onClick={() => nav("/gateways/new")}><Plus size={16} /> New gateway</button>} />
      <LinkTabs items={GATEWAY_TABS} />
      {shown.length === 0 && <Empty>{council ? "No councils yet. Create one to combine several models into a single, reviewed answer." : "No gateways yet. Create one to combine endpoint models behind a single API."}</Empty>}
      <div className="grid gap-5 xl:grid-cols-2">
        {shown.map((g) => (
          <div key={g.id} className="card card-pad">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Link to={g.kind === "council" ? `/gateways/${g.id}/edit` : `/gateways/${g.id}`} className="text-base font-semibold hover:text-[#a79cff]">{g.name}</Link>
                <div className="mt-1 text-xs text-[#8e8e9a]">{g.kind === "council" ? <><span className="chip mr-1" style={{ color: "#b79cf0", background: "#b79cf01f" }}>Council</span>{g.council?.councillors?.length || 0} councillors{g.council?.review_enabled === false ? "" : " · peer review"} · chairman {gwName(g, g.council?.chairman)}</> : <>{STRATEGIES[g.strategy]} · queue {g.queue_timeout}s</>} · {g.has_keys ? "API key required" : "open access"}</div>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <button className="btn whitespace-nowrap" onClick={() => setTesting(g)}><FlaskConical size={15} /> Test</button>
                {g.kind !== "council" && <button className="btn whitespace-nowrap" onClick={() => setStressing(g)} title="Measure peak throughput by ramping up concurrent requests"><Gauge size={15} /> Stress test</button>}
                {g.kind !== "council" && <Link to={`/gateways/${g.id}`} className="btn"><Activity size={15} /> Live</Link>}
                <Link to={`/gateways/${g.id}/edit`} className="btn"><Pencil size={15} /></Link>
                <button className="btn danger" onClick={() => del(g)}><Trash2 size={15} /></button>
              </div>
            </div>
            <div className="mono mt-4 flex items-center justify-between gap-2 rounded-md border border-white/5 bg-black/30 px-3 py-2 text-xs text-[#a79cff]">
              <span className="truncate">{gwUrl(g.slug)}</span>
              <button onClick={() => navigator.clipboard?.writeText(gwUrl(g.slug))} title="Copy"><Copy size={14} /></button>
            </div>
            <div className="mt-4 space-y-2">
              {g.models.map((m: any) => (
                <div key={m.id} className="rounded-lg border border-white/5 bg-white/[0.02] p-3" style={m.enabled === false ? { opacity: 0.55 } : undefined}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="mono font-medium">{m.public_name}{m.enabled === false && <span className="chip ml-2" title="Not served: requests get 404">disabled</span>}</span>
                    <div className="flex flex-wrap gap-1">{m.capabilities.map((c: string) => <CapChip key={c} cap={c} />)}</div>
                  </div>
                  {g.kind === "council"
                    ? <div className="mt-1 text-xs text-[#8e8e9a]">{(g.council?.councillors || []).map((r: any) => gwName(g, r)).join(", ")}</div>
                    : <div className="mt-1 text-xs text-[#8e8e9a]">{m.members.length} upstream: {m.members.map((x: any) => `${x.upstream_id}@${x.endpoint_name}`).join(", ")}</div>}
                </div>
              ))}
              {g.models.length === 0 && <div className="text-sm text-[#8e8e9a]">No models exposed yet.</div>}
            </div>
          </div>
        ))}
      </div>
      {testing && <TestGateway gw={testing} onClose={() => setTesting(null)} />}
      {stressing && <StressTest gw={stressing} onClose={() => setStressing(null)} />}
    </div>
  );
}
