import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api } from "../api";
import { Empty } from "./ui";

/** Admin audit trail: who changed what. Only field names are recorded, never values. */
export default function AuditLog() {
  const [rows, setRows] = useState<any[]>([]);
  const [action, setAction] = useState("");
  const [typ, setTyp] = useState("");
  const qs = `${action ? `&action=${encodeURIComponent(action)}` : ""}${typ ? `&target_type=${encodeURIComponent(typ)}` : ""}`;
  useEffect(() => {
    const load = () => api(`/api/audit?limit=200${qs}`).then(setRows).catch(() => {});
    load(); const t = setInterval(load, 5000); return () => clearInterval(t);
  }, [qs]);
  const types = ["gateways", "endpoints", "models", "keys", "content-filters", "content-filter-groups", "settings", "unified", "compression", "session"];
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input className="input !w-56" placeholder="Filter by action (e.g. delete)" value={action} onChange={(e) => setAction(e.target.value)} />
        <select className="input !w-auto" value={typ} onChange={(e) => setTyp(e.target.value)}><option value="">All targets</option>{types.map((t) => <option key={t}>{t}</option>)}</select>
        <a className="btn" href={`/api/audit/export?format=csv${qs}`} download><Download size={14} /> CSV</a>
        <a className="btn" href={`/api/audit/export?format=jsonl${qs}`} download><Download size={14} /> JSONL</a>
      </div>
      <div className="card overflow-x-auto p-2 scroll-thin">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">
            {["Time", "Who", "Action", "Target", "Changed fields", "IP"].map((h) => <th key={h} className="px-3 py-3 font-medium">{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                <td className="whitespace-nowrap px-3 py-2 text-[#8b91b5]">{new Date(r.ts * 1000).toLocaleString()}</td>
                <td className="px-3 py-2">{r.actor || "—"}</td>
                <td className="px-3 py-2"><span className="chip" style={{ color: r.status >= 400 ? "#d9776f" : r.action.startsWith("delete") ? "#d1a553" : "#7d9be0", background: "#ffffff0d" }}>{r.action}</span></td>
                <td className="mono px-3 py-2 text-xs">{r.target_type}{r.target_id ? ` #${r.target_id}` : ""}</td>
                <td className="px-3 py-2 text-xs text-[#8b91b5]">{(r.fields || []).join(", ") || "—"}</td>
                <td className="mono px-3 py-2 text-xs text-[#6b7196]">{r.ip}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty>No recorded changes yet.</Empty>}
      </div>
      <p className="mt-2 text-xs text-[#6b7196]">Only the names of the changed fields are stored, never their values, so keys and passwords cannot leak into this log.</p>
    </div>
  );
}
