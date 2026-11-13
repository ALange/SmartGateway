import { useEffect, useState } from "react";
import { Copy, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { fmt, Modal } from "./ui";

const blank = { name: "", enabled: true, rpm_limit: "", tpm_limit: "", max_concurrent: "", daily_token_quota: "", monthly_token_quota: "", allowed_models: [] as string[], expires_days: "" };
const num = (v: any) => (v === "" || v == null ? null : +v);

function KeyForm({ gwId, models, init, onClose, onDone }: { gwId: number; models: string[]; init?: any; onClose: () => void; onDone: (created?: any) => void }) {
  const [f, setF] = useState<any>(init ? { ...blank, ...Object.fromEntries(Object.keys(blank).map((k) => [k, init[k] ?? (blank as any)[k]])), expires_days: "" } : blank);
  const [err, setErr] = useState("");
  const save = async () => {
    const body = { name: f.name, enabled: f.enabled, rpm_limit: num(f.rpm_limit), tpm_limit: num(f.tpm_limit), max_concurrent: num(f.max_concurrent),
      daily_token_quota: num(f.daily_token_quota), monthly_token_quota: num(f.monthly_token_quota), allowed_models: f.allowed_models,
      expires_at: f.expires_days ? Date.now() / 1000 + +f.expires_days * 86400 : init?.expires_at ?? null };
    try {
      const r = init ? await api(`/api/keys/${init.id}`, { method: "PATCH", json: body }) : await api(`/api/gateways/${gwId}/keys`, { method: "POST", json: body });
      onDone(init ? undefined : r); onClose();
    } catch (e: any) { setErr(e.message); }
  };
  const field = (k: string, label: string, ph = "unlimited") => (
    <div><label className="lbl">{label}</label><input type="number" min={1} className="input" placeholder={ph} value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
  );
  return (
    <Modal title={init ? `Edit key · ${init.name}` : "New API key"} onClose={onClose}>
      <div className="space-y-4">
        <div><label className="lbl">Name</label><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. team-a, ci, mobile-app" autoFocus /></div>
        <div className="grid grid-cols-2 gap-3">
          {field("rpm_limit", "Requests / minute")}{field("tpm_limit", "Tokens / minute")}
          {field("max_concurrent", "Max concurrent requests")}{field("expires_days", "Expires in (days)", init?.expires_at ? "keep current" : "never")}
          {field("daily_token_quota", "Daily token quota")}{field("monthly_token_quota", "Monthly token quota")}
        </div>
        <div>
          <label className="lbl">Allowed models <span className="normal-case text-[#6b7196]">(none selected = all)</span></label>
          <div className="flex flex-wrap gap-2">{models.map((m) => (
            <label key={m} className="btn cursor-pointer !py-1"><input type="checkbox" checked={f.allowed_models.includes(m)}
              onChange={(e) => setF({ ...f, allowed_models: e.target.checked ? [...f.allowed_models, m] : f.allowed_models.filter((x: string) => x !== m) })} /> <span className="mono">{m}</span></label>))}
            {models.length === 0 && <span className="text-sm text-[#6b7196]">This gateway has no models yet.</span>}</div>
        </div>
        {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-2.5 text-sm text-red-300">{err}</div>}
        <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!f.name} onClick={save}>{init ? "Save" : "Create key"}</button></div>
      </div>
    </Modal>
  );
}

export default function KeysPanel({ gwId, models, slug }: { gwId: number; models: string[]; slug: string }) {
  const [keys, setKeys] = useState<any[]>([]);
  const [form, setForm] = useState<any>(null);   // null | {} (new) | key (edit)
  const [created, setCreated] = useState<any>(null);
  const load = () => api(`/api/gateways/${gwId}/keys`).then(setKeys);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [gwId]);
  const toggle = async (k: any) => { await api(`/api/keys/${k.id}`, { method: "PATCH", json: { ...k, enabled: !k.enabled } }); load(); };
  const del = async (k: any) => { if (confirm(`Delete key "${k.name}"? Clients using it will get 401.`)) { await api(`/api/keys/${k.id}`, { method: "DELETE" }); load(); } };
  const pct = (v: number, q?: number) => (q ? `${fmt(v)} / ${fmt(q)}` : fmt(v));
  return (
    <div className="card card-pad">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold"><KeyRound size={15} /> API keys</h3>
          <p className="mt-1 text-xs text-[#8a92a6]">{keys.length === 0 ? "No keys: this gateway is open to anyone who can reach it. " : "Clients must send a key as Bearer token or x-api-key. "}Keys are stored hashed and shown only once.</p>
        </div>
        <button className="btn primary" onClick={() => setForm({})}><Plus size={15} /> New key</button>
      </div>
      {keys.length > 0 && (
        <div className="overflow-x-auto scroll-thin"><table className="w-full text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">{["Name", "Key", "Limits", "Tokens today / quota", "Month / quota", "Req 24h", "Last used", "Active", ""].map((h) => <th key={h} className="pb-2 pr-3 font-medium">{h}</th>)}</tr></thead>
          <tbody>{keys.map((k) => (
            <tr key={k.id} className="border-t border-white/5">
              <td className="py-2.5 pr-3 font-medium">{k.name}</td>
              <td className="mono py-2.5 pr-3 text-xs text-[#8a92a6]">{k.key_prefix}</td>
              <td className="py-2.5 pr-3 text-xs text-[#8a92a6]">{[k.rpm_limit && `${k.rpm_limit} rpm`, k.tpm_limit && `${fmt(k.tpm_limit)} tpm`, k.max_concurrent && `${k.max_concurrent} conc`, k.allowed_models?.length ? `${k.allowed_models.length} model(s)` : ""].filter(Boolean).join(" · ") || "none"}
                {k.expires_at ? ` · exp ${new Date(k.expires_at * 1000).toLocaleDateString()}` : ""}</td>
              <td className="py-2.5 pr-3">{pct(k.usage.day_tokens, k.daily_token_quota)}</td>
              <td className="py-2.5 pr-3">{pct(k.usage.month_tokens, k.monthly_token_quota)}</td>
              <td className="py-2.5 pr-3">{k.requests_24h}</td>
              <td className="py-2.5 pr-3 text-xs text-[#8a92a6]">{k.last_used ? new Date(k.last_used * 1000).toLocaleString() : "never"}</td>
              <td className="py-2.5 pr-3"><input type="checkbox" checked={k.enabled} onChange={() => toggle(k)} /></td>
              <td className="py-2.5 text-right whitespace-nowrap"><button className="btn !p-1.5" onClick={() => setForm(k)}><Pencil size={13} /></button> <button className="btn danger !p-1.5" onClick={() => del(k)}><Trash2 size={13} /></button></td>
            </tr>))}</tbody></table></div>
      )}
      {form && <KeyForm gwId={gwId} models={models} init={form.id ? form : undefined} onClose={() => setForm(null)} onDone={(c) => { load(); if (c) setCreated(c); }} />}
      {created && (
        <Modal title="Copy your new API key" onClose={() => setCreated(null)}>
          <div className="space-y-3">
            <div className="rounded-md border border-[#d1a553]/30 bg-[#d1a553]/10 p-3 text-sm text-[#e6c27a]">This is the only time the key is shown. Store it somewhere safe.</div>
            <div className="mono flex items-center justify-between gap-2 rounded-md border border-white/10 bg-black/30 px-3 py-2 text-xs"><span className="break-all">{created.key}</span>
              <button className="btn !p-1.5" onClick={() => navigator.clipboard?.writeText(created.key)}><Copy size={14} /></button></div>
            <pre className="mono scroll-thin overflow-x-auto rounded-md bg-black/40 p-3 text-[11px] text-[#9fb0ff]">{`curl ${location.origin}/gw/${slug}/v1/chat/completions \\\n  -H "Authorization: Bearer ${created.key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${models[0] || "model"}","messages":[{"role":"user","content":"Hi"}]}'`}</pre>
            <div className="flex justify-end"><button className="btn primary" onClick={() => setCreated(null)}>Done</button></div>
          </div>
        </Modal>
      )}
    </div>
  );
}
