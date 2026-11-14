import { useEffect, useMemo, useRef, useState } from "react";
import { useLive } from "../live";

const W = 1000, NW = 200, NH = 58;
const COLX = { client: 20, gw: 250, pm: 500, up: 780 };
type P = { x: number; y: number };

const curve = (a: P, b: P) => { const dx = (b.x - a.x) * 0.5; return `C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`; };
const pathOf = (pts: P[]) => `M ${pts[0].x} ${pts[0].y} ` + pts.slice(1).map((p, i) => curve(pts[i], p)).join(" ");

export default function FlowGraph({ gw, onEvent }: { gw: any; onEvent?: (ev: any) => void }) {
  const [parts, setParts] = useState<any[]>([]);
  const counts = useRef<Record<string, number[]>>({});
  const lastSpawn = useRef(0);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const { snapshot, subscribe } = useLive();
  const [, force] = useState(0);

  // a unified graph holds the models of several gateways: tell same-named models apart by their gateway
  const pmOf = (ev: any) => (m: any) => m.public_name === ev.public_model && (m.gw_id == null || String(m.gw_id) === String(ev.gateway_id));
  const layout = useMemo(() => {
    if (!gw) return null;
    const ups: any[] = []; const seen = new Set<number>();
    gw.models.forEach((m: any) => m.members.forEach((x: any) => { if (!seen.has(x.model_id)) { seen.add(x.model_id); ups.push(x); } }));
    const rows = Math.max(ups.length, gw.models.length, 1);
    const H = Math.max(360, rows * 86 + 60);
    const place = (n: number, i: number) => (H / (n + 1)) * (i + 1);
    const pms = gw.models.map((m: any, i: number) => ({ ...m, y: place(gw.models.length, i) }));
    const upN = ups.map((u, i) => ({ ...u, y: place(ups.length, i) }));
    return { H, pms, ups: upN, client: { y: H / 2 }, gwY: H / 2 };
  }, [gw]);

  useEffect(() => {
    if (!layout) return;
    return subscribe((ev) => {
      if (gw.gwIds ? !gw.gwIds.map(String).includes(String(ev.gateway_id)) : String(ev.gateway_id) !== String(gw.id)) return;
      onEventRef.current?.(ev);
      const now = Date.now();
      const bump = (k: string) => { (counts.current[k] ||= []).push(now); };
      if (ev.type === "start") {
        const pm = layout.pms.find(pmOf(ev));
        const up = layout.ups.find((u: any) => u.model_id === ev.model_id);
        if (!pm || !up) return;
        const pts: P[] = [{ x: COLX.client + NW, y: layout.client.y }, { x: COLX.gw, y: layout.gwY }, { x: COLX.gw + NW, y: layout.gwY }, { x: COLX.pm, y: pm.y }, { x: COLX.pm + NW, y: pm.y }, { x: COLX.up, y: up.y }];
        const d = pathOf([pts[0], pts[1]]) + " L " + pts[2].x + " " + pts[2].y + " " + curve(pts[2], pts[3]) + " L " + pts[4].x + " " + pts[4].y + " " + curve(pts[4], pts[5]);
        bump("c-g"); bump(`g-p-${pm.id}`); bump(`p-u-${pm.id}-${up.model_id}`);
        if (now - lastSpawn.current >= 40) { lastSpawn.current = now; setParts((p) => [...p.slice(-60), { k: ev.id + "s" + ev.attempt, d, color: "#7d9be0", dur: 1.6, r: 5 }]); }  // at most ~25 dots/s so heavy load (stress tests) stays smooth
      } else if (ev.type === "end") {
        const up = layout.ups.find((u: any) => u.model_id === ev.model_id);
        if (up && !ev.ok) setParts((p) => [...p.slice(-60), { k: ev.id + "e", d: `M ${COLX.up} ${up.y} l -30 0`, color: "#d9776f", dur: 0.8, r: 7 }]);
        if (up && ev.ok) {
          const d = `M ${COLX.up} ${up.y} ` + curve({ x: COLX.up, y: up.y }, { x: COLX.pm + NW, y: layout.pms.find(pmOf(ev))?.y ?? layout.gwY });
          if (now - lastSpawn.current >= 40) { lastSpawn.current = now; setParts((p) => [...p.slice(-60), { k: ev.id + "r", d, color: "#5fb585", dur: 0.9, r: 3.5 }]); }
        }
      }
    });
  }, [layout, gw.id, gw.gwIds, subscribe]);

  useEffect(() => { const t = setInterval(() => force((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { if (parts.length) { const t = setTimeout(() => setParts((p) => p.slice(1)), 1800); return () => clearTimeout(t); } }, [parts]);

  if (!layout) return null;
  const now = Date.now();
  const rate = (k: string) => (counts.current[k] = (counts.current[k] || []).filter((t) => now - t < 20000)).length;
  const width = (k: string) => 1.5 + Math.min(7, rate(k) * 0.7);
  const qd = gw.gwIds ? gw.gwIds.reduce((a: number, i: number) => a + (snapshot?.queue?.[i] || 0), 0) : snapshot?.queue?.[gw.id] || 0;
  const totalIn = layout.ups.reduce((a: number, u: any) => a + (snapshot?.models?.[u.model_id]?.in_flight || 0), 0);

  const Node = ({ x, y, title, sub, accent, children }: any) => (
    <g transform={`translate(${x},${y - NH / 2})`}>
      <rect width={NW} height={NH} rx={8} fill="#12151c" stroke={accent || "rgba(255,255,255,0.1)"} strokeWidth={1.2} />
      <text x={14} y={24} fill="#e6e8f5" fontSize={13} fontWeight={600} className="mono">{title.length > 22 ? title.slice(0, 21) + "…" : title}</text>
      <text x={14} y={42} fill="#8b91b5" fontSize={11}>{sub}</text>
      {children}
    </g>
  );

  return (
        <div className="card self-start overflow-hidden p-3">
          <svg viewBox={`0 0 ${W} ${layout.H}`} className="w-full">
            <defs><filter id="glow"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
              <linearGradient id="edge" x1="0" x2="1"><stop offset="0%" stopColor="#5b7fc4" /><stop offset="100%" stopColor="#5b7fc4" /></linearGradient></defs>
            {[["Clients", COLX.client], ["Gateway", COLX.gw], ["Public models", COLX.pm], ["Upstream models", COLX.up]].map(([l, x]: any) => <text key={l} x={x} y={18} fill="#5b6086" fontSize={10} letterSpacing="2" style={{ textTransform: "uppercase" }}>{l}</text>)}
            {/* edges */}
            <path d={`M ${COLX.client + NW} ${layout.client.y} ${curve({ x: COLX.client + NW, y: layout.client.y }, { x: COLX.gw, y: layout.gwY })}`} stroke="#5b7fc4" strokeOpacity={0.35} strokeWidth={width("c-g")} fill="none" />
            {layout.pms.map((pm: any) => <path key={pm.id} d={`M ${COLX.gw + NW} ${layout.gwY} ${curve({ x: COLX.gw + NW, y: layout.gwY }, { x: COLX.pm, y: pm.y })}`} stroke="#5b7fc4" strokeOpacity={0.35} strokeWidth={width(`g-p-${pm.id}`)} fill="none" />)}
            {layout.pms.flatMap((pm: any) => pm.members.map((x: any) => {
              const up = layout.ups.find((u: any) => u.model_id === x.model_id);
              const live = snapshot?.models?.[x.model_id];
              const dead = live?.breaker === "open";
              return <path key={`${pm.id}-${x.id}`} d={`M ${COLX.pm + NW} ${pm.y} ${curve({ x: COLX.pm + NW, y: pm.y }, { x: COLX.up, y: up.y })}`}
                stroke={dead ? "#d9776f" : "#5b7fc4"} strokeOpacity={dead ? 0.5 : 0.35} strokeDasharray={dead ? "5 5" : undefined} strokeWidth={width(`p-u-${pm.id}-${x.model_id}`)} fill="none" />;
            }))}
            {/* nodes */}
            <Node x={COLX.client} y={layout.client.y} title="Clients" sub={`${totalIn} in flight`} accent="#ffffff22" />
            <Node x={COLX.gw} y={layout.gwY} title={gw.slug} sub={qd ? `${qd} queued` : "routing"} accent={qd ? "#d1a553" : "#5b7fc4"}>
              {qd > 0 && <g transform={`translate(${NW - 52},12)`}><rect width={42} height={20} rx={10} fill="#d1a55333" /><text x={21} y={14} textAnchor="middle" fill="#d1a553" fontSize={11}>{qd}</text></g>}
            </Node>
            {layout.pms.map((pm: any) => <Node key={pm.id} x={COLX.pm} y={pm.y} title={pm.label || pm.public_name} sub={`${pm.members.length} upstream · ${pm.strategy || gw.strategy}`} accent="#ffffff30" />)}
            {layout.ups.map((u: any) => {
              const live = snapshot?.models?.[u.model_id] || { in_flight: 0, max_slots: u.max_slots };
              const st = snapshot?.endpoints?.[u.endpoint_id]?.status;
              const col = st === "offline" || live.breaker === "open" ? "#d9776f" : live.in_flight + (live.external || 0) >= live.max_slots ? "#d1a553" : "#5fb585";
              const pct = Math.min(1, live.in_flight / Math.max(1, live.max_slots));
              return <Node key={u.model_id} x={COLX.up} y={u.y} title={u.upstream_id} sub={`@ ${u.endpoint_name}`} accent={col + "99"}>
                <circle cx={NW - 16} cy={16} r={4} fill={col} />
                <text x={NW - 14} y={43} textAnchor="end" fill="#b8bddb" fontSize={11}>{live.in_flight}{live.external ? `+${live.external}` : ""}/{live.max_slots}</text>
                <rect x={14} y={NH - 8} width={NW - 70} height={4} rx={2} fill="rgba(255,255,255,0.07)" />
                <rect x={14} y={NH - 8} width={(NW - 70) * pct} height={4} rx={2} fill={col} style={{ transition: "width .4s" }} />
                <rect x={14 + (NW - 70) * pct} y={NH - 8} width={(NW - 70) * Math.min(1 - pct, (live.external || 0) / Math.max(1, live.max_slots))} height={4} fill="#8a92a6" style={{ transition: "all .4s" }} />
                {(u.capabilities || []).slice(0, 4).map((c: string, i: number) => <circle key={c} cx={NW - 16 - i * 0} cy={0} r={0} />)}
              </Node>;
            })}
            {/* particles */}
            {parts.map((p) => (
              <circle key={p.k} r={p.r} fill={p.color}>
                <animateMotion dur={`${p.dur}s`} path={p.d} fill="freeze" />
                <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.1;0.85;1" dur={`${p.dur}s`} fill="freeze" />
              </circle>
            ))}
          </svg>
          <div className="flex flex-wrap gap-4 px-4 pb-3 text-xs text-[#8b91b5]">
            <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-[#7d9be0]" />request</span>
            <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-[#5fb585]" />response</span>
            <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-[#d9776f]" />error / breaker open</span>
            <span>Line width = recent throughput · bar = slot usage</span>
          </div>
        </div>
  );
}
