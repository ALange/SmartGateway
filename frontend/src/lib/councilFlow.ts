import { api } from "../api";

/** A gateway-shaped object for the flow graph of a council: the models its roles call, tagged with the gateway that serves them.
 *  Council calls publish their live events under those gateways, not under the council itself. */
export async function loadCouncilFlow(council: any) {
  const [all, eps] = await Promise.all([api("/api/gateways?all=true"), api("/api/endpoints").catch(() => [])]);
  const c = council.council || {};
  const roles: any[] = [...(c.councillors || []), ...(Array.isArray(c.reviewers) ? c.reviewers : []), ...(c.chairman ? [c.chairman] : [])];
  const models: any[] = [], seen = new Set<string>();
  for (const r of roles) {
    if (r.model_id) {
      const hidden = all.find((g: any) => g.slug === "__council__");
      const gm = hidden?.models.find((m: any) => m.public_name === `model-${r.model_id}`);
      if (!gm || seen.has(`m${r.model_id}`)) continue;
      seen.add(`m${r.model_id}`);
      const ep = eps.find((e: any) => e.models.some((m: any) => m.id === r.model_id));
      const up = ep?.models.find((m: any) => m.id === r.model_id)?.upstream_id;
      models.push({ ...gm, gw_id: hidden.id, label: ep ? `${ep.name}/${up}` : gm.public_name });
    } else {
      const g = all.find((x: any) => x.id === r.gateway_id);
      const gm = g?.models.find((m: any) => m.public_name === r.model);
      if (!gm || seen.has(`g${g.id}:${r.model}`)) continue;
      seen.add(`g${g.id}:${r.model}`);
      models.push({ ...gm, gw_id: g.id, label: `${g.name}: ${gm.public_name}` });
    }
  }
  return { id: `council-${council.id}`, slug: council.slug, name: council.name, strategy: "least_busy", gwIds: [...new Set(models.map((m) => m.gw_id))], models };
}
