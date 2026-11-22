import { useEffect, useState } from "react";
import { Loader2, Lock } from "lucide-react";
import { api } from "../api";

export default function AdminPassword() {
  const [source, setSource] = useState<string>("");
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api("/api/session").then((s: any) => setSource(s.password_source || "")).catch(() => {}); }, []);
  if (source === "open") return null;   // login is disabled on this server, nothing to change

  const locked = source === "env";
  const problem = pw && pw.length < 10 ? "At least 10 characters" : again && again !== pw ? "Passwords do not match" : "";
  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await api("/api/admin/password", { method: "POST", json: { current: cur, new: pw } });
      setCur(""); setPw(""); setAgain("");
      setSource("ui");
      setMsg({ ok: true, text: "Password changed. Other signed-in browsers have been signed out." });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    setBusy(false);
  };

  return (
    <div className="card card-pad mt-5">
      <h3 className="flex items-center gap-2 text-base font-semibold"><Lock size={17} /> Admin password</h3>
      {locked ? (
        <p className="mt-2 text-sm text-[#8a92a6]">The password is set by the <code>SG_ADMIN_PASSWORD</code> environment variable. Change it there and restart the container; it cannot be changed from here.</p>
      ) : (
        <>
          <p className="mt-1 text-sm text-[#8a92a6]">
            {source === "generated" ? "You are using the random password generated at first start (see the container log). Choose your own below." : "Changing it signs out every other browser."}
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <div><label className="lbl">Current password</label><input type="password" autoComplete="current-password" className="input" value={cur} onChange={(e) => setCur(e.target.value)} /></div>
            <div><label className="lbl">New password</label><input type="password" autoComplete="new-password" className="input" value={pw} onChange={(e) => setPw(e.target.value)} /></div>
            <div><label className="lbl">Repeat new password</label><input type="password" autoComplete="new-password" className="input" value={again} onChange={(e) => setAgain(e.target.value)} /></div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button className="btn primary" disabled={busy || !cur || !pw || !!problem} onClick={save}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Lock size={15} />} Change password</button>
            {problem && <span className="text-sm text-[#f0a35e]">{problem}</span>}
            {msg && <span className={`text-sm ${msg.ok ? "text-[#4cc38a]" : "text-[#f06a6a]"}`}>{msg.text}</span>}
          </div>
        </>
      )}
    </div>
  );
}
