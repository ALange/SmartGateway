import { useEffect, useState } from "react";
import { ChevronRight, Loader2, Plus, RefreshCw, Trash2, Sparkles } from "lucide-react";
import { ALL_CAPS, api } from "../api";
import { useLive } from "../live";
import { CapChip, Empty, Modal, PageHeader, StatusDot } from "../components/ui";

const TYPES: Record<string, { label: string; name: string; base_url: string; keyHint: string; notes: string[] }> = {
  generic: { label: "OpenAI-compatible", name: "", base_url: "", keyHint: "optional", notes: [] },
  "opencode-zen": {
    label: "OpenCode Zen", name: "OpenCode Zen", base_url: "https://opencode.ai/inference/v1", keyHint: "from opencode.ai/console",
    notes: ["Pay-as-you-go catalog of curated models (GPT, Claude, Gemini, GLM, Kimi, Qwen, MiniMax and more). Billed per token from your Zen credits.",
      "Models, capabilities and context sizes are read from OpenCode's own catalog: no test requests are sent, so adding it costs nothing.",
      "Models marked free may use your prompts to improve them. Stress tests spend real credits."],
  },
  "opencode-go": {
    label: "OpenCode Go", name: "OpenCode Go", base_url: "https://opencode.ai/inference/go/v1", keyHint: "from opencode.ai/console (Go subscription)",
    notes: ["Subscription catalog of open coding models. Usage is capped per 5 hours, week and month; when a cap is reached requests fail with 429, so add a fallback member to the gateway.",
      "Models, capabilities and context sizes are read from OpenCode's own catalog: no test requests are sent.",
      "The gateway sends the session header Go requires, one stable id per conversation. Stress tests use up your allowance."],
  },
};

function AddEndpoint({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState("generic");
  const [f, setF] = useState({ name: "", base_url: "", api_key: "", probe: true });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const t = TYPES[kind];
  const hosted = kind !== "generic";
  const pick = (k: string) => { setKind(k); setErr(""); setF((x) => ({ ...x, base_url: TYPES[k].base_url, name: !x.name || Object.values(TYPES).some((v) => v.name === x.name) ? TYPES[k].name : x.name })); };
  const submit = async () => {
    setBusy(true); setErr("");
    try { await api("/api/endpoints", { method: "POST", json: { ...f, api_key: f.api_key || null, probe: hosted ? false : f.probe, provider: hosted ? kind : null } }); onDone(); onClose(); }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <Modal title="Add endpoint" onClose={onClose}>
      <div className="space-y-4">
        <div><label className="lbl">Type</label>
          <div className="flex flex-wrap gap-2">{Object.entries(TYPES).map(([k, v]) => (
            <button key={k} type="button" className={`btn ${kind === k ? "primary" : ""}`} onClick={() => pick(k)}>{v.label}</button>))}</div></div>
        <div><label className="lbl">Name</label><input className="input" placeholder="e.g. GPU box #1" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div><label className="lbl">Base URL</label><input className="input mono" placeholder="http://host:8000/v1" value={f.base_url} readOnly={hosted} onChange={(e) => setF({ ...f, base_url: e.target.value })} /></div>
        <div><label className="lbl">API key <span className="normal-case text-[#787884]">({t.keyHint})</span></label><input className="input mono" type="password" autoComplete="off" placeholder="sk-…" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></div>
        {hosted && <ul className="space-y-1.5 rounded-md border border-white/5 bg-white/[0.02] p-3 text-xs text-[#8e8e9a]">{t.notes.map((n) => <li key={n}>• {n}</li>)}</ul>}
        {!hosted && <label className="flex cursor-pointer items-start gap-3 text-sm text-[#c8c8d0]">
          <input type="checkbox" className="mt-1" checked={f.probe} onChange={(e) => setF({ ...f, probe: e.target.checked })} />
          <span>Probe capabilities actively <span className="block text-xs text-[#8e8e9a]">Sends tiny test requests (embedding, image input, reasoning) to each model for accurate detection.</span></span>
        </label>}
        {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !f.name || !f.base_url} onClick={submit}>
            {busy ? <><Loader2 size={16} className="animate-spin" /> {hosted ? "Reading catalog…" : "Discovering models…"}</> : <><Sparkles size={16} /> Add &amp; discover</>}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export default function Endpoints() {
  const [eps, setEps] = useState<any[]>([]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());   // collapsed by default; click an endpoint to see its models
  const toggle = (id: number) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const { snapshot } = useLive();
  const load = () => api("/api/endpoints").then(setEps);
  useEffect(() => { load(); }, []);

  const rescan = async (id: number) => { setBusy(id); try { await api(`/api/endpoints/${id}/rescan`, { method: "POST" }); await load(); } catch (e: any) { alert(e.message); } setBusy(null); };
  const del = async (ep: any) => { if (confirm(`Delete endpoint "${ep.name}" and remove its models from all gateways?`)) { await api(`/api/endpoints/${ep.id}`, { method: "DELETE" }); load(); } };
  const patchModel = async (m: any, body: any) => { await api(`/api/models/${m.id}`, { method: "PATCH", json: body }); load(); };

  return (
    <div className="fadein">
      <PageHeader title="Endpoints" sub="Upstream OpenAI-compatible servers and the models they offer."
        actions={<button className="btn primary" onClick={() => setAdding(true)}><Plus size={16} /> Add endpoint</button>} />
      {eps.length === 0 && <Empty>No endpoints yet. Add one to discover its models and capabilities.</Empty>}
      <div className="space-y-5">
        {eps.map((ep) => {
          const status = snapshot?.endpoints?.[ep.id]?.status || ep.status;
          return (
            <div key={ep.id} className="card card-pad">
              <div className="flex cursor-pointer flex-wrap items-center justify-between gap-3" role="button" tabIndex={0} aria-expanded={open.has(ep.id)}
                onClick={() => toggle(ep.id)} onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(ep.id); } }}>
                <div className="flex items-center gap-3">
                  <ChevronRight size={16} className={`shrink-0 text-[#787884] transition-transform ${open.has(ep.id) ? "rotate-90" : ""}`} />
                  <StatusDot status={status} />
                  <div>
                    <div className="flex items-center gap-2 text-lg font-semibold">{ep.name}{ep.provider && <span className="chip" style={{ color: "#a79cff", background: "#6c5ce722" }}>{ep.provider === "opencode-go" ? "OpenCode Go" : "OpenCode Zen"}</span>}</div>
                    <div className="mono text-xs text-[#8e8e9a]">{ep.base_url}{ep.api_key ? " · key set" : ""}{ep.slot_source && ep.slot_source !== "none" ? ` · live load via ${ep.slot_source}` : ""}</div>
                    <div className="mt-0.5 text-xs text-[#787884]">{ep.models.length} model{ep.models.length === 1 ? "" : "s"}{ep.models.length ? ` · ${ep.models.filter((m: any) => m.enabled).length} enabled` : ""}</div>
                  </div>
                  {status === "offline" && ep.last_error && <span className="chip" style={{ color: "#d9776f", background: "#d9776f22" }}>{ep.last_error}</span>}
                </div>
                <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                  <button className="btn" disabled={busy === ep.id} onClick={() => rescan(ep.id)}>
                    <RefreshCw size={15} className={busy === ep.id ? "animate-spin" : ""} /> Re-scan
                  </button>
                  <button className="btn danger" onClick={() => del(ep)}><Trash2 size={15} /></button>
                </div>
              </div>
              {open.has(ep.id) && <div className="mt-5 overflow-x-auto scroll-thin">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs uppercase tracking-wider text-[#787884]">
                    <th className="pb-2 font-medium">Model</th><th className="pb-2 font-medium">Capabilities <span className="normal-case">(click to toggle)</span></th>
                    <th className="pb-2 font-medium">Slots</th><th className="pb-2 font-medium">Load</th><th className="pb-2 font-medium">Enabled</th></tr></thead>
                  <tbody>
                    {ep.models.map((m: any) => {
                      const live = snapshot?.models?.[m.id];
                      return (
                        <tr key={m.id} className="border-t border-white/5">
                          <td className="py-3 pr-4"><div className="mono">{m.upstream_id}</div>{m.context_length && <div className="text-xs text-[#787884]">ctx {m.context_length}</div>}</td>
                          <td className="py-3 pr-4"><div className="flex flex-wrap gap-1.5">
                            {ALL_CAPS.map((c) => {
                              const on = m.capabilities.includes(c);
                              return <CapChip key={c} cap={c} off={!on} source={m.cap_sources?.[c]}
                                onClick={() => patchModel(m, { capabilities: on ? m.capabilities.filter((x: string) => x !== c) : [...m.capabilities, c] })} />;
                            })}
                          </div></td>
                          <td className="py-3 pr-4"><input type="number" min={1} className="input !w-20 !py-1.5" defaultValue={m.max_slots}
                            onBlur={(e) => +e.target.value !== m.max_slots && patchModel(m, { max_slots: +e.target.value })} /></td>
                          <td className="py-3 pr-4 w-40">
                            <div className="flex h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full transition-all" style={{ width: `${Math.min(100, ((live?.in_flight || 0) / m.max_slots) * 100)}%`, background: "#6c5ce7" }} />
                              <div className="h-full transition-all" style={{ width: `${Math.min(100, ((live?.external || 0) / m.max_slots) * 100)}%`, background: "#8e8e9a" }} /></div>
                            <div className="mt-1 text-xs text-[#8e8e9a]">{live?.in_flight || 0}{live?.external ? ` + ${live.external} external` : ""}/{m.max_slots} busy{live?.breaker && live.breaker !== "closed" ? ` · breaker ${live.breaker}` : ""}</div>
                          </td>
                          <td className="py-3"><input type="checkbox" checked={m.enabled} onChange={(e) => patchModel(m, { enabled: e.target.checked })} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {ep.models.length === 0 && <div className="py-4 text-sm text-[#8e8e9a]">No models reported by this endpoint.</div>}
              </div>}
            </div>
          );
        })}
      </div>
      {adding && <AddEndpoint onClose={() => setAdding(false)} onDone={load} />}
    </div>
  );
}
