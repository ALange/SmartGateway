import { useEffect, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { api } from "../api";
import { Modal } from "./ui";

async function rpc(slug: string, method: string, params?: any) {
  const r = await api(`/api/admin-mcp/${slug}`, { method: "POST", json: { jsonrpc: "2.0", id: 1, method, ...(params ? { params } : {}) } });
  if (r.error) throw new Error(r.error.message);
  return r.result;
}

export default function McpTester({ gw, onClose }: { gw: any; onClose: () => void }) {
  const [tools, setTools] = useState<any[]>([]);
  const [sel, setSel] = useState("");
  const [args, setArgs] = useState("{}");
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { rpc(gw.slug, "tools/list").then((r) => { setTools(r.tools); if (r.tools[0]) setSel(r.tools[0].name); }).catch((e) => setErr(e.message)); }, [gw.slug]);
  const tool = tools.find((t) => t.name === sel);
  const run = async () => {
    setBusy(true); setErr(""); setOut(null);
    let a: any;
    try { a = args.trim() ? JSON.parse(args) : {}; } catch { setErr("Arguments must be valid JSON"); setBusy(false); return; }
    const t0 = performance.now();
    try { const r = await rpc(gw.slug, "tools/call", { name: sel, arguments: a }); setOut({ r, ms: Math.round(performance.now() - t0) }); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <Modal title={`Test MCP gateway · ${gw.name}`} onClose={onClose} wide>
      <div className="space-y-4">
        {tools.length === 0 && !err && <div className="text-sm text-[#8a92a6]">This gateway exposes no tools (tick some in its editor).</div>}
        {tools.length > 0 && (<>
          <div><label className="lbl">Tool</label><select className="input mono" value={sel} onChange={(e) => { setSel(e.target.value); setOut(null); }}>{tools.map((t) => <option key={t.name}>{t.name}</option>)}</select>
            {tool?.description && <p className="mt-1 text-xs text-[#8b91b5]">{tool.description}</p>}</div>
          {tool?.inputSchema?.properties && <pre className="mono scroll-thin max-h-32 overflow-auto rounded-md bg-black/30 p-2 text-[11px] text-[#8b91b5]">{JSON.stringify(tool.inputSchema.properties, null, 1)}</pre>}
          <div><label className="lbl">Arguments (JSON)</label><textarea className="input mono" rows={4} value={args} onChange={(e) => setArgs(e.target.value)} /></div>
          <div className="flex justify-end"><button className="btn primary" disabled={busy || !sel} onClick={run}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Call tool</button></div>
        </>)}
        {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
        {out && (
          <div className="rounded-md border border-white/[0.08] bg-[#0c0e13] p-3 text-xs">
            <div className="mb-2 flex gap-3"><span className="chip" style={{ color: out.r.isError ? "#d9776f" : "#5fb585", background: (out.r.isError ? "#d9776f" : "#5fb585") + "22" }}>{out.r.isError ? "tool error" : "OK"}</span><span className="text-[#8a92a6]">{out.ms} ms</span></div>
            <pre className="mono scroll-thin max-h-72 overflow-auto whitespace-pre-wrap">{(out.r.content || []).map((c: any) => c.text ?? JSON.stringify(c)).join("\n") || JSON.stringify(out.r, null, 1)}</pre>
          </div>)}
      </div>
    </Modal>
  );
}
