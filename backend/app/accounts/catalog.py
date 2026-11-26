"""The models of a ChatGPT/Codex account: the account's own list when the backend answers, else the built-in one."""
import logging

from .. import discovery
from . import AccountError, codex_adapter, codex_auth

log = logging.getLogger("smartgateway.accounts")
CLIENT_VERSION = "0.99.0"


def _found(models: list) -> list:
    out = []
    for m in models:
        dm = discovery.DiscoveredModel(upstream_id=m["id"])
        dm.capabilities = set(codex_adapter.CAPS)
        dm.sources = {c: "metadata" for c in dm.capabilities}
        dm.context_length = m.get("context")
        dm.max_slots = 4
        out.append(dm)
    return out


async def discover(endpoint_id: int) -> list:
    from .. import proxy
    ids = None
    try:
        token, account_id = await codex_auth.fresh_access(endpoint_id)
        headers = {"Authorization": f"Bearer {token}", "originator": "codex_cli_rs", "User-Agent": "codex_cli_rs/" + CLIENT_VERSION}
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        r = await proxy.client.get(f"{codex_auth.CODEX_BASE}/models", params={"client_version": CLIENT_VERSION}, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("models") if isinstance(data, dict) else data
            rows = rows or (data.get("data") if isinstance(data, dict) else None) or []
            ids = [x.get("slug") or x.get("id") for x in rows if isinstance(x, dict) and (x.get("slug") or x.get("id")) and x.get("visibility", "list") != "hide"]
    except AccountError:
        raise
    except Exception as e:  # noqa: BLE001 - fall back to the built-in list
        log.info("codex model list unavailable (%s); using the built-in list", type(e).__name__)
    builtin = {m["id"]: m for m in codex_adapter.BUILTIN_MODELS}
    return _found([builtin.get(i, {"id": i, "context": 272_000}) for i in ids] if ids else codex_adapter.BUILTIN_MODELS)
