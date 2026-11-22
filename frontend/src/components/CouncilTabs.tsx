import { useState } from "react";
import { ms } from "./ui";

/** Tab view of one council run: the chairman, each councillor's own answer, and the anonymous reviews with the resulting ranking. */
export default function CouncilTabs({ data }: { data: any }) {
  const [tab, setTab] = useState<string>("final");
  const s1: any[] = data.stage1 || [], rv: any[] = data.reviews || [], rank: any[] = data.ranking || [];
  const tabs: [string, string][] = [["final", "Chairman"], ...s1.map((a, i): [string, string] => [`a${i}`, a.label.split("/").slice(-1)[0] || `#${i + 1}`]), ["reviews", `Reviews (${rv.length})`]];
  const tokens = (u?: any) => (u ? `${u.prompt_tokens}→${u.completion_tokens} tok` : "");
  const a = tab.startsWith("a") && tab !== "reviews" ? s1[+tab.slice(1)] : null;
  const pos = (label: string) => rank.findIndex((r) => r.label === label);
  return (
    <div className="mt-3 rounded-md border border-white/[0.08] bg-black/20">
      <div className="flex flex-wrap gap-1 border-b border-white/[0.06] p-1.5">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} title={k.startsWith("a") && k !== "reviews" ? s1[+k.slice(1)]?.label : undefined}
            className={`rounded px-2 py-1 text-xs ${tab === k ? "bg-[#6c5ce7]/30 text-white" : "text-[#8e8e9a] hover:bg-white/5"}`}>
            {l}{k.startsWith("a") && k !== "reviews" && pos(s1[+k.slice(1)]?.label) === 0 && rank[0]?.avg_rank != null ? " 🥇" : ""}{k.startsWith("a") && k !== "reviews" && !s1[+k.slice(1)]?.ok ? " ⚠" : ""}</button>))}
      </div>
      <div className="max-h-96 overflow-auto p-3 text-xs scroll-thin">
        {tab === "final" && (
          <div className="space-y-1 text-[#c8c8d0]">
            <div>Chairman: <span className="mono">{data.chairman?.label}</span> · {ms(data.chairman?.ms || 0)} · {tokens(data.chairman?.usage)}</div>
            {data.chairman?.fallback && <div className="text-[#d1a553]">The chairman failed ({data.chairman.error || "no answer"}): the best-ranked answer was returned instead.</div>}
            <div className="pt-2 font-semibold text-[#8e8e9a]">Ranking by peer review (best first)</div>
            {rank.filter((r) => r.avg_rank != null).length === 0 && <div className="text-[#787884]">No ranking available.</div>}
            {rank.map((r, i) => r.avg_rank != null && (
              <div key={r.label} className="flex justify-between gap-3"><span><span className="text-[#787884]">{i + 1}.</span> <span className="mono">{r.label}</span></span>
                <span className="text-[#8e8e9a]">score {r.avg_rank.toFixed(2)} · {r.first_places}× first · {r.votes} votes</span></div>))}
          </div>)}
        {a && (
          <div>
            <div className="mb-2 text-[#8e8e9a]"><span className="mono">{a.label}</span> · {ms(a.ms)} · {tokens(a.usage)}</div>
            {a.ok ? <div className="whitespace-pre-wrap text-[#c8c8d0]">{a.text}</div> : <div className="text-red-300">Failed: {a.error}</div>}
          </div>)}
        {tab === "reviews" && (
          <div className="space-y-4">
            {rv.length === 0 && <div className="text-[#787884]">No reviews were made.</div>}
            {rv.map((r, i) => (
              <div key={i}>
                <div className="mb-1 text-[#8e8e9a]">Reviewer <span className="mono">{r.reviewer}</span> · saw {r.shown} anonymous answers · {ms(r.ms)}</div>
                {!r.ok ? <div className="text-red-300">Failed: {r.error}</div> : (<>
                  {r.ranking.length > 0 && <div className="mb-1 text-[#c8c8d0]">Ranked: {r.ranking.map((l: string, k: number) => <span key={k}>{k > 0 && " > "}<span className="mono">{l}</span></span>)}</div>}
                  {r.unparsed && <div className="mb-1 text-[#d1a553]">The ranking could not be read from this review.</div>}
                  <div className="whitespace-pre-wrap text-[#8e8e9a]">{r.text}</div></>)}
              </div>))}
          </div>)}
      </div>
    </div>
  );
}
