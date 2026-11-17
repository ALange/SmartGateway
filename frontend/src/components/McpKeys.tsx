import { useEffect, useState } from "react";
import { Copy, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { Modal } from "./ui";

function KeyForm({ gwId, tools, init, onClose, onDone }: { gwId: number; tools: string[]; init?: any; onClose: () => void; onDone: (created?: any) => void }) {
  const [f, setF] = useState<any>({ name: init?.name || "", enabled: init?.enabled ?? true, rpm_limit: init?.rpm_limit ?? "", expires_days: "", allowed_tools: init?.allowed_tools || [] });
  const [err, setErr] = useState("");
  const save = async () => {
    const body = { name: f.name, enabled: f.enabled, rpm_limit: f.rpm_limit === "" ? null : +f.rpm_limit, allowed_tools: f.allowed_tools.length ? f.allowed_tools : null,
      expires_at: f.expires_days ? Date.now() / 1000 + +f.expires_days * 86400 : init?.expires_at ?? null };
    try {
      const r = init ? await api(`/api/mcp-keys/${init.id}`, { method: "PATCH", json: body }) : await api(`/api/mcp-gateways/${gwId}/keys`, { method: "POST", json: body });
      onDone(init ? undefined : r); onClose();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <Modal title={init ? `Edit key · ${init.name}` : "New MCP API key"} onClose={onClose}>
      <div className="space-y-4">
        <div><label className="lbl">Name</label><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. claude-code, ci" autoFocus /></div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="lbl">Requests / minute</label><input type="number" min={1} className="input" placeholder="unlimited" value={f.rpm_limit} onChange={(e) => setF({ ...f, rpm_limit: e.target.value })} /></div>
          <div><label className="lbl">Expires in (days)</label><input type="number" min={1} className="input" placeholder={init?.expires_at ? "keep current" : "never"} value={f.expires_days} onChange={(e) => setF({ ...f, expires_days: e.target.value })} /></div>
        </div>
        <div><label className="lbl">Allowed tools <span className="normal-case text-[#6b7196]">(none selected = all)</span></label>
          <div className="flex max-h-48 flex-wrap gap-2 overflow-auto scroll-thin">{tools.map((t) => (
            <label key={t} className="btn cursor-pointer !py-1"><input type="checkbox" checked={f.allowed_tools.includes(t)}
              onChange={(e) => setF({ ...f, allowed_tools: e.target.checked ? [...f.allowed_tools, t] : f.allowed_tools.filter((x: string) => x !== t) })} /> <span className="mono">{t}</span></label>))}
            {tools.length === 0 && <span className="text-sm text-[#6b7196]">This gateway exposes no tools yet.</span>}</div></div>
        {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-2.5 text-sm text-red-300">{err}</div>}
        <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!f.name} onClick={save}>{init ? "Save" : "Create key"}</button></div>
      </div>
    </Modal>
  );
}

export default function McpKeys({ gwId, tools, slug }: { gwId: number; tools: string[]; slug: string }) {
  const [keys, setKeys] = useState<any[]>([]);
  const [form, setForm] = useState<any>(null);
  const [created, setCreated] = useState<any>(null);
  const load = () => api(`/api/mcp-gateways/${gwId}/keys`).then(setKeys);
  useEffect(() => { load(); }, [gwId]);
  const toggle = async (k: any) => { await api(`/api/mcp-keys/${k.id}`, { method: "PATCH", json: { ...k, enabled: !k.enabled } }); load(); };
  const del = async (k: any) => { if (confirm(`Delete key "${k.name}"?`)) { await api(`/api/mcp-keys/${k.id}`, { method: "DELETE" }); load(); } };
  return (
    <div className="card card-pad mt-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-base font-semibold"><KeyRound size={16} /> API keys</h3>
        <button className="btn" onClick={() => setForm({})}><Plus size={15} /> New key</button>
      </div>
      {keys.length === 0 ? <p className="text-sm text-[#8b91b5]">No keys: this MCP gateway is open to anyone who can reach it. Create a key to require <span className="mono">Authorization: Bearer …</span> or <span className="mono">x-api-key</span>.</p> : (
        <table className="w-full text-sm"><tbody>{keys.map((k) => (
          <tr key={k.id} className="border-t border-white/5">
            <td className="py-2.5 pr-3 font-medium">{k.name}</td><td className="mono pr-3 text-xs text-[#8b91b5]">{k.key_prefix}</td>
            <td className="pr-3 text-xs text-[#8a92a6]">{[k.rpm_limit && `${k.rpm_limit} rpm`, k.allowed_tools?.length && `${k.allowed_tools.length} tool(s)`, k.expires_at && `expires ${new Date(k.expires_at * 1000).toLocaleDateString()}`].filter(Boolean).join(" · ") || "no limits"}</td>
            <td className="pr-3"><input type="checkbox" checked={k.enabled} onChange={() => toggle(k)} /></td>
            <td className="text-right"><button className="btn !p-1.5" onClick={() => setForm(k)}><Pencil size={14} /></button> <button className="btn danger !p-1.5" onClick={() => del(k)}><Trash2 size={14} /></button></td>
          </tr>))}</tbody></table>)}
      {form && <KeyForm gwId={gwId} tools={tools} init={form.id ? form : undefined} onClose={() => setForm(null)} onDone={(c) => { load(); if (c) setCreated(c); }} />}
      {created && (
        <Modal title="Copy your new API key" onClose={() => setCreated(null)}>
          <div className="space-y-3">
            <div className="rounded-md border border-[#d1a553]/30 bg-[#d1a553]/10 p-3 text-sm text-[#e6c27a]">This is the only time the key is shown. Store it somewhere safe.</div>
            <div className="mono flex items-center justify-between gap-2 rounded-md border border-white/10 bg-black/30 px-3 py-2 text-xs"><span className="break-all">{created.key}</span>
              <button className="btn !p-1.5" onClick={() => navigator.clipboard?.writeText(created.key)}><Copy size={14} /></button></div>
            <pre className="mono scroll-thin overflow-x-auto rounded-md bg-black/40 p-3 text-[11px] text-[#9fb0ff]">{`claude mcp add --transport http ${slug} ${location.origin}/mcp/${slug} \\\n  --header "Authorization: Bearer ${created.key}"`}</pre>
            <div className="flex justify-end"><button className="btn primary" onClick={() => setCreated(null)}>Done</button></div>
          </div>
        </Modal>
      )}
    </div>
  );
}
