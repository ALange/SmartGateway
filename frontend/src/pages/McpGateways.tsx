import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, ChevronDown, ChevronRight, Copy, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { Empty, Modal, PageHeader, StatusDot } from "../components/ui";
import McpTester from "../components/McpTester";

const url = (slug: string) => `${location.origin}/mcp/${slug}`;

function Connect({ gw }: { gw: any }) {
  const auth = gw.has_keys;
  const snippets: [string, string][] = [
    ["Claude Code", `claude mcp add --transport http ${gw.slug} ${url(gw.slug)}${auth ? ` \\\n  --header "Authorization: Bearer <your-key>"` : ""}`],
    ["Codex (~/.codex/config.toml)", `[mcp_servers.${gw.slug}]\nurl = "${url(gw.slug)}"${auth ? `\nhttp_headers = { Authorization = "Bearer <your-key>" }` : ""}`],
    ["Generic (mcpServers JSON)", JSON.stringify({ mcpServers: { [gw.slug]: { type: "http", url: url(gw.slug), ...(auth ? { headers: { Authorization: "Bearer <your-key>" } } : {}) } } }, null, 2)],
  ];
  return <div className="mt-3 space-y-3">{snippets.map(([t, s]) => (
    <div key={t}><div className="mb-1 text-xs text-[#787884]">{t}</div><pre className="mono scroll-thin overflow-x-auto rounded-md bg-black/40 p-3 text-[11px] text-[#a79cff]">{s}</pre></div>))}</div>;
}

function Calls({ gw, onClose }: { gw: any; onClose: () => void }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => { api(`/api/mcp-gateways/${gw.id}/calls`).then(setD); }, [gw.id]);
  return (
    <Modal title={`Calls · ${gw.name}`} onClose={onClose} wide>
      {!d ? <div className="text-sm text-[#8e8e9a]">Loading…</div> : (<div className="space-y-5">
        <div><div className="mb-1 text-xs uppercase tracking-wider text-[#787884]">Last 24 hours by name</div>
          {d.stats.length === 0 ? <div className="text-sm text-[#8e8e9a]">No calls yet.</div> : (
            <table className="w-full text-sm"><thead><tr className="text-left text-xs text-[#787884]"><th className="pb-1 font-medium">Name</th><th className="font-medium">Calls</th><th className="font-medium">Errors</th><th className="font-medium">Avg</th></tr></thead>
              <tbody>{d.stats.map((s: any) => <tr key={s.name} className="border-t border-white/5"><td className="mono py-1.5 pr-3">{s.name}</td><td>{s.calls}</td><td className={s.errors ? "text-[#d9776f]" : ""}>{s.errors}</td><td>{s.avg_ms} ms</td></tr>)}</tbody></table>)}</div>
        <div><div className="mb-1 text-xs uppercase tracking-wider text-[#787884]">Recent</div>
          <div className="max-h-72 space-y-1 overflow-auto scroll-thin text-xs">{d.recent.map((c: any) => (
            <div key={c.id} className="flex items-center justify-between gap-3 rounded-md bg-white/[0.02] px-2.5 py-1.5">
              <span className="mono truncate">{c.ok ? "✓" : "✗"} {c.method} · {c.name}</span>
              <span className="shrink-0 text-[#787884]">{c.total_ms} ms · {new Date(c.ts * 1000).toLocaleTimeString()}{c.error ? ` · ${c.error}` : ""}</span></div>))}</div></div>
      </div>)}
    </Modal>
  );
}

export default function McpGateways() {
  const [gws, setGws] = useState<any[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [test, setTest] = useState<any>(null);
  const [calls, setCalls] = useState<any>(null);
  const load = () => api("/api/mcp-gateways").then(setGws);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);
  const del = async (g: any) => { if (confirm(`Delete MCP gateway "${g.name}"? Its API keys stop working.`)) { await api(`/api/mcp-gateways/${g.id}`, { method: "DELETE" }); load(); } };
  return (
    <div className="fadein">
      <PageHeader title="MCP Gateways" sub="One MCP server URL combining the tools, resources and prompts of several MCP servers."
        actions={<Link to="/mcp-gateways/new" className="btn primary"><Plus size={16} /> New MCP gateway</Link>} />
      {gws.length === 0 && <Empty>No MCP gateways yet. Add MCP servers first, then combine them here.</Empty>}
      <div className="space-y-5">{gws.map((g) => (
        <div key={g.id} className="card card-pad">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <Link to={`/mcp-gateways/${g.id}/edit`} className="text-base font-semibold hover:text-[#a79cff]">{g.name}</Link>
              <div className="mt-1 text-xs text-[#8e8e9a]">{g.exposed.tools} tools · {g.exposed.resources + g.exposed.templates} resources · {g.exposed.prompts} prompts · {g.has_keys ? "API key required" : "open access"} · {g.calls_24h} calls/24h{g.errors_24h ? <span className="text-[#d9776f]"> ({g.errors_24h} errors)</span> : null}</div>
              <div className="mt-2 flex flex-wrap gap-2">{g.members.map((m: any) => <span key={m.id} className="chip" style={{ color: "#a79cff", background: "#6c5ce722" }}><StatusDot status={m.status} /> {m.endpoint_name} <span className="mono opacity-70">{m.prefix}</span></span>)}</div>
            </div>
            <div className="flex gap-2">
              <button className="btn" onClick={() => setTest(g)}><Play size={15} /> Test</button>
              <button className="btn" onClick={() => setCalls(g)}><BarChart3 size={15} /> Calls</button>
              <Link to={`/mcp-gateways/${g.id}/edit`} className="btn"><Pencil size={15} /></Link>
              <button className="btn danger" onClick={() => del(g)}><Trash2 size={15} /></button>
            </div>
          </div>
          <div className="mono mt-4 flex items-center justify-between gap-2 rounded-md border border-white/5 bg-black/30 px-3 py-2 text-xs text-[#a79cff]">
            <span className="truncate">{url(g.slug)}</span><button onClick={() => navigator.clipboard?.writeText(url(g.slug))} title="Copy"><Copy size={14} /></button></div>
          <button className="mt-3 flex items-center gap-1.5 text-sm text-[#a79cff]" onClick={() => setOpen(open === g.id ? null : g.id)}>{open === g.id ? <ChevronDown size={15} /> : <ChevronRight size={15} />} How to connect</button>
          {open === g.id && <Connect gw={g} />}
        </div>))}</div>
      {test && <McpTester gw={test} onClose={() => setTest(null)} />}
      {calls && <Calls gw={calls} onClose={() => setCalls(null)} />}
    </div>
  );
}
