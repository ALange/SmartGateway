import Logo from "../components/Logo";
import { useState } from "react";
import { api } from "../api";

export default function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("admin");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await api("/api/login", { method: "POST", json: { username: u, password: p } }); onDone(); }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <form onSubmit={submit} className="card card-pad w-full max-w-sm space-y-4">
        <div className="flex items-center gap-3">
          <Logo size={32} />
          <span className="text-base font-semibold">SmartGateway</span>
        </div>
        <div className="text-sm text-[#8e8e9a]">Sign in to manage endpoints and gateways.</div>
        <div><label className="lbl">Username</label><input className="input" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" /></div>
        <div><label className="lbl">Password</label><input className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" autoFocus /></div>
        {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-2.5 text-sm text-red-300">{err}</div>}
        <button className="btn primary w-full justify-center" disabled={busy || !p}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
