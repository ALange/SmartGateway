import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, ShieldAlert } from "lucide-react";
import { api } from "../api";
import { Modal } from "./ui";

type Start = { session_id: string; user_code: string; verification_url: string; interval: number };

/** Sign in with a ChatGPT / Codex subscription (OpenAI's device-code flow): show the code, wait until it is approved in the browser. */
export default function OAuthAccountDialog({ endpointId, onClose, onDone }: { endpointId?: number; onClose: () => void; onDone: () => void }) {
  const [step, setStep] = useState<"notice" | "code" | "done">("notice");
  const [start, setStart] = useState<Start | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const begin = async () => {
    setBusy(true); setErr("");
    try {
      const s: Start = await api("/api/accounts/codex/start", { method: "POST", json: { endpoint_id: endpointId ?? null } });
      setStart(s); setStep("code");
      poll(s);
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  const poll = (s: Start) => {
    const tick = async () => {
      if (!alive.current) return;
      try {
        const r = await api("/api/accounts/codex/poll", { method: "POST", json: { session_id: s.session_id } });
        if (!alive.current) return;
        if (r.status === "done") { setStep("done"); onDone(); setTimeout(onClose, 1200); return; }
      } catch (e: any) { if (alive.current) { setErr(e.message); setStep("notice"); } return; }
      setTimeout(tick, s.interval * 1000);
    };
    setTimeout(tick, s.interval * 1000);
  };

  return (
    <Modal title={endpointId ? "Sign in again" : "Add ChatGPT / Codex account"} onClose={onClose}>
      <div className="space-y-4">
        {step === "notice" && <>
          <div className="flex gap-3 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-200">
            <ShieldAlert size={18} className="mt-0.5 shrink-0" />
            <div>
              This uses your ChatGPT subscription through the same sign-in as the official Codex CLI. Use it for your own work only.
              OpenAI does not offer this as a public API, so it may stop working or be limited at any time, and your plan's own usage limits apply.
              Your tokens stay on this server, encrypted.
            </div>
          </div>
          <p className="text-sm text-[#8e8e9a]">You will get a short code and approve it at OpenAI in your browser. Nothing needs to be installed.</p>
        </>}
        {step === "code" && start && <>
          <p className="text-sm text-[#c8c8d0]">1. Open the OpenAI sign-in page and enter this code:</p>
          <div className="rounded-md border border-white/10 bg-white/[0.03] p-4 text-center">
            <div className="mono text-3xl font-semibold tracking-widest" data-testid="user-code">{start.user_code}</div>
          </div>
          <a className="btn w-full justify-center" href={start.verification_url} target="_blank" rel="noreferrer noopener"><ExternalLink size={15} /> Open {start.verification_url.replace("https://", "")}</a>
          <p className="flex items-center gap-2 text-sm text-[#8e8e9a]"><Loader2 size={15} className="animate-spin" /> 2. Waiting for you to approve it… this window updates by itself.</p>
        </>}
        {step === "done" && <div className="rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-200">Signed in. The account is ready to use.</div>}
        {err && <div className="rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-300">{err}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onClose}>{step === "done" ? "Close" : "Cancel"}</button>
          {step === "notice" && <button className="btn primary" disabled={busy} onClick={begin}>{busy ? <><Loader2 size={16} className="animate-spin" /> Starting…</> : "I understand, start sign-in"}</button>}
        </div>
      </div>
    </Modal>
  );
}
