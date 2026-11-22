import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2, Play } from "lucide-react";
import { Link } from "react-router-dom";
import { api, PALETTE } from "../api";
import { fmt, PageHeader } from "../components/ui";

const RANGES: [string, string][] = [["1h", "1 hour"], ["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]];
const TABS = ["Savings", "Cache alignment", "Context", "Try it"];
const tip = { contentStyle: { background: "#181c25", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }, cursor: { fill: "rgba(255,255,255,0.04)" } };
const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);

function Tile({ label, value, sub }: { label: string; value: any; sub?: string }) {
  return <div className="card card-pad"><div className="text-sm text-[#8a92a6]">{label}</div><div className="mt-1.5 text-2xl font-semibold">{value}</div>{sub && <div className="mt-1 text-xs text-[#6b7196]">{sub}</div>}</div>;
}

function Breakdown({ title, rows }: { title: string; rows: any[] }) {
  return (
    <div className="card card-pad">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {rows.length === 0 ? <p className="text-sm text-[#6b7196]">No data for this period</p> : (
        <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">{["", "Events", "Avg ratio", "Tokens saved", "Cache hit rate"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.key} className="border-t border-white/5"><td className="mono py-2 pr-3">{r.key}</td><td>{fmt(r.events)}</td><td>{pct(r.avgRatio)}</td><td>{fmt(r.tokensSaved)}</td><td>{pct(r.cacheHitRate)}</td></tr>)}</tbody></table>)}
    </div>
  );
}

function Efficiency({ d }: { d: any }) {
  const t = d.totals;
  return (
    <div className="space-y-5">
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <Tile label="Tokens not sent" value={fmt(t.tokensNotSent)} sub="Prompt tokens removed, counted on every turn they would have been resent" />
        <Tile label="Unique tokens saved" value={fmt(t.uniqueTokensSaved)} sub="Each tool result counted once, when first compressed" />
        <Tile label="Compression events" value={fmt(t.compressionEventsCount)} sub={`${fmt(t.appliedEvents)} applied · ${fmt(t.shadowEvents)} shadow`} />
        <Tile label="Cache hit rate" value={pct(t.cacheHitRate)} sub="Blocks served from the compression cache" />
        <Tile label="Avg compression ratio" value={pct(t.avgCompressionRatio)} sub="Compressed size / original (lower is better)" />
        <Tile label="Shadow: would have saved" value={fmt(t.potentialTokensNotSent)} sub="Measured in shadow mode, not applied" />
        <Tile label="Requests" value={fmt(t.requests)} />
        <Tile label="Compression cache" value={d.liveCache ? `${fmt(d.liveCache.entries)} entries` : "–"} sub={d.liveCache ? `${fmt(d.liveCache.hits)} hits · ${fmt(d.liveCache.misses)} misses (since start)` : ""} />
      </div>
      <div className="grid gap-5 xl:grid-cols-2"><Breakdown title="By content type" rows={d.byContentType} /><Breakdown title="By compressor" rows={d.byCompressor} /></div>
      {t.compressionEventsCount === 0 && <p className="text-sm text-[#8a92a6]">No compression activity yet. Turn on Shadow or On mode in Settings → Compression, or per model in a gateway's settings.</p>}
    </div>
  );
}

function Alignment({ d }: { d: any }) {
  const a = d.alignment;
  const bars = Object.entries(a.scoreBuckets).map(([k, v]) => ({ k, v }));
  const labels = Object.entries(a.volatileLabels).sort((x: any, y: any) => y[1] - x[1]);
  const score = a.avgScore;
  const color = score == null ? "#8a92a6" : score >= 90 ? "#5fb585" : score >= 70 ? "#d1a553" : "#d9776f";
  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-[#8a92a6]">Volatile text in a system prompt (timestamps, UUIDs, tokens) changes on every request, so servers cannot reuse their cached prompt prefix. The score drops 10 points per finding. Detection only; nothing is rewritten.</p>
      <div className="grid gap-5 md:grid-cols-3">
        <Tile label="Average alignment score" value={<span style={{ color }}>{score == null ? "–" : score.toFixed(0)}</span>} sub="100 = fully stable system prompts" />
        <Tile label="Requests with volatile content" value={fmt(a.requestsWithFindings)} />
        <Tile label="Requests analysed" value={fmt(Object.values(a.scoreBuckets).reduce((x: number, y: any) => x + y, 0))} />
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <div className="card card-pad"><h3 className="mb-3 text-sm font-semibold">Score distribution</h3>
          <div className="h-[220px]"><ResponsiveContainer><BarChart data={bars}><CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} /><XAxis dataKey="k" tick={{ fill: "#6b7196", fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#6b7196", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} width={30} /><Tooltip {...tip} /><Bar dataKey="v" name="requests" fill="#3b6fd8" radius={[2, 2, 0, 0]} /></BarChart></ResponsiveContainer></div></div>
        <div className="card card-pad"><h3 className="mb-3 text-sm font-semibold">What was found</h3>
          {labels.length === 0 ? <p className="text-sm text-[#6b7196]">Nothing volatile detected.</p> : <div className="space-y-2">{labels.map(([k, v]: any, i) => <div key={k} className="flex items-center justify-between text-sm"><span className="flex items-center gap-2"><i className="h-2 w-2 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} /><span className="mono">{k}</span></span><span>{fmt(v)}</span></div>)}</div>}</div>
      </div>
      <div className="card card-pad"><h3 className="mb-1 text-sm font-semibold">System prompt variants</h3><p className="mb-3 text-xs text-[#6b7196]">Distinct system prompts seen per model. One is ideal; many means the prefix keeps changing.</p>
        {a.promptVariants.length === 0 ? <p className="text-sm text-[#6b7196]">No data.</p> : <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]"><th className="pb-2 font-medium">Model</th><th className="pb-2 font-medium">Variants</th><th className="pb-2 font-medium">Requests</th></tr></thead>
          <tbody>{a.promptVariants.map((p: any) => <tr key={p.public_model} className="border-t border-white/5"><td className="mono py-2">{p.public_model}</td><td className={p.variants > 1 ? "text-[#d1a553]" : ""}>{p.variants}</td><td>{fmt(p.requests)}</td></tr>)}</tbody></table>}</div>
    </div>
  );
}

function Context({ d }: { d: any }) {
  const [sel, setSel] = useState(0);
  const c = d.composition;
  const parts: [string, string, number, number][] = [["System prompt", "#8a8fc4", c.shares.system, c.avgPerRequest.system], ["Tool definitions", "#5fa3b5", c.shares.tools, c.avgPerRequest.tools], ["Messages & tool results", "#3b6fd8", c.shares.messages, c.avgPerRequest.messages]];
  const sess = d.growth[sel];
  const pts = (sess?.points || []).map((p: any, i: number) => ({ n: i + 1, prompt: p.promptTokens, completion: p.completionTokens }));
  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-[#8a92a6]">{d.meta.estimateNote} Based on the {fmt(d.meta.requestsAnalyzed)} most recent analysed requests{d.meta.truncated ? " (scan limit reached)" : ""}.</p>
      <div className="card card-pad"><h3 className="mb-3 text-sm font-semibold">What fills the context</h3>
        {c.totalChars === 0 ? <p className="text-sm text-[#6b7196]">No analysed requests in this period.</p> : (<>
          <div className="flex h-4 overflow-hidden rounded-full bg-white/5">{parts.map(([k, col, sh]) => <div key={k} title={`${k} ${pct(sh)}`} style={{ width: `${sh * 100}%`, background: col }} />)}</div>
          <div className="mt-3 grid gap-3 md:grid-cols-3">{parts.map(([k, col, sh, avg]) => <div key={k} className="text-sm"><span className="flex items-center gap-2"><i className="h-2 w-2 rounded-full" style={{ background: col }} />{k}</span><div className="mt-0.5 text-lg font-semibold">{pct(sh)}</div><div className="text-xs text-[#6b7196]">avg {fmt(avg)} chars / request (~{fmt(avg / 4)} tokens)</div></div>)}</div></>)}
        <div className="mt-4 grid gap-3 text-sm md:grid-cols-3"><div>Requests: <b>{fmt(d.tokenTotals.requests)}</b></div><div>Prompt tokens (exact): <b>{fmt(d.tokenTotals.prompt)}</b></div><div>Completion tokens (exact): <b>{fmt(d.tokenTotals.completion)}</b></div></div>
      </div>
      <div className="card card-pad"><h3 className="mb-1 text-sm font-semibold">Top contributors</h3><p className="mb-3 text-xs text-[#6b7196]">Large blocks that keep being resent, grouped by content hash. “Total” is size × times seen.</p>
        {d.contributors.length === 0 ? <p className="text-sm text-[#6b7196]">None yet (blocks under 512 characters are ignored).</p> : <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wider text-[#6b7196]">{["Kind", "Label", "Size", "Seen", "Total (chars)"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>{d.contributors.map((x: any) => <tr key={x.kind + x.label + x.chars} className="border-t border-white/5"><td className="py-2 pr-3"><span className="chip text-[#8a92a6]" style={{ background: "#ffffff0d" }}>{x.kind}</span></td><td className="mono max-w-[360px] truncate pr-3">{x.label}</td><td>{fmt(x.chars)}</td><td>{x.occurrences}×</td><td>{fmt(x.totalChars)}</td></tr>)}</tbody></table></div>}</div>
      <div className="card card-pad"><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-semibold">Context growth per session</h3>
        {d.growth.length > 0 && <select className="input !w-auto" value={sel} onChange={(e) => setSel(+e.target.value)}>{d.growth.map((g: any, i: number) => <option key={i} value={i}>{g.sessionKey} · {g.requests} requests · {new Date(g.end * 1000).toLocaleString()}</option>)}</select>}</div>
        {pts.length === 0 ? <p className="text-sm text-[#6b7196]">No sessions yet. Sessions are grouped by the X-SG-Session header, the OpenAI “user” field, or the first system+user message.</p> :
          <div className="h-[260px]"><ResponsiveContainer><LineChart data={pts}><CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} /><XAxis dataKey="n" tick={{ fill: "#6b7196", fontSize: 11 }} axisLine={false} tickLine={false} label={{ value: "request #", fill: "#6b7196", fontSize: 11, position: "insideBottom", offset: -2 }} /><YAxis tick={{ fill: "#6b7196", fontSize: 11 }} axisLine={false} tickLine={false} width={44} /><Tooltip {...tip} /><Line type="monotone" dataKey="prompt" name="prompt tokens" stroke="#3b6fd8" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="completion" name="completion tokens" stroke="#5fb585" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div>}</div>
    </div>
  );
}

const SAMPLES: Record<string, () => string> = {
  JSON: () => JSON.stringify(Array.from({ length: 120 }, (_, i) => ({ id: i, name: `user-${i}`, email: `user${i}@example.com`, bio: "lorem ipsum dolor sit amet ".repeat(5) })), null, 1),
  Log: () => Array.from({ length: 400 }, (_, i) => `2024-05-01 10:${String(Math.floor(i / 60) % 60).padStart(2, "0")}:${String(i % 60).padStart(2, "0")} INFO worker-${i % 3} processed batch ${i}${i % 150 === 0 ? " ERROR timeout talking to db" : ""}`).join("\n"),
  "Search results": () => Array.from({ length: 12 }, (_, f) => Array.from({ length: 25 }, (_, n) => `src/module${f}/handler.py:${n + 1}:    value = compute_something(${n}, ${f})  # TODO refactor`).join("\n")).join("\n"),
  Text: () => Array.from({ length: 80 }, (_, i) => `Sentence number ${i} explains how the build pipeline works in detail.`).join(" "),
};

function TryIt() {
  const [text, setText] = useState("");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = async () => { setBusy(true); setErr(""); try { setRes(await api("/api/compression/preview", { method: "POST", json: { text } })); } catch (e: any) { setErr(e.message); setRes(null); } setBusy(false); };
  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-[#8a92a6]">Paste a tool output to see how it would be classified and compressed with your current compression settings. Nothing is stored or sent anywhere.</p>
      <div className="card card-pad">
        <div className="mb-3 flex flex-wrap items-center gap-2"><span className="text-xs text-[#6b7196]">Load a sample:</span>{Object.keys(SAMPLES).map((k) => <button key={k} className="btn !py-1" onClick={() => { setText(SAMPLES[k]()); setRes(null); }}>{k}</button>)}</div>
        <textarea className="input mono" rows={9} placeholder="Paste JSON, a log, grep output, a diff or plain text…" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="mt-3 flex items-center justify-between"><span className="text-xs text-[#6b7196]">{text.length.toLocaleString()} characters (~{Math.ceil(text.length / 4).toLocaleString()} tokens)</span>
          <button className="btn primary" disabled={!text || busy} onClick={run}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Compress</button></div>
        {err && <div className="mt-3 rounded-md border border-red-400/30 bg-red-400/10 p-2.5 text-sm text-red-300">{err}</div>}
      </div>
      {res && (
        <div className="space-y-5">
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
            <Tile label="Detected as" value={<span className="mono text-xl">{res.detected.content_type}{res.detected.language ? ` · ${res.detected.language}` : ""}</span>} sub={`confidence ${Math.round(res.detected.confidence * 100)}%${res.was_mixed ? " · mixed content" : ""}`} />
            <Tile label="Compressor" value={<span className="mono text-xl">{res.compressor_used}</span>} sub={res.strategy} />
            <Tile label="Tokens" value={`${fmt(res.tokens_before)} → ${fmt(res.tokens_after)}`} sub={`saves ${fmt(res.tokens_saved)}`} />
            <Tile label="Size ratio" value={pct(res.compression_ratio)} sub={`${res.chars_before.toLocaleString()} → ${res.chars_after.toLocaleString()} chars`} />
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <div className="card card-pad"><h3 className="mb-2 text-sm font-semibold">Original</h3><pre className="mono scroll-thin max-h-[420px] overflow-auto whitespace-pre-wrap break-all rounded-md bg-black/30 p-3 text-xs">{text.length > 6000 ? text.slice(0, 6000) + `\n… (${(text.length - 6000).toLocaleString()} more characters not shown)` : text}</pre></div>
            <div className="card card-pad"><h3 className="mb-2 text-sm font-semibold">Compressed</h3><pre className="mono scroll-thin max-h-[420px] overflow-auto whitespace-pre-wrap break-all rounded-md bg-black/30 p-3 text-xs">{res.compressed}</pre></div>
          </div>
        </div>)}
    </div>
  );
}

export default function Compression() {
  const [tab, setTab] = useState(0);
  const [range, setRange] = useState("24h");
  const [gw, setGw] = useState("");
  const [gws, setGws] = useState<any[]>([]);
  const [eff, setEff] = useState<any>(null);
  const [ctx, setCtx] = useState<any>(null);
  useEffect(() => { api("/api/gateways").then(setGws); }, []);
  useEffect(() => {
    if (tab === 3) return;
    const q = `range=${range}${gw ? `&gateway_id=${gw}` : ""}`;
    const load = () => { api(`/api/insights/compression?${q}`).then(setEff); api(`/api/insights/context?${q}`).then(setCtx); };
    load(); const t = setInterval(load, 6000); return () => clearInterval(t);
  }, [range, gw, tab]);
  return (
    <div className="fadein">
      <PageHeader title="Compression" sub="How much context compression saves, how stable your prompts are, and what fills the context."
        actions={tab !== 3 ? <>
          <Link to="/settings?tab=compression" className="btn" title="Global compression mode and thresholds">Defaults</Link>
          <select className="input !w-auto" value={gw} onChange={(e) => setGw(e.target.value)}><option value="">All gateways</option>{gws.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</select>
          <div className="flex gap-1 rounded-lg border border-white/[0.08] bg-white/[0.03] p-1">{RANGES.map(([k, l]) => <button key={k} onClick={() => setRange(k)} className={`rounded-md px-3 py-1 text-sm ${range === k ? "bg-white/10 text-white" : "text-[#8a92a6]"}`}>{l}</button>)}</div></> : undefined} />
      <div className="mb-5 flex gap-1 border-b border-white/[0.08]">{TABS.map((t, i) => <button key={t} onClick={() => setTab(i)} className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === i ? "border-[#3b6fd8] text-white" : "border-transparent text-[#8a92a6] hover:text-white"}`}>{t}</button>)}</div>
      {tab === 0 && eff && <Efficiency d={eff} />}
      {tab === 1 && eff && <Alignment d={eff} />}
      {tab === 2 && ctx && <Context d={ctx} key={`${range}${gw}`} />}
      {tab === 3 && <TryIt />}
    </div>
  );
}
