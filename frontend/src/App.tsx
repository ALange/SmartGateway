import { NavLink, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import Logo from "./components/Logo";
import { Activity, Users, LayoutGrid, LogOut, MessageSquare, Network, Plug, Plug2, Boxes, Minimize2, ListFilter, ScrollText, Settings as SettingsIcon } from "lucide-react";
import Settings from "./pages/Settings";
import Compression from "./pages/Compression";
import Filters from "./pages/Filters";
import { api } from "./api";
import Login from "./pages/Login";
import Playground from "./pages/Playground";
import { useLive } from "./live";
import Dashboard from "./pages/Dashboard";
import Endpoints from "./pages/Endpoints";
import Gateways from "./pages/Gateways";
import GatewayEditor from "./pages/GatewayEditor";
import CouncilEditor, { EditRoute } from "./pages/CouncilEditor";
import GatewayLive from "./pages/GatewayLive";
import UnifiedLive from "./pages/UnifiedLive";
import LiveAll from "./pages/LiveAll";
import Logs from "./pages/Logs";
import McpEndpoints from "./pages/McpEndpoints";
import McpGateways from "./pages/McpGateways";
import McpGatewayEditor from "./pages/McpGatewayEditor";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutGrid, end: true },
  { to: "/endpoints", label: "Endpoints", icon: Plug },
  { to: "/gateways", label: "Smart Gateways", icon: Network },
  { to: "/councils", label: "Council Gateways", icon: Users },
  { to: "/mcp-endpoints", label: "MCP Endpoints", icon: Plug2 },
  { to: "/mcp-gateways", label: "MCP Gateways", icon: Boxes },
  { to: "/live", label: "Live", icon: Activity },
  { to: "/playground", label: "Playground", icon: MessageSquare },
  { to: "/compression", label: "Compression", icon: Minimize2 },
  { to: "/filters", label: "Content Filters", icon: ListFilter },
  { to: "/logs", label: "Request Log", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function App() {
  const [auth, setAuth] = useState<{ auth_required: boolean; authenticated: boolean } | null>(null);
  const refresh = () => api("/api/session").then(setAuth).catch(() => setAuth({ auth_required: false, authenticated: true }));
  useEffect(() => { refresh(); const h = () => setAuth({ auth_required: true, authenticated: false }); window.addEventListener("sg-unauth", h); return () => window.removeEventListener("sg-unauth", h); }, []);
  if (!auth) return null;
  if (auth.auth_required && !auth.authenticated) return <Login onDone={refresh} />;
  return <Shell auth={auth} onLogout={async () => { await api("/api/logout", { method: "POST" }); refresh(); }} />;
}

function Shell({ auth, onLogout }: { auth: { auth_required: boolean }; onLogout: () => void }) {
  const { connected, snapshot } = useLive();
  const inflight = snapshot ? Object.values<any>(snapshot.models).reduce((a, m) => a + m.in_flight, 0) : 0;
  return (
    <div className="mx-auto flex min-h-full max-w-[1800px] gap-5 p-5">
      <aside className="sticky top-5 hidden h-[calc(100vh-40px)] w-[260px] shrink-0 flex-col gap-4 lg:flex">
        <div className="flex items-center gap-3 px-2 py-1">
          <Logo size={32} />
          <span className="text-base font-semibold">SmartGateway</span>
        </div>
        <div className="px-2 text-xs text-[#8a92a6]">
          <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: connected ? "#5fb585" : "#d9776f" }} />{connected ? "Live stream connected" : "Reconnecting…"}</div>
        </div>
        <nav className="card flex flex-col gap-0.5 p-2">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => `flex items-center gap-3 rounded-md px-3.5 py-3 text-[15px] transition ${isActive ? "bg-white/[0.07] text-white" : "text-[#9aa0c3] hover:bg-white/[0.04]"}`}>
              <n.icon size={18} /> {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex-1" />
        {auth.auth_required && <button className="btn justify-center" onClick={onLogout}><LogOut size={15} /> Sign out</button>}
        <div className="card p-4">
          <div className="flex items-center gap-2 text-sm font-semibold"><Activity size={15} className="text-[#7d9be0]" /> {inflight} in flight</div>
          <div className="mt-0.5 text-xs text-[#8a92a6]">Across all upstream slots</div>
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        <div className="mb-4 flex gap-2 overflow-x-auto lg:hidden">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `btn whitespace-nowrap ${isActive ? "primary" : ""}`}>
              <n.icon size={16} /> {n.label}
            </NavLink>
          ))}
        </div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/endpoints" element={<Endpoints />} />
          <Route path="/gateways" element={<Gateways />} />
          <Route path="/gateways/new" element={<GatewayEditor />} />
          <Route path="/councils" element={<Gateways kind="council" />} />
          <Route path="/councils/new" element={<CouncilEditor />} />
          <Route path="/gateways/:id/edit" element={<EditRoute />} />
          <Route path="/gateways/unified" element={<UnifiedLive />} />
          <Route path="/gateways/:id" element={<GatewayLive />} />
          <Route path="/mcp-endpoints" element={<McpEndpoints />} />
          <Route path="/mcp-gateways" element={<McpGateways />} />
          <Route path="/mcp-gateways/new" element={<McpGatewayEditor />} />
          <Route path="/mcp-gateways/:id/edit" element={<McpGatewayEditor />} />
          <Route path="/live" element={<LiveAll />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/compression" element={<Compression />} />
          <Route path="/filters" element={<Filters />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
