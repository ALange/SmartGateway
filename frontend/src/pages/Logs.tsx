import { useEffect, useState } from "react";
import { api, PALETTE } from "../api";
import { ms, PageHeader } from "../components/ui";

export default function Logs() {
  const [rows, setRows] = useState<any[]>([]);
  const [gws, setGws] = useState<any[]>([]);
  const [eps, setEps] = useState<any[]>([]);
  const [gid, setGid] = useState("");
  const [errs, setErrs] = useState(false);
  useEffect(() => { api("/api/gateways").then(setGws); api("/api/endpoints").then(setEps); }, []);
  useEffect(() => {
    const load = () => api(`/api/logs?limit=200${gid ? `&gateway_id=${gid}` : ""}${errs ? "&only_errors=true" : ""}`).then(setRows);
    load(); const t = setInterval(load, 3000); return () => clearInterval(t);
  }, [gid, errs]);
  const epName = (id: number) => eps.find((e) => e.id === id)?.name || "—";
  const gwName = (id: number) => gws.find((g) => g.id === id)?.name || id;
  return (
    <div className="fadein">
      <PageHeader title="Request Log" sub="Most recent routed requests (auto-refreshing)."
        actions={<>
          <select className="input !w-auto" value={gid} onChange={(e) => setGid(e.target.value)}><option value="">All gateways</option>{gws.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</select>
          <label className="btn cursor-pointer"><input type="checkbox" checked={errs} onChange={(e) => setErrs(e.target.checked)} /> Errors only</label></>} />
      <div className="card overflow-x-auto p-2 scroll-thin">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">
            {["Time", "Gateway", "Public model", "Routed to", "Needs", "Status", "Queue", "TTFT", "Total", "Tokens", "Saved", "Tries"].map((h) => <th key={h} className="px-3 py-3 font-medium">{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-white/5 hover:bg-white/[0.02]" title={r.error || ""}>
                <td className="px-3 py-2 whitespace-nowrap text-[#8b91b5]">{new Date(r.ts * 1000).toLocaleTimeString()}</td>
                <td className="px-3 py-2">{gwName(r.gateway_id)}</td>
                <td className="mono px-3 py-2">{r.public_model}</td>
                <td className="px-3 py-2">{r.upstream_model ? <><span className="mono">{r.upstream_model}</span> <span className="text-[#6b7196]">@ {epName(r.endpoint_id)}</span></> : "—"}</td>
                <td className="px-3 py-2 text-xs text-[#8b91b5]">{r.requirements || "—"}{r.stream && " · stream"}</td>
                <td className="px-3 py-2"><span className="chip" style={{ color: r.ok ? "#5fb585" : "#d9776f", background: (r.ok ? "#5fb585" : "#d9776f") + "1f" }}>{r.status}</span></td>
                <td className="px-3 py-2">{r.queue_ms ? ms(r.queue_ms) : "—"}</td>
                <td className="px-3 py-2">{r.ttft_ms != null ? ms(r.ttft_ms) : "—"}</td>
                <td className="px-3 py-2">{ms(r.total_ms)}</td>
                <td className="px-3 py-2 text-[#8b91b5]">{r.prompt_tokens}→{r.completion_tokens}</td>
                <td className="px-3 py-2 whitespace-nowrap" title={r.compress_mode ? `compression: ${r.compress_mode}` : ""}>{r.tokens_saved ? <span style={{ color: r.compress_mode === "shadow" ? "#8a92a6" : "#5fb585" }}>{r.compress_mode === "shadow" ? "~" : "−"}{r.tokens_saved}</span> : "—"}</td>
                <td className="px-3 py-2">{r.attempts}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="p-6 text-center text-sm text-[#8b91b5]">No requests yet.</div>}
      </div>
    </div>
  );
}
