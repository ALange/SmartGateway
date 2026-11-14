import { useEffect, useState } from "react";
import { Eraser, HardDrive, Loader2, Save } from "lucide-react";
import { api } from "../api";
import { fmt } from "./ui";

const FIELDS: [string, string, string][] = [
  ["log_retention_days", "Request log", "requestlog"],
  ["compression_retention_days", "Compression events", "compressionevent"],
  ["stress_retention_days", "Stress test runs", "stressrun"],
  ["audit_retention_days", "Audit log", "auditevent"],
];
const size = (b: number) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.round(b / 1024) + " KB");

export default function Retention() {
  const [v, setV] = useState<Record<string, number>>({});
  const [info, setInfo] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = () => { api("/api/settings").then((s) => setV(Object.fromEntries(FIELDS.map(([k]) => [k, s[k] ?? 0])))); api("/api/maintenance/db").then(setInfo).catch(() => {}); };
  useEffect(load, []);
  const run = async (name: string, f: () => Promise<string>) => {
    setBusy(name); setMsg(null);
    try { setMsg({ ok: true, text: await f() }); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    setBusy(""); load();
  };
  const save = () => run("save", async () => { await api("/api/settings", { method: "PUT", json: Object.fromEntries(FIELDS.map(([k]) => [k, Math.max(0, Math.round(+v[k] || 0))])) }); return "Saved. Old rows are pruned hourly."; });
  const prune = () => run("prune", async () => {
    const r = await api("/api/maintenance/prune", { method: "POST" });
    const n = Object.values(r.deleted as Record<string, number>).reduce((a, b) => a + b, 0);
    return n ? `Deleted ${n} old rows.` : "Nothing to delete (a limit of 0 keeps everything).";
  });
  const vac = () => run("vac", async () => { const r = await api("/api/maintenance/vacuum", { method: "POST" }); return `Database file ${size(r.before)} → ${size(r.after)}.`; });
  return (
    <div className="card card-pad mt-5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-base font-semibold"><HardDrive size={17} /> Data retention</h3>
        {info && <span className="text-xs text-[#8a92a6]">database {size(info.size_bytes)}</span>}
      </div>
      <p className="mb-4 text-sm text-[#8b91b5]">Delete old rows automatically so the database does not grow forever. 0 days keeps everything. Request logs hold no prompts or answers, only metadata.</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {FIELDS.map(([k, label, table]) => (
          <div key={k}><label className="lbl">{label} · keep days</label>
            <input type="number" min={0} max={3650} className="input" value={v[k] ?? 0} onChange={(e) => setV({ ...v, [k]: +e.target.value })} />
            <div className="mt-1 text-xs text-[#6b7196]">{info ? `${fmt(info.rows[table] ?? 0)} rows` : ""}</div></div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button className="btn primary" onClick={save} disabled={!!busy}>{busy === "save" ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save</button>
        <button className="btn" onClick={prune} disabled={!!busy}>{busy === "prune" ? <Loader2 size={15} className="animate-spin" /> : <Eraser size={15} />} Prune now</button>
        <button className="btn" onClick={vac} disabled={!!busy} title="Shrinks the file after rows were deleted; blocks the database for a moment">{busy === "vac" ? <Loader2 size={15} className="animate-spin" /> : <HardDrive size={15} />} Shrink file</button>
        {msg && <span className="text-sm" style={{ color: msg.ok ? "#5fb585" : "#d9776f" }}>{msg.text}</span>}
      </div>
    </div>
  );
}
