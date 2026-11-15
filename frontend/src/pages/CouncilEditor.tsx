import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus, Save, Trash2, Users } from "lucide-react";
import { api } from "../api";
import { PageHeader } from "../components/ui";
import KeysPanel from "../components/KeysPanel";
import GatewayEditor from "./GatewayEditor";

type Role = { gateway_id: number; model: string } | { model_id: number };
const REVIEW_HINT = "Placeholders: {question} {responses}. Keep the line “FINAL RANKING:” followed by “1. Response X” so rankings can be read.";
const CHAIR_HINT = "Placeholders: {question} {responses} {ranking}.";
// a role is either an endpoint model ({model_id}) or the public model of a gateway ({gateway_id, model})
const enc = (r: Role | null) => (!r ? "" : "model_id" in r ? `m::${r.model_id}` : `g::${r.gateway_id}::${r.model}`);
const dec = (v: string): Role | null => {
  if (v.startsWith("m::")) return { model_id: +v.slice(3) };
  if (!v.startsWith("g::")) return null;
  const rest = v.slice(3), i = rest.indexOf("::");
  return { gateway_id: +rest.slice(0, i), model: rest.slice(i + 2) };
};

/** Router for /gateways/:id/edit: councils get their own editor. */
export function EditRoute() {
  const { id } = useParams();
  const [kind, setKind] = useState<string | null>(null);
  useEffect(() => { api(`/api/gateways/${id}`).then((g) => setKind(g.kind || "router")).catch(() => setKind("router")); }, [id]);
  if (!kind) return null;
  return kind === "council" ? <CouncilEditor /> : <GatewayEditor />;
}

function RolePicker({ value, onChange, gws, eps, selfId, placeholder }: { value: Role | null; onChange: (r: Role | null) => void; gws: any[]; eps: any[]; selfId?: number; placeholder: string }) {
  return (
    <select className="input mono" value={enc(value)} onChange={(e) => onChange(dec(e.target.value))}>
      <option value="">{placeholder}</option>
      {eps.map((e) => {
        const ms = e.models.filter((m: any) => m.enabled !== false && !(m.capabilities || []).includes("embedding"));
        return ms.length > 0 && (
          <optgroup key={`e${e.id}`} label={`Model · ${e.name}`}>
            {ms.map((m: any) => <option key={m.id} value={`m::${m.id}`}>{m.upstream_id}</option>)}
          </optgroup>);
      })}
      {gws.filter((g) => g.id !== selfId).map((g) => (
        <optgroup key={`g${g.id}`} label={`Gateway · ${g.name}${g.kind === "council" ? " (council)" : ""}`}>
          {g.models.map((m: any) => <option key={m.id} value={`g::${g.id}::${m.public_name}`}>{m.public_name}</option>)}
        </optgroup>))}
    </select>
  );
}

export default function CouncilEditor() {
  const { id } = useParams();
  const nav = useNavigate();
  const [gws, setGws] = useState<any[]>([]);
  const [eps, setEps] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [f, setF] = useState<any>({
    name: "", slug: "", model: "council",
    councillors: [null, null, null] as (Role | null)[], chairman: null as Role | null, review_enabled: true, separate: false, reviewers: [] as (Role | null)[],
    min_answers: 2, stage_timeout_s: 120, stage_max_tokens: "", stream_progress: true, details_default: false, store_runs: false, review_prompt: "", chairman_prompt: "", max_retries: 2, queue_timeout: 60,
  });
  const [prompts, setPrompts] = useState<{ review: string; chairman: string }>({ review: "", chairman: "" });

  useEffect(() => {
    api("/api/gateways").then(setGws);
    api("/api/endpoints").then(setEps);
    api("/api/council/defaults").then((d) => setPrompts({ review: d.review_prompt, chairman: d.chairman_prompt })).catch(() => {});
    if (id) api(`/api/gateways/${id}`).then((g) => {
      const c = g.council || {};
      setF({
        name: g.name, slug: g.slug, model: g.models[0]?.public_name || "council", councillors: c.councillors || [], chairman: c.chairman || null,
        review_enabled: c.review_enabled ?? true, separate: Array.isArray(c.reviewers), reviewers: Array.isArray(c.reviewers) ? c.reviewers : [],
        min_answers: c.min_answers ?? 2, stage_timeout_s: c.stage_timeout_s ?? 120, stage_max_tokens: c.stage_max_tokens ?? "", stream_progress: !!c.stream_progress,
        details_default: !!c.details_default, store_runs: !!c.store_runs, review_prompt: c.review_prompt || "", chairman_prompt: c.chairman_prompt || "",
        max_retries: g.max_retries, queue_timeout: g.queue_timeout,
      });
    });
  }, [id]);

  const set = (patch: any) => setF((p: any) => ({ ...p, ...patch }));
  const setList = (k: string, i: number, v: Role | null) => set({ [k]: f[k].map((x: Role | null, j: number) => (j === i ? v : x)) });
  const n = f.councillors.filter(Boolean).length;
  const calls = n + (f.review_enabled ? (f.separate ? f.reviewers.filter(Boolean).length : n) : 0) + 1;
  const warnRank = f.review_enabled && !f.separate && n === 2;
  
  const save = async () => {
    setErr("");
    if (n < 2) return setErr("Pick at least two councillors.");
    if (!f.chairman) return setErr("Pick a chairman.");
    if (!f.model.trim()) return setErr("Give the council a public model name.");
    const council = {
      councillors: f.councillors.filter(Boolean), chairman: f.chairman, review_enabled: f.review_enabled,
      reviewers: f.separate ? f.reviewers.filter(Boolean) : "councillors", min_answers: +f.min_answers || 2, stage_timeout_s: +f.stage_timeout_s || 120,
      stage_max_tokens: f.stage_max_tokens ? +f.stage_max_tokens : null, stream_progress: f.stream_progress, details_default: f.details_default,
      store_runs: f.store_runs, review_prompt: f.review_prompt, chairman_prompt: f.chairman_prompt,
    };
    const body = { name: f.name, slug: f.slug || null, kind: "council", council, max_retries: +f.max_retries, queue_timeout: +f.queue_timeout, models: [{ public_name: f.model.trim(), members: [] }] };
    try {
      const g = id ? await api(`/api/gateways/${id}`, { method: "PUT", json: body }) : await api("/api/gateways", { method: "POST", json: body });
      nav(id ? "/councils" : `/gateways/${g.id}/edit`);
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="fadein">
      <PageHeader title={id ? "Edit council" : "New council"} sub="Several models answer, review each other anonymously, and a chairman writes the final answer."
        actions={<><button className="btn" onClick={() => nav(-1)}>Cancel</button><button className="btn primary" onClick={save}><Save size={16} /> Save council</button></>} />
      {err && <div className="mb-4 rounded-md border border-[#d9776f]/40 bg-[#d9776f]/10 p-3 text-sm text-[#e8a39d]">{err}</div>}
      <div className="space-y-5">
        <div className="card card-pad grid gap-4 md:grid-cols-3">
          <div><label className="lbl">Name</label><input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Research council" /></div>
          <div><label className="lbl">URL slug</label><input className="input mono" value={f.slug} onChange={(e) => set({ slug: e.target.value })} placeholder="auto" /></div>
          <div><label className="lbl">Public model name</label><input className="input mono" value={f.model} onChange={(e) => set({ model: e.target.value })} /></div>
        </div>

        <div className="card card-pad">
          <div className="mb-1 flex items-center justify-between"><h3 className="flex items-center gap-2 text-base font-semibold"><Users size={17} /> Stage 1 · Councillors</h3>
            <button className="btn" disabled={f.councillors.length >= 8} onClick={() => set({ councillors: [...f.councillors, null] })}><Plus size={15} /> Add</button></div>
          <p className="mb-3 text-sm text-[#8b91b5]">Each councillor answers the question on its own. Pick an endpoint model directly, or the public model of an existing gateway (its routing, failover, caching and guardrails then apply to the call).</p>
          <div className="space-y-2">
            {f.councillors.map((r: Role | null, i: number) => (
              <div key={i} className="flex items-center gap-2">
                <span className="w-6 text-sm text-[#6b7196]">{i + 1}</span>
                <div className="flex-1"><RolePicker value={r} onChange={(v) => setList("councillors", i, v)} gws={gws} eps={eps} selfId={id ? +id : undefined} placeholder="Choose a gateway model…" /></div>
                <button className="btn danger" onClick={() => set({ councillors: f.councillors.filter((_: any, j: number) => j !== i) })}><Trash2 size={15} /></button>
              </div>))}
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div><label className="lbl">Minimum answers needed</label><input type="number" min={1} max={8} className="input" value={f.min_answers} onChange={(e) => set({ min_answers: e.target.value })} />
              <p className="mt-1 text-xs text-[#6b7196]">If fewer councillors answer (errors, timeouts), the request fails with 502.</p></div>
            <div><label className="lbl">Timeout per call (s)</label><input type="number" min={5} max={900} className="input" value={f.stage_timeout_s} onChange={(e) => set({ stage_timeout_s: e.target.value })} /></div>
          </div>
        </div>

        <div className="card card-pad">
          <div className="mb-1 flex items-center justify-between"><h3 className="text-base font-semibold">Stage 2 · Peer review</h3>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.review_enabled} onChange={(e) => set({ review_enabled: e.target.checked })} /> Enabled</label></div>
          <p className="mb-3 text-sm text-[#8b91b5]">Each reviewer gets the other answers as “Response A, B, C…” in random order, with no model names, and ranks them by accuracy and insight. A councillor never reviews its own answer.</p>
          {f.review_enabled && (<>
            <label className="mb-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.separate} onChange={(e) => set({ separate: e.target.checked, reviewers: e.target.checked && f.reviewers.length === 0 ? [null] : f.reviewers })} /> Use different reviewers instead of the councillors</label>
            {f.separate && (
              <div className="space-y-2">
                {f.reviewers.map((r: Role | null, i: number) => (
                  <div key={i} className="flex items-center gap-2">
                    <div className="flex-1"><RolePicker value={r} onChange={(v) => setList("reviewers", i, v)} gws={gws} eps={eps} selfId={id ? +id : undefined} placeholder="Choose a gateway model…" /></div>
                    <button className="btn danger" onClick={() => set({ reviewers: f.reviewers.filter((_: any, j: number) => j !== i) })}><Trash2 size={15} /></button>
                  </div>))}
                <button className="btn" disabled={f.reviewers.length >= 8} onClick={() => set({ reviewers: [...f.reviewers, null] })}><Plus size={15} /> Add reviewer</button>
              </div>)}
            {warnRank && <p className="mt-2 text-xs text-[#d1a553]">With two councillors each reviewer sees only one answer, so nothing can be ranked. Add a third councillor or use separate reviewers.</p>}
          </>)}
        </div>

        <div className="card card-pad">
          <h3 className="mb-1 text-base font-semibold">Stage 3 · Chairman</h3>
          <p className="mb-3 text-sm text-[#8b91b5]">Gets every answer, the reviews and the ranking, and writes the one answer the client sees. If the chairman fails, the best-ranked answer is returned instead.</p>
          <RolePicker value={f.chairman} onChange={(v) => set({ chairman: v })} gws={gws} eps={eps} selfId={id ? +id : undefined} placeholder="Choose the chairman model…" />
        </div>

        <div className="card card-pad">
          <h3 className="mb-3 text-base font-semibold">Behaviour</h3>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={f.stream_progress} onChange={(e) => set({ stream_progress: e.target.checked })} />
              <span>Stream progress<span className="block text-xs text-[#6b7196]">Streaming clients receive “Stage 1: 2/3 answers in…” lines as reasoning text while the council works. Off: nothing is sent until the chairman starts.</span></span></label>
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={f.details_default} onChange={(e) => set({ details_default: e.target.checked })} />
              <span>Always include details<span className="block text-xs text-[#6b7196]">Adds an <span className="mono">sg_council</span> field (all answers, reviews, ranking) to every response. Clients can also ask per request with the header <span className="mono">X-SG-Council-Details: 1</span>.</span></span></label>
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={f.store_runs} onChange={(e) => set({ store_runs: e.target.checked })} />
              <span>Store runs<span className="block text-xs text-[#d1a553]">Keeps each question, every answer and review so you can open them from the Logs. This stores prompts and answers, which the request log never does. Set a retention period in Settings.</span></span></label>
            <div><label className="lbl">Max tokens per call (optional)</label><input type="number" min={1} className="input" value={f.stage_max_tokens} onChange={(e) => set({ stage_max_tokens: e.target.value })} placeholder="no cap" /></div>
          </div>
          <details className="mt-4"><summary className="cursor-pointer text-sm text-[#9fb0ff]">Advanced: prompts</summary>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <div><label className="lbl">Review prompt</label><textarea className="input mono text-xs" rows={10} value={f.review_prompt} onChange={(e) => set({ review_prompt: e.target.value })} placeholder={prompts.review} />
                <p className="mt-1 text-xs text-[#6b7196]">{REVIEW_HINT} Empty = default (shown greyed).</p></div>
              <div><label className="lbl">Chairman prompt</label><textarea className="input mono text-xs" rows={10} value={f.chairman_prompt} onChange={(e) => set({ chairman_prompt: e.target.value })} placeholder={prompts.chairman} />
                <p className="mt-1 text-xs text-[#6b7196]">{CHAIR_HINT} Empty = default.</p></div>
            </div>
          </details>
        </div>

        <div className="card card-pad text-sm text-[#b8bddb]">
          <div className="font-semibold">Cost and latency</div>
          <p className="mt-1">Each request makes <b>{calls}</b> model calls ({n} answers{f.review_enabled ? ` + ${f.separate ? f.reviewers.filter(Boolean).length : n} reviews` : ""} + 1 chairman), one stage after the other, so the reply takes roughly the sum of the slowest call in each stage.</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[#8b91b5]">
            <li>Guardrails (PII, content filters) run on picked gateways, once per call. Endpoint models are sent the prompt as is.</li>
            <li>Council calls skip the picked gateways' API-key checks. The council's own API keys protect the council. Endpoint models are called directly: pick a gateway instead when you want guardrails or caching on that role.</li>
            <li>Only chat is supported: no tools or JSON-mode.</li>
          </ul>
        </div>

        {id ? <KeysPanel gwId={+id} slug={f.slug} models={[f.model]} /> : <p className="text-sm text-[#8b91b5]">Save the council first to create API keys for it.</p>}
      </div>
    </div>
  );
}
