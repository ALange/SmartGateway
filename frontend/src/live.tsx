import { createContext, useContext, useEffect, useRef, useState } from "react";

type Listener = (ev: any) => void;
type Live = { snapshot: any; connected: boolean; subscribe: (l: Listener) => () => void };
const Ctx = createContext<Live>({ snapshot: null, connected: false, subscribe: () => () => {} });
export const useLive = () => useContext(Ctx);

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const [snapshot, setSnapshot] = useState<any>(null);
  const [connected, setConnected] = useState(false);
  const listeners = useRef(new Set<Listener>());

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let timer: any;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); if (!stop) timer = setTimeout(connect, 1500); };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.type === "snapshot") setSnapshot(ev);
        else listeners.current.forEach((l) => l(ev));
      };
    };
    connect();
    return () => { stop = true; clearTimeout(timer); ws?.close(); };
  }, []);

  const subscribe = (l: Listener) => { listeners.current.add(l); return () => { listeners.current.delete(l); }; };
  return <Ctx.Provider value={{ snapshot, connected, subscribe }}>{children}</Ctx.Provider>;
}
