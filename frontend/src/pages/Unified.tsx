import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Activity, FlaskConical, Plus } from "lucide-react";
import { PageHeader } from "../components/ui";
import TestGateway from "../components/TestGateway";
import UnifiedEndpoint from "../components/UnifiedEndpoint";
import { GATEWAY_TABS, LinkTabs } from "../components/Tabs";

/** Third tab of Gateways: set up the single OpenAI/Anthropic-compatible URL that fronts several gateways. */
export default function Unified() {
  const nav = useNavigate();
  const [uni, setUni] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const names: string[] = uni?.model_names || [];
  const testGw = { unified: true, name: "Unified endpoint", models: names.map((n) => ({ id: n, public_name: n, capabilities: [], members: [] })) };
  return (
    <div className="fadein">
      <PageHeader title="Gateways" sub="One URL in front of several gateways; clients pick a gateway or model by name."
        actions={<>
          <button className="btn" onClick={() => setTesting(true)} disabled={names.length === 0}><FlaskConical size={15} /> Test</button>
          <Link to="/gateways/unified" className="btn"><Activity size={15} /> Live</Link>
          <button className="btn primary" onClick={() => nav("/gateways/new")}><Plus size={16} /> New gateway</button></>} />
      <LinkTabs items={GATEWAY_TABS} />
      <UnifiedEndpoint onChange={setUni} />
      {testing && <TestGateway gw={testGw} onClose={() => setTesting(false)} />}
    </div>
  );
}
