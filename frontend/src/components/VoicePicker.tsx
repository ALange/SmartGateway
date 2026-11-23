export const voiceName = (v: any): string => (typeof v === "string" ? v : String(v?.id ?? v?.voice_id ?? v?.name ?? ""));
export const splitVoices = (t: string) => String(t || "").split(/[,\n]/).map((x) => x.trim()).filter(Boolean);

/** Checklist of the voices the upstream reported (discovered when the endpoint was added); the text field below stays editable for names it does not list. */
export default function VoicePicker({ options, text, onChange, onRefresh, busy, err }: { options: string[]; text: string; onChange: (t: string) => void; onRefresh: () => void; busy: boolean; err: string }) {
  const chosen = splitVoices(text);
  const set = new Set(chosen);
  const toggle = (v: string) => onChange((set.has(v) ? chosen.filter((x) => x !== v) : [...chosen, v]).join(", "));
  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="lbl !mb-0">Voices to offer (optional)</label>
        {options.length > 0 && <>
          <button type="button" className="btn !py-1 text-xs" onClick={() => onChange([...new Set([...chosen, ...options])].join(", "))}>All</button>
          <button type="button" className="btn !py-1 text-xs" onClick={() => onChange(chosen.filter((x) => !options.includes(x)).join(", "))}>None</button>
        </>}
        <button type="button" className="btn !py-1 text-xs" disabled={busy} onClick={onRefresh} title="Ask the upstream server for its current voice list (GET /audio/voices)">{busy ? "Asking…" : "Refresh from upstream"}</button>
      </div>
      {options.length > 0 ? (
        <div className="mt-2 flex max-h-44 flex-wrap gap-1.5 overflow-y-auto">
          {options.map((v) => (
            <label key={v} className="btn cursor-pointer !py-1 text-xs mono" style={set.has(v) ? { borderColor: "#6c5ce7" } : undefined}>
              <input type="checkbox" checked={set.has(v)} onChange={() => toggle(v)} /> {v}
            </label>
          ))}
        </div>
      ) : <div className="mt-2 text-xs text-[#787884]">The upstream did not list its voices. Type the names below, or use Refresh once the server supports <span className="mono">/audio/voices</span>.</div>}
      <input className="input mono mt-2" value={text} onChange={(e) => onChange(e.target.value)} placeholder="af_heart, am_michael" title="Returned by GET /v1/audio/voices. Names the upstream does not list can be typed here." />
      <div className="mt-1 text-xs text-[#787884]">{chosen.length ? `${chosen.length} voice${chosen.length === 1 ? "" : "s"} offered.` : "Nothing selected: every voice the upstream reports is offered (always up to date)."}</div>
      {err && <div className="mt-1 text-xs text-[#ff9b9b]">{err}</div>}
    </div>
  );
}
