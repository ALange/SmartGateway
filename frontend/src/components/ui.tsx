import { Link } from "react-router-dom";
import { ArrowLeft, X } from "lucide-react";
import { CAP_STYLE } from "../api";

export function CapChip({ cap, source, onClick, off }: { cap: string; source?: string; onClick?: () => void; off?: boolean }) {
  const s = CAP_STYLE[cap] || { color: "#8e8e9a", label: cap };
  return (
    <span
      onClick={onClick}
      title={source ? `source: ${source}` : undefined}
      className="chip"
      style={{
        color: off ? "#5d5d68" : s.color,
        background: off ? "transparent" : s.color + "1f",
        borderColor: off ? "rgba(255,255,255,0.08)" : s.color + "44",
        cursor: onClick ? "pointer" : "default",
        borderStyle: off ? "dashed" : "solid",
      }}
    >
      {s.label}
      {source === "manual" && !off ? "*" : ""}
    </span>
  );
}

export function StatusDot({ status }: { status?: string }) {
  const c = status === "online" ? "#5fb585" : status === "offline" ? "#d9776f" : "#d1a553";
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {status === "online" && <span className="absolute inset-0 rounded-full" style={{ background: c, animation: "pulse-ring 2s infinite" }} />}
      <span className="relative h-2.5 w-2.5 rounded-full" style={{ background: c }} />
    </span>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onMouseDown={onClose}>
      <div className={`card card-pad fadein w-full ${wide ? "max-w-5xl" : "max-w-lg"} max-h-[90vh] overflow-auto scroll-thin`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-5 flex items-center justify-between">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button className="btn !p-1.5" onClick={onClose}><X size={16} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function PageHeader({ title, sub, actions, back, crumb }: { title: string; sub?: string; actions?: React.ReactNode; back?: string; crumb?: string[] }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-4">
        {back && <Link to={back} className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-white/10 text-[#c8c8d0] hover:bg-white/5" title="Back"><ArrowLeft size={16} /></Link>}
        <div className="min-w-0">
          {crumb && <div className="mb-0.5 text-xs text-[#787884]">{[...crumb, title].join("  ›  ")}</div>}
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {sub && <p className="mt-1 text-sm text-[#8e8e9a]">{sub}</p>}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

/** Metric tile: small icon + label on top, big number with an optional unit, a coloured one-line caption underneath. */
export function Stat({ icon: Icon, label, value, unit, sub, tone }: { icon?: any; label: string; value: React.ReactNode; unit?: string; sub?: React.ReactNode; tone?: "ok" | "warn" | "bad" }) {
  const c = tone === "ok" ? "#4ade80" : tone === "warn" ? "#e0a458" : tone === "bad" ? "#f08a84" : "#787884";
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-[13px] text-[#8e8e9a]">{Icon && <Icon size={14} />}{label}</div>
      <div className="mt-2 text-[28px] font-medium leading-none tracking-tight">{value}{unit && <span className="ml-1.5 text-sm font-normal text-[#787884]">{unit}</span>}</div>
      {sub != null && <div className="mt-2 text-xs" style={{ color: c }}>{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="card card-pad text-center text-sm text-[#8e8e9a]">{children}</div>;
}

export const fmt = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n)));
export const ms = (n: number) => (n >= 1000 ? (n / 1000).toFixed(2) + " s" : Math.round(n) + " ms");
