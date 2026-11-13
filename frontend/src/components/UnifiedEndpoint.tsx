import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Copy, KeyRound, Layers, Loader2, Lock, Plus, Save, Trash2 } from "lucide-react";
import { api } from "../api";

type Row = { id: number; name: string; slug: string; models: string[]; has_keys: boolean; exposed: boolean; model: string | null; names: string[] };

/** One OpenAI-compatible endpoint in front of several gateways; the model name is the gateway name. */
export default function UnifiedEndpoint() {
  const [enabled, setEnabled] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [keys, setKeys] = useState<any[]>([]);
  const [keyName, setKeyName] = useState("");
  const [fresh, setFresh] = useState<any>(null);  // a just-created key: its plaintext is shown once
  const [keyErr, setKeyErr] = useState("");
  const apply = (v: any) => { setEnabled(v.enabled); setRows(v.gateways); setKeys(v.keys || []); setDirty(false); };
  useEffect(() => { api("/api/unified").then(apply).catch(() => {}); }, []);
  if (!rows) return null;

  const url = `${location.origin}/unified/v1`;
  const patch = (id: number, p: Partial<Row>) => { setRows(rows.map((r) => (r.id === id ? { ...r, ...p } : r))); setDirty(true); };
  const exposed = rows.filter((r) => r.exposed);
  const firstName = exposed[0]?.name || "My Gateway";
  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const v = await api("/api/unified", { method: "PUT", json: { enabled, gateways: exposed.map((r) => ({ id: r.id, model: r.model })) } });
      apply(v); setMsg({ ok: true, text: "Saved. Changes apply within a few seconds." });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    setBusy(false);
  };
  const addKey = async () => {
    setKeyErr("");
    try {
      const k = await api("/api/unified/keys", { method: "POST", json: { name: keyName.trim() } });
      setFresh(k); setKeyName(""); setKeys((x) => [...x, k]);
    } catch (e: any) { setKeyErr(e.message); }
  };
  const delKey = async (k: any) => {
    if (!confirm(`Delete the key "${k.name}"? Clients using it stop working immediately.${keys.length === 1 ? " This is the last key, so the endpoint will accept calls without a unified key again." : ""}`)) return;
    await api(`/api/unified/keys/${k.id}`, { method: "DELETE" });
    setKeys((x) => x.filter((y) => y.id !== k.id)); if (fresh?.id === k.id) setFresh(null);
  };
  const copy = () => { navigator.clipboard?.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); };

  return (
    <div className="card card-pad mb-5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-base font-semibold"><Layers size={17} /> Unified endpoint</h3>
        <span className="chip" style={enabled ? { color: "#5fb585", background: "#5fb58522" } : { color: "#8a92a6", background: "#8a92a622" }}>{enabled ? "Enabled" : "Disabled"}</span>
      </div>
      <p className="mb-5 max-w-3xl text-sm text-[#8a92a6]">
        Offer several gateways through one OpenAI-compatible URL. Clients pick a gateway by its <b className="font-medium text-[#c9cfdd]">name</b> in the <span className="mono">model</span> field.
        Each gateway keeps its own routing, cache, limits and API keys: use the gateway's key as the Bearer token (open gateways need none).
      </p>

      <label className="mb-4 flex cursor-pointer items-center gap-3 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => { setEnabled(e.target.checked); setDirty(true); }} />
        <span>Enable the unified endpoint</span>
      </label>

      <label className="lbl">Endpoint URL</label>
      <div className="mb-5 flex gap-2">
        <input readOnly className="input mono" value={url} onFocus={(e) => e.currentTarget.select()} />
        <button className="btn whitespace-nowrap" onClick={copy}>{copied ? <Check size={15} /> : <Copy size={15} />} {copied ? "Copied" : "Copy"}</button>
      </div>

      <label className="lbl">Gateways to expose</label>
      {rows.length === 0 ? (
        <div className="rounded-md border border-white/5 p-4 text-sm text-[#8a92a6]">No gateways yet. <Link to="/gateways/new" className="text-[#7d9be0] hover:underline">Create one</Link> first.</div>
      ) : (
        <div className="divide-y divide-white/5 rounded-md border border-white/5">
          {rows.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3.5 py-3">
              <label className="flex min-w-[220px] flex-1 cursor-pointer items-center gap-3 text-sm">
                <input type="checkbox" checked={r.exposed} disabled={r.models.length === 0} onChange={(e) => patch(r.id, { exposed: e.target.checked })} />
                <span className="font-medium">{r.name}</span>
                <span className="mono text-xs text-[#6b7196]">{r.slug}</span>
                {r.has_keys
                  ? <span className="chip" style={{ color: "#c2a05a", background: "#c2a05a22" }}><Lock size={11} /> API key</span>
                  : <span className="chip" style={{ color: "#8a92a6", background: "#8a92a622" }}>Open</span>}
              </label>
              {r.models.length === 0 && <span className="text-xs text-[#6b7196]">no models configured</span>}
              {r.exposed && r.models.length > 1 && (
                <div className="flex items-center gap-2 text-xs text-[#8a92a6]">
                  <span>Default model</span>
                  <select className="input !w-auto !py-1" value={r.model || ""} onChange={(e) => patch(r.id, { model: e.target.value })}>
                    {r.models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
              )}
              {r.exposed && (
                <div className="flex w-full flex-wrap items-center gap-1.5 pl-7 text-xs text-[#6b7196]">
                  model name{r.models.length > 1 ? "s" : ""}:
                  {(dirty ? [r.name, ...(r.models.length > 1 ? r.models.map((m) => `${r.name}/${m}`) : [])] : r.names).map((n) => <span key={n} className="chip mono" style={{ color: "#9fb0e8", background: "#3b6fd822" }}>{n}</span>)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <p className="mt-2 text-xs text-[#6b7196]">A gateway with several public models is reachable as <span className="mono">name</span> (its default model) and as <span className="mono">name/model</span>. If two gateways share a name, the slug is appended to the second.</p>

      <label className="lbl mt-6 flex items-center gap-1.5"><KeyRound size={12} /> API keys</label>
      <p className="mb-3 max-w-3xl text-xs text-[#6b7196]">
        {keys.length === 0
          ? "No key set: callers authenticate against each gateway (open gateways need none, protected ones need their own key). Create a key to require authentication for the whole endpoint."
          : "A key is required. A unified key works for every exposed gateway, so clients don't need each gateway's own key. A gateway's own key still works for that gateway only."}
      </p>
      {fresh && (
        <div className="mb-3 rounded-md border border-[#5fb585]/30 bg-[#5fb585]/10 p-3 text-sm">
          <div className="mb-1.5 text-[#7fd1a3]">Key "{fresh.name}" created. Copy it now, it will not be shown again.</div>
          <div className="flex gap-2"><input readOnly className="input mono" value={fresh.key} onFocus={(e) => e.currentTarget.select()} />
            <button className="btn whitespace-nowrap" onClick={() => navigator.clipboard?.writeText(fresh.key)}><Copy size={15} /> Copy</button>
            <button className="btn" onClick={() => setFresh(null)}>Done</button></div>
        </div>
      )}
      {keys.length > 0 && (
        <div className="mb-3 divide-y divide-white/5 rounded-md border border-white/5">
          {keys.map((k) => (
            <div key={k.id} className="flex items-center gap-4 px-3.5 py-2.5 text-sm">
              <span className="min-w-[140px] font-medium">{k.name}</span>
              <span className="mono text-xs text-[#8a92a6]">{k.prefix}</span>
              <span className="flex-1 text-xs text-[#6b7196]">created {new Date(k.created_at * 1000).toLocaleDateString()}</span>
              <button className="btn danger !px-2 !py-1" title="Delete key" onClick={() => delKey(k)}><Trash2 size={14} /></button>
            </div>))}
        </div>
      )}
      <div className="flex gap-2">
        <input className="input max-w-xs" placeholder="Key name, e.g. team-a, ci" value={keyName} maxLength={60} onChange={(e) => setKeyName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && keyName.trim() && addKey()} />
        <button className="btn whitespace-nowrap" onClick={addKey} disabled={!keyName.trim()}><Plus size={15} /> Create key</button>
      </div>
      {keyErr && <div className="mt-2 text-sm text-red-300">{keyErr}</div>}

      {exposed.length > 0 && (
        <pre className="mono scroll-thin mt-5 overflow-x-auto rounded-md bg-black/40 p-3 text-[11px] leading-relaxed text-[#9fb0ff]">{`curl ${url}/chat/completions \\
  -H "Authorization: Bearer <${keys.length ? "unified API key" : "gateway API key, if the gateway has one"}>" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${firstName}",
       "messages":[{"role":"user","content":"Hi"}]}'

# list what the caller may use:  GET ${url}/models`}</pre>
      )}

      {msg && <div className={`mt-4 rounded-md border p-2.5 text-sm ${msg.ok ? "border-[#5fb585]/30 bg-[#5fb585]/10 text-[#7fd1a3]" : "border-red-400/30 bg-red-400/10 text-red-300"}`}>{msg.text}</div>}
      <div className="mt-5 flex justify-end">
        <button className="btn primary" onClick={save} disabled={busy || !dirty}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save</button>
      </div>
    </div>
  );
}
