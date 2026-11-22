import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2, Minimize2, RotateCcw, Save, Trash2 } from "lucide-react";
import { api } from "../api";

const MODES: [string, string, string][] = [
  ["off", "Off", "Requests are forwarded exactly as received. Nothing is measured or changed."],
  ["shadow", "Shadow", "Measures what compression would save and records it, but still forwards the original. The safe way to evaluate."],
  ["on", "On", "Older tool results are compressed before they are sent upstream. User, assistant and system text is never touched."],
];

function Num({ label, value, onChange, step, hint }: { label: string; value: any; onChange: (v: number) => void; step?: number; hint?: string }) {
  return <div><label className="lbl">{label}</label><input type="number" step={step} className="input" value={value ?? ""} onChange={(e) => onChange(+e.target.value)} />{hint && <p className="mt-1 text-xs text-[#787884]">{hint}</p>}</div>;
}
function Check({ label, value, onChange, hint }: { label: string; value: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return <label className="flex cursor-pointer items-start gap-3 text-sm"><input type="checkbox" className="mt-1" checked={!!value} onChange={(e) => onChange(e.target.checked)} /><span>{label}{hint && <span className="block text-xs text-[#787884]">{hint}</span>}</span></label>;
}

export default function CompressionSettings() {
  const [s, setS] = useState<any>(null);
  const [mode, setMode] = useState("off");
  const [cfg, setCfg] = useState<any>(null);
  const [insights, setInsights] = useState(true);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async () => { const v = await api("/api/settings/compression"); setS(v); setMode(v.mode); setCfg(v.config); setInsights(v.context_insights_enabled); };
  useEffect(() => { load(); }, []);
  if (!s || !cfg) return null;
  const set = (path: string[], v: any) => setCfg((c: any) => { const n = structuredClone(c); let o = n; path.slice(0, -1).forEach((k) => (o = o[k])); o[path[path.length - 1]] = v; return n; });

  const save = async () => {
    setBusy("save"); setMsg(null);
    try { const v = await api("/api/settings/compression", { method: "PUT", json: { mode, config: cfg, context_insights_enabled: insights } }); setS(v); setMsg({ ok: true, text: "Saved. New requests use these settings within a few seconds." }); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
    setBusy("");
  };
  const clear = async () => { if (confirm("Clear the compression cache? Results will be recomputed on demand.")) { setBusy("clear"); await api("/api/compression/cache/clear", { method: "POST" }); await load(); setBusy(""); } };

  return (
    <div className="card card-pad">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-base font-semibold"><Minimize2 size={17} /> Context compression</h3>
        <Link to="/compression" className="text-sm text-[#a79cff] hover:underline">View savings &amp; insights →</Link>
      </div>
      <p className="mb-5 max-w-3xl text-sm text-[#8e8e9a]">
        Long agent conversations resend every earlier tool output on each turn. Compression shrinks those <em>older</em> tool results (JSON, logs, search results, long text)
        before they reach the model, so prompts get smaller and fit smaller context windows. The newest results the model has not seen yet are never touched. Applies to all client APIs.
        A model can override this default in its gateway settings.
      </p>

      <div className="grid gap-3 md:grid-cols-3">
        {MODES.map(([k, label, desc]) => (
          <button key={k} onClick={() => setMode(k)} className={`rounded-lg border p-4 text-left transition ${mode === k ? "border-[#6c5ce7] bg-[#6c5ce7]/10" : "border-white/[0.08] hover:bg-white/[0.03]"}`}>
            <div className="flex items-center gap-2 font-medium"><span className={`h-3 w-3 rounded-full border ${mode === k ? "border-[#6c5ce7] bg-[#6c5ce7]" : "border-white/30"}`} />{label}{k === "off" && <span className="chip text-[#8e8e9a]" style={{ background: "#ffffff0d" }}>default</span>}</div>
            <p className="mt-1.5 text-xs text-[#8e8e9a]">{desc}</p>
          </button>
        ))}
      </div>
      {mode === "on" && <div className="mt-3 rounded-md border border-[#d1a553]/30 bg-[#d1a553]/10 p-3 text-xs text-[#e6c27a]">Compression is lossy: sampled arrays, truncated strings and dropped log lines are replaced by markers. Try <b>Shadow</b> first to see the savings without changing what models receive.</div>}

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <Num label="Minimum size to compress (characters)" value={cfg.min_content_chars} onChange={(v) => set(["min_content_chars"], v)} hint="Smaller tool results are left alone." />
        <div className="space-y-3 self-end">
          <Check label="Record cache-alignment and context-composition insights" value={insights} onChange={setInsights} hint="Stores sizes, labels and hashes per request. Never request content." />
        </div>
      </div>

      <div className="mt-5">
        <div className="lbl">Compressors</div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <Check label="JSON" value={cfg.enable_json} onChange={(v) => set(["enable_json"], v)} hint="Samples long arrays, trims long strings" />
          <Check label="Logs" value={cfg.enable_log} onChange={(v) => set(["enable_log"], v)} hint="Keeps errors, collapses repeats" />
          <Check label="Search results" value={cfg.enable_search} onChange={(v) => set(["enable_search"], v)} hint="grep-style file:line output" />
          <Check label="Text truncation" value={cfg.enable_simple} onChange={(v) => set(["enable_simple"], v)} hint="Head + tail for everything else" />
          <Check label="Mixed content" value={cfg.enable_mixed_content} onChange={(v) => set(["enable_mixed_content"], v)} hint="Split code fences / blocks" />
        </div>
      </div>

      <details className="mt-5 rounded-md border border-white/[0.08] p-3">
        <summary className="cursor-pointer text-sm text-[#8e8e9a]">Advanced parameters</summary>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <Num label="JSON · max array items" value={cfg.json.max_items} onChange={(v) => set(["json", "max_items"], v)} hint="0 keeps every item" />
          <Num label="JSON · string keep ratio" value={cfg.json.compression_ratio} step={0.05} onChange={(v) => set(["json", "compression_ratio"], v)} hint="Long strings shrink to this fraction" />
          <div><label className="lbl">JSON · fields kept verbatim</label><input className="input mono" value={(cfg.json.preserve_fields || []).join(", ")} onChange={(e) => set(["json", "preserve_fields"], e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} /></div>
          <Num label="Logs · max lines" value={cfg.log.max_lines} onChange={(v) => set(["log", "max_lines"], v)} />
          <div className="self-end"><Check label="Logs · keep every timestamped line" value={cfg.log.pin_timestamps} onChange={(v) => set(["log", "pin_timestamps"], v)} hint="Off: only error/warn lines are pinned" /></div>
          <div />
          <Num label="Search · max files" value={cfg.search.max_results} onChange={(v) => set(["search", "max_results"], v)} />
          <Num label="Search · lines per file" value={cfg.search.max_lines_per_file} onChange={(v) => set(["search", "max_lines_per_file"], v)} />
          <Num label="Search · snippet length" value={cfg.search.max_snippet_chars} onChange={(v) => set(["search", "max_snippet_chars"], v)} />
          <Num label="Text · keep ratio" value={cfg.simple.compression_ratio} step={0.05} onChange={(v) => set(["simple", "compression_ratio"], v)} hint="Share of the text that is kept" />
          <div className="self-end"><Check label="Text · keep UUIDs/hashes from the cut part" value={cfg.simple.preserve_identifiers} onChange={(v) => set(["simple", "preserve_identifiers"], v)} hint="Appends ids that truncation removed" /></div>
        </div>
        <button className="btn mt-4" onClick={() => setCfg(structuredClone(s.defaults))}><RotateCcw size={14} /> Reset parameters to defaults</button>
      </details>

      {msg && <div className={`mt-4 rounded-md border p-2.5 text-sm ${msg.ok ? "border-[#5fb585]/30 bg-[#5fb585]/10 text-[#7fd1a3]" : "border-red-400/30 bg-red-400/10 text-red-300"}`}>{msg.text}</div>}
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-[#787884]">In-memory compression cache: {s.cache.entries} entries · {s.cache.hits} hits / {s.cache.misses} misses</span>
        <div className="flex gap-2">
          <button className="btn" onClick={clear} disabled={busy === "clear"}><Trash2 size={15} /> Clear cache</button>
          <button className="btn primary" onClick={save} disabled={busy === "save"}>{busy === "save" ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save</button>
        </div>
      </div>
    </div>
  );
}
