import { Tabs } from "../components/Tabs";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BadgeCheck, FolderPlus, Library, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { api } from "../api";
import { Empty, Modal, PageHeader } from "../components/ui";

const STAGES: Record<string, string> = { input: "Prompt", output: "Answer", both: "Prompt + answer" };
const blankFilter = () => ({ name: "", description: "", pattern: "", stage: "both", action: "block", validator: "", enabled: true, group_ids: [] as number[] });
const blankGroup = () => ({ name: "", description: "", filter_ids: [] as number[] });

const Chip = ({ color, children, title }: { color: string; children: React.ReactNode; title?: string }) =>
  <span className="chip" title={title} style={{ color, background: color + "1f" }}>{children}</span>;

// the API answers 409 with {message, used_by}; api() turns that into a JSON string
const parseUse = (e: any): { message: string; used_by: string[] } | null => { try { const d = JSON.parse(e.message); return d?.used_by ? d : null; } catch { return null; } };

export default function Filters() {
  const [data, setData] = useState<{ filters: any[]; groups: any[]; validators: any[] }>({ filters: [], groups: [], validators: [] });
  const [note, setNote] = useState("");
  const [tab, setTab] = useState<"filters" | "groups">("filters");
  const [q, setQ] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [editF, setEditF] = useState<any>(null);
  const [editG, setEditG] = useState<any>(null);
  const [err, setErr] = useState("");

  const load = () => api("/api/content-filters").then(setData);
  useEffect(() => { load(); }, []);
  const groupName = (id: number) => data.groups.find((g) => g.id === id)?.name || "?";
  const filterName = (id: number) => data.filters.find((f) => f.id === id)?.name || "?";

  const shown = useMemo(() => data.filters.filter((f) =>
    (!groupFilter || (groupFilter === "none" ? f.group_ids.length === 0 : f.group_ids.includes(+groupFilter))) &&
    (!q || `${f.name} ${f.pattern} ${f.description}`.toLowerCase().includes(q.toLowerCase()))), [data, q, groupFilter]);

  const remove = async (kind: "filters" | "groups", item: any) => {
    const url = `/api/content-${kind === "filters" ? "filters" : "filter-groups"}/${item.id}`;
    setErr("");
    if (!confirm(`Delete ${kind === "filters" ? "filter" : "group"} "${item.name}"?`)) return;
    try { await api(url, { method: "DELETE" }); }
    catch (e: any) {
      const use = parseUse(e);
      if (!use) { setErr(e.message); return; }
      if (!confirm(`"${item.name}" is used by:\n\n${use.used_by.join("\n")}\n\nThese gateway models stop applying it. Delete anyway?`)) return;
      await api(`${url}?force=true`, { method: "DELETE" });
    }
    load();
  };
  const installPresets = async () => {
    setErr(""); setNote("");
    try {
      const r = await api("/api/content-filters/presets/install", { method: "POST" });
      setNote(r.added_filters || r.added_groups ? `Added ${r.added_filters} filter${r.added_filters === 1 ? "" : "s"} and ${r.added_groups} group${r.added_groups === 1 ? "" : "s"}.` : "Everything is already there — nothing was added.");
      load();
    } catch (e: any) { setErr(e.message); }
  };
  const toggle = async (f: any) => { await api(`/api/content-filters/${f.id}`, { method: "PUT", json: { ...f, enabled: !f.enabled } }); load(); };

  return (
    <div className="fadein">
      <PageHeader title="Content filters" sub="Reusable regex filters that block or redact text. Group them, then pick filters or groups on a gateway model."
        actions={<>
          <button className="btn" onClick={installPresets} title="Adds the ready-made filters for national IDs and GDPR data that are missing. Nothing you changed is touched."><Library size={16} /> Add built-in presets</button>
          <button className="btn" onClick={() => setEditG(blankGroup())}><FolderPlus size={16} /> New group</button>
          <button className="btn primary" onClick={() => setEditF(blankFilter())}><Plus size={16} /> New filter</button></>} />
      {err && <div className="mb-4 rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
      {note && <div className="mb-4 rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-300">{note}</div>}
      <p className="mb-4 text-xs text-[#6b7196]">Built-in presets cover national IDs, tax and phone numbers of 10 countries and GDPR sensitive data. Number formats with a checksum are only flagged when the checksum is valid. Keyword filters for GDPR categories are an approximation — they miss paraphrases and flag some harmless text. Presets are ordinary filters: edit, duplicate or delete them freely.</p>

      <Tabs items={[["filters", `Filters (${data.filters.length})`], ["groups", `Groups (${data.groups.length})`]]} value={tab} onChange={(k) => setTab(k as "filters" | "groups")} />

      {tab === "filters" && <>
        <div className="mb-4 flex flex-wrap gap-2">
          <div className="relative"><Search size={14} className="absolute left-3 top-3 text-[#6b7196]" /><input className="input !pl-8" placeholder="Search name or pattern" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <select className="input !w-auto" value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>
            <option value="">All groups</option><option value="none">Not in any group</option>{data.groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</select>
        </div>
        {data.filters.length === 0 ? <Empty>No content filters yet. Create one, then select it on a gateway model under “Safety &amp; guardrails”.</Empty> : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">{["On", "Name", "Pattern", "Applies to", "Action", "Groups", "Used by", ""].map((h) => <th key={h} className="px-3 py-3 font-medium">{h}</th>)}</tr></thead>
              <tbody>
                {shown.map((f) => (
                  <tr key={f.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                    <td className="px-3 py-2"><input type="checkbox" checked={f.enabled} onChange={() => toggle(f)} title="Disabled filters are skipped everywhere" /></td>
                    <td className="px-3 py-2"><div className="font-medium" style={{ opacity: f.enabled ? 1 : 0.5 }}>{f.name}</div>{f.description && <div className="text-xs text-[#6b7196]">{f.description}</div>}</td>
                    <td className="px-3 py-2 text-xs"><div className="mono max-w-[360px] truncate text-[#9fb0ff]" title={f.pattern}>{f.pattern}</div>
                      {f.validator && <span className="mt-1 inline-flex items-center gap-1 text-[#5fb585]" title="Only structurally valid numbers match"><BadgeCheck size={12} /> checksum: {data.validators.find((v) => v.name === f.validator)?.label || f.validator}</span>}</td>
                    <td className="px-3 py-2"><Chip color="#8a92a6">{STAGES[f.stage]}</Chip></td>
                    <td className="px-3 py-2"><Chip color={f.action === "block" ? "#d9776f" : "#d1a553"}>{f.action === "block" ? "Block" : "Redact"}</Chip></td>
                    <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{f.group_ids.length ? f.group_ids.map((id: number) => <Chip key={id} color="#7d9be0">{groupName(id)}</Chip>) : <span className="text-[#6b7196]">—</span>}</div></td>
                    <td className="px-3 py-2 text-xs text-[#8b91b5]" title={f.used_by.join("\n")}>{f.used_by.length ? `${f.used_by.length} model${f.used_by.length > 1 ? "s" : ""}` : "—"}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap"><button className="btn" onClick={() => setEditF(f)}><Pencil size={14} /></button> <button className="btn danger" onClick={() => remove("filters", f)}><Trash2 size={14} /></button></td>
                  </tr>))}
              </tbody>
            </table>
            {shown.length === 0 && <div className="p-6 text-center text-sm text-[#8b91b5]">No filters match.</div>}
          </div>)}
      </>}

      {tab === "groups" && (data.groups.length === 0 ? <Empty>No groups yet. A group is a named set of filters, for example “Company secrets”.</Empty> : (
        <div className="grid gap-5 xl:grid-cols-2">
          {data.groups.map((g) => (
            <div key={g.id} className="card card-pad">
              <div className="flex items-start justify-between gap-3">
                <div><div className="text-base font-semibold">{g.name}</div>{g.description && <div className="mt-1 text-xs text-[#8b91b5]">{g.description}</div>}</div>
                <div className="flex gap-2"><button className="btn" onClick={() => setEditG(g)}><Pencil size={14} /></button><button className="btn danger" onClick={() => remove("groups", g)}><Trash2 size={14} /></button></div>
              </div>
              <div className="mt-3 flex flex-wrap gap-1">{g.filter_ids.length ? g.filter_ids.map((id: number) => <Chip key={id} color="#9aa0c3">{filterName(id)}</Chip>) : <span className="text-xs text-[#6b7196]">Empty group</span>}</div>
              <div className="mt-3 text-xs text-[#6b7196]">{g.used_by.length ? `Used by: ${g.used_by.join(", ")}` : "Not used by any gateway model"}</div>
            </div>))}
        </div>))}

      {editF && <FilterEditor value={editF} groups={data.groups} validators={data.validators} onClose={() => setEditF(null)} onSaved={() => { setEditF(null); load(); }} />}
      {editG && <GroupEditor value={editG} filters={data.filters} onClose={() => setEditG(null)} onSaved={() => { setEditG(null); load(); }} />}
    </div>
  );
}

function FilterEditor({ value, groups, validators, onClose, onSaved }: { value: any; groups: any[]; validators: any[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>(value);
  const [err, setErr] = useState("");
  const [sample, setSample] = useState("");
  const [res, setRes] = useState<any>(null);
  const set = (p: any) => setF({ ...f, ...p });
  const save = async () => {
    setErr("");
    try { await api(f.id ? `/api/content-filters/${f.id}` : "/api/content-filters", { method: f.id ? "PUT" : "POST", json: { ...f, validator: f.validator || null } }); onSaved(); } catch (e: any) { setErr(e.message); }
  };
  const test = async () => { setErr(""); try { setRes(await api("/api/content-filters/test", { method: "POST", json: { pattern: f.pattern, text: sample, validator: f.validator || null } })); } catch (e: any) { setRes(null); setErr(e.message); } };
  return (
    <Modal title={f.id ? "Edit filter" : "New filter"} onClose={onClose} wide>
      {err && <div className="mb-3 rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
      <div className="grid gap-4 md:grid-cols-2">
        <div><label className="lbl">Name</label><input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Project Falcon" /></div>
        <div><label className="lbl">Description (optional)</label><input className="input" value={f.description} onChange={(e) => set({ description: e.target.value })} /></div>
        <div className="md:col-span-2"><label className="lbl">Regular expression (case-insensitive)</label><input className="input mono" value={f.pattern} onChange={(e) => set({ pattern: e.target.value })} placeholder="project\s+falcon" />
          <p className="mt-1 text-xs text-[#6b7196]">Python regex syntax. It must not match empty text.</p></div>
        <div className="md:col-span-2"><label className="lbl">Validation (optional)</label>
          <select className="input" value={f.validator || ""} onChange={(e) => set({ validator: e.target.value })}><option value="">None — every regex match counts</option>{validators.map((v) => <option key={v.name} value={v.name}>{v.label}</option>)}</select>
          <p className="mt-1 text-xs text-[#6b7196]">A match only counts if the number passes this checksum. If the pattern has a group named <span className="mono">(?P&lt;n&gt;…)</span>, that group is checked instead of the whole match.</p></div>
        <div><label className="lbl">Applies to</label><select className="input" value={f.stage} onChange={(e) => set({ stage: e.target.value })}>{Object.entries(STAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div><label className="lbl">When it matches</label><select className="input" value={f.action} onChange={(e) => set({ action: e.target.value })}><option value="block">Block the request / answer</option><option value="redact">Replace the text with [FILTERED]</option></select></div>
        <div className="md:col-span-2"><label className="lbl">Groups</label>
          {groups.length === 0 ? <p className="text-xs text-[#6b7196]">No groups yet — create one with “New group”.</p> :
            <div className="flex flex-wrap gap-3">{groups.map((g) => <label key={g.id} className="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={f.group_ids.includes(g.id)}
              onChange={(e) => set({ group_ids: e.target.checked ? [...f.group_ids, g.id] : f.group_ids.filter((x: number) => x !== g.id) })} /> {g.name}</label>)}</div>}</div>
        <label className="flex cursor-pointer items-center gap-3 text-sm md:col-span-2"><input type="checkbox" checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
        <div className="md:col-span-2 rounded-md border border-white/[0.08] p-3">
          <label className="lbl">Test against sample text</label>
          <textarea className="input" rows={2} value={sample} onChange={(e) => setSample(e.target.value)} placeholder="Paste text to check the pattern…" />
          <div className="mt-2 flex items-center gap-3"><button className="btn" onClick={test} disabled={!f.pattern}>Test</button>
            {res && <span className="text-sm" style={{ color: res.count ? "#d1a553" : "#5fb585" }}>{res.count ? `${res.count} match${res.count > 1 ? "es" : ""}: ${res.matches.slice(0, 5).map((m: any) => `“${m.text}”`).join(", ")}` : "No match"}</span>}</div>
        </div>
      </div>
      <div className="mt-5 flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></div>
    </Modal>
  );
}

function GroupEditor({ value, filters, onClose, onSaved }: { value: any; filters: any[]; onClose: () => void; onSaved: () => void }) {
  const [g, setG] = useState<any>(value);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try { await api(g.id ? `/api/content-filter-groups/${g.id}` : "/api/content-filter-groups", { method: g.id ? "PUT" : "POST", json: g }); onSaved(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <Modal title={g.id ? "Edit group" : "New group"} onClose={onClose}>
      {err && <div className="mb-3 rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
      <div className="grid gap-4">
        <div><label className="lbl">Name</label><input className="input" value={g.name} onChange={(e) => setG({ ...g, name: e.target.value })} placeholder="Company secrets" /></div>
        <div><label className="lbl">Description (optional)</label><input className="input" value={g.description} onChange={(e) => setG({ ...g, description: e.target.value })} /></div>
        <div><label className="lbl">Filters in this group</label>
          {filters.length === 0 ? <p className="text-xs text-[#6b7196]">No filters yet — create some first.</p> :
            <div className="max-h-64 space-y-1 overflow-y-auto rounded-md border border-white/[0.08] p-2">{filters.map((f) => <label key={f.id} className="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={g.filter_ids.includes(f.id)}
              onChange={(e) => setG({ ...g, filter_ids: e.target.checked ? [...g.filter_ids, f.id] : g.filter_ids.filter((x: number) => x !== f.id) })} /> {f.name} <span className="mono text-xs text-[#6b7196]">{f.pattern}</span></label>)}</div>}</div>
      </div>
      <div className="mt-5 flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></div>
    </Modal>
  );
}
