import { Link } from "react-router-dom";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Plug2 } from "lucide-react";
import { fmt, ms, StatusDot } from "./ui";

const tip = { contentStyle: { background: "#10132a", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 12, fontSize: 12 }, cursor: { fill: "rgba(255,255,255,0.04)" } };

export default function McpPanel({ d }: { d: any }) {
  const t = d.totals;
  const bars = d.timeline.map((b: any) => ({ ...b, label: new Date(b.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), ok: b.calls - b.errors }));
  return (
    <div className="mt-5">
      <div className="mb-3 flex items-center gap-2"><Plug2 size={16} /><h3 className="text-base font-semibold">MCP servers</h3>
        <Link to="/mcp-endpoints" className="ml-2 text-xs text-[#9fb0e8] hover:underline">manage</Link></div>
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        {[["MCP calls", fmt(t.calls), `${t.endpoints} endpoint${t.endpoints === 1 ? "" : "s"}, ${t.online} online`],
          ["Success rate", t.calls ? (((t.calls - t.errors) / t.calls) * 100).toFixed(1) + "%" : "–", `${t.errors} errors`],
          ["Latency p50 / p95", t.p50_ms == null ? "–" : ms(t.p50_ms), t.p95_ms == null ? "" : `p95 ${ms(t.p95_ms)}`],
          ["Gateways used", String(d.gateways.length), d.gateways[0] ? `busiest: ${d.gateways[0].name}` : "no calls in this period"]].map(([k, v, sub]) => (
          <div key={k} className="card card-pad"><div className="text-sm text-[#8b91b5]">{k}</div><div className="mt-1.5 text-2xl font-semibold">{v}</div><div className="mt-1 text-xs text-[#6b7196]">{sub}</div></div>))}
      </div>
      <div className="mt-5 grid gap-5 xl:grid-cols-[1.6fr_1fr]">
        <div className="card card-pad overflow-x-auto scroll-thin">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]"><th className="pb-2 font-medium">Endpoint</th><th className="pb-2 font-medium">Offers</th><th className="pb-2 font-medium">Calls</th><th className="pb-2 font-medium">Errors</th><th className="pb-2 font-medium">Avg</th><th className="pb-2 font-medium">p95</th></tr></thead>
            <tbody>{d.endpoints.map((e: any) => (
              <tr key={e.id} className="border-t border-white/5">
                <td className="py-2.5 pr-3"><span className="flex items-center gap-2"><StatusDot status={e.status} /><span className="font-medium">{e.name}</span></span></td>
                <td className="pr-3 text-xs text-[#8b91b5]">{e.tools} tools · {e.resources} res · {e.prompts} prompts</td>
                <td className="pr-3">{fmt(e.calls)}</td><td className={`pr-3 ${e.errors ? "text-[#d9776f]" : ""}`}>{e.errors}</td>
                <td className="pr-3">{e.avg_ms == null ? "–" : ms(e.avg_ms)}</td><td>{e.p95_ms == null ? "–" : ms(e.p95_ms)}</td></tr>))}
            {d.endpoints.length === 0 && <tr><td colSpan={6} className="py-3 text-sm text-[#6b7196]">No MCP servers yet.</td></tr>}</tbody>
          </table>
          {d.tools.length > 0 && (<>
            <div className="mb-1 mt-5 text-xs uppercase tracking-wider text-[#6b7196]">Top tools</div>
            <div className="divide-y divide-white/5">{d.tools.map((x: any) => (
              <div key={x.endpoint + x.name} className="flex items-center justify-between gap-3 py-1.5 text-sm"><span className="mono truncate">{x.name}</span>
                <span className="shrink-0 text-xs text-[#8b91b5]">{x.endpoint} · {fmt(x.calls)} calls{x.errors ? <span className="text-[#d9776f]"> · {x.errors} err</span> : null} · {ms(x.avg_ms)}</span></div>))}</div></>)}
        </div>
        <div className="card card-pad">
          <h3 className="text-base font-semibold">Calls over time</h3>
          <div className="mt-3 h-[220px]">{t.calls === 0 ? <div className="flex h-full items-center justify-center text-sm text-[#6b7196]">No MCP calls in this period</div> : (
            <ResponsiveContainer><BarChart data={bars}><XAxis dataKey="label" tick={{ fill: "#6b7196", fontSize: 11 }} axisLine={false} tickLine={false} interval={Math.floor(bars.length / 5)} />
              <Tooltip {...tip} /><Bar dataKey="ok" stackId="a" fill="#3b6fd8" name="ok" /><Bar dataKey="errors" stackId="a" fill="#d9776f" name="errors" /></BarChart></ResponsiveContainer>)}</div>
        </div>
      </div>
    </div>
  );
}
