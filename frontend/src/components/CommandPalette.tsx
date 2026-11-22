import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search } from "lucide-react";

export type PaletteItem = { label: string; to: string; hint?: string; icon?: any };

/** Ctrl/Cmd+K quick jump: type to filter pages and common actions, arrows + Enter to go. */
export default function CommandPalette({ items, open, onClose }: { items: PaletteItem[]; open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const nav = useNavigate();
  const ref = useRef<HTMLInputElement>(null);
  const shown = useMemo(() => {
    const w = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return items.filter((x) => w.every((t) => `${x.label} ${x.hint || ""}`.toLowerCase().includes(t)));
  }, [q, items]);
  useEffect(() => { if (open) { setQ(""); setI(0); setTimeout(() => ref.current?.focus(), 0); } }, [open]);
  useEffect(() => setI(0), [q]);
  if (!open) return null;
  const go = (x?: PaletteItem) => { if (x) { nav(x.to); onClose(); } };
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-4 pt-[14vh]" onMouseDown={onClose}>
      <div className="card fadein w-full max-w-lg overflow-hidden" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-white/[0.07] px-4 py-3">
          <Search size={16} className="text-[#787884]" />
          <input ref={ref} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to a page or action…" className="w-full bg-transparent text-sm outline-none placeholder:text-[#787884]"
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") { e.preventDefault(); setI((v) => Math.min(v + 1, shown.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setI((v) => Math.max(v - 1, 0)); }
              else if (e.key === "Enter") go(shown[i]);
            }} />
          <kbd className="rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] text-[#787884]">esc</kbd>
        </div>
        <div className="scroll-thin max-h-[50vh] overflow-y-auto p-2">
          {shown.length === 0 && <div className="px-3 py-6 text-center text-sm text-[#787884]">Nothing matches “{q}”.</div>}
          {shown.map((x, k) => (
            <button key={x.to + x.label} onMouseEnter={() => setI(k)} onClick={() => go(x)}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${k === i ? "bg-white/[0.07] text-white" : "text-[#c8c8d0]"}`}>
              {x.icon && <x.icon size={16} className="text-[#8e8e9a]" />}
              <span className="flex-1">{x.label}</span>
              {x.hint && <span className="text-xs text-[#787884]">{x.hint}</span>}
              {k === i && <CornerDownLeft size={13} className="text-[#787884]" />}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
