import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Copy, Pencil, Plus, Trash2, Activity, FlaskConical, Gauge } from "lucide-react";
import { api, gwUrl, STRATEGIES } from "../api";
import { CapChip, Empty, PageHeader } from "../components/ui";
import TestGateway from "../components/TestGateway";
import StressTest from "../components/StressTest";

export default function Gateways() {
  const [gws, setGws] = useState<any[]>([]);
  const [testing, setTesting] = useState<any>(null);
  const [stressing, setStressing] = useState<any>(null);
  const nav = useNavigate();
  const load = () => api("/api/gateways").then(setGws);
  useEffect(() => { load(); }, []);
  const del = async (g: any) => { if (confirm(`Delete gateway "${g.name}"?`)) { await api(`/api/gateways/${g.id}`, { method: "DELETE" }); load(); } };

  return (
    <div className="fadein">
      <PageHeader title="Smart Gateways" sub="Each gateway is a single OpenAI-compatible endpoint that fans out to your upstream models."
        actions={<button className="btn primary" onClick={() => nav("/gateways/new")}><Plus size={16} /> New gateway</button>} />
      {gws.length === 0 && <Empty>No gateways yet. Create one to combine endpoint models behind a single API.</Empty>}
      <div className="grid gap-5 xl:grid-cols-2">
        {gws.map((g) => (
          <div key={g.id} className="card card-pad">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Link to={`/gateways/${g.id}`} className="text-base font-semibold hover:text-[#9fb0ff]">{g.name}</Link>
                <div className="mt-1 text-xs text-[#8b91b5]">{STRATEGIES[g.strategy]} · queue {g.queue_timeout}s · {g.has_keys ? "API key required" : "open access"}</div>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <button className="btn whitespace-nowrap" onClick={() => setTesting(g)}><FlaskConical size={15} /> Test</button>
                <button className="btn whitespace-nowrap" onClick={() => setStressing(g)} title="Measure peak throughput by ramping up concurrent requests"><Gauge size={15} /> Stress test</button>
                <Link to={`/gateways/${g.id}`} className="btn"><Activity size={15} /> Live</Link>
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
                  <div className="mt-1 text-xs text-[#8b91b5]">{m.members.length} upstream: {m.members.map((x: any) => `${x.upstream_id}@${x.endpoint_name}`).join(", ")}</div>
                </div>
              ))}
              {g.models.length === 0 && <div className="text-sm text-[#8b91b5]">No models exposed yet.</div>}
            </div>
          </div>
        ))}
      </div>
      {testing && <TestGateway gw={testing} onClose={() => setTesting(null)} />}
      {stressing && <StressTest gw={stressing} onClose={() => setStressing(null)} />}
    </div>
  );
}
