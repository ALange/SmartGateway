"""What proxy._route calls for an account endpoint: send one chat/completions request to the vendor and return a chat/completions-shaped response."""
import json

from ..router import health
from . import AccountError, codex_adapter, codex_auth

USER_AGENT = "codex_cli_rs/0.99.0 (SmartGateway)"


async def send(client, c, route: str, fwd: dict, form_files=None):
    """Raises AccountError when the account cannot serve this request (the gateway then tries the next member)."""
    if route != "chat/completions" or form_files is not None:
        raise AccountError(f"This account only serves chat completions (not {route})")
    stream = bool(fwd.get("stream"))
    body = json.dumps(codex_adapter.request(fwd))
    resp = None
    for attempt in (0, 1):
        try:
            token, account_id = await codex_auth.fresh_access(c.endpoint_id, force=bool(attempt))
        except AccountError as e:
            if e.needs_login:
                health.status[c.endpoint_id] = "offline"
            raise
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream",
                   "originator": "codex_cli_rs", "User-Agent": USER_AGENT}
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        resp = await client.send(client.build_request("POST", f"{codex_auth.CODEX_BASE}/responses", content=body, headers=headers), stream=True)
        if resp.status_code == 401 and attempt == 0:   # the token was revoked or rotated elsewhere: refresh once and retry
            await resp.aclose()
            continue
        break
    return await codex_adapter.AdaptedResponse(resp, str(fwd.get("model") or ""), stream).prepare()
