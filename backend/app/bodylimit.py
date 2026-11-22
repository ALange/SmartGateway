"""Request size limits as plain ASGI middleware: an oversized upload is refused with 413 before it is buffered in memory."""
import json
import os

MB = 1024 * 1024
GATEWAY_LIMIT = int(float(os.environ.get("SG_MAX_BODY_MB", "100")) * MB)   # chat bodies with base64 images/audio can be large
ADMIN_LIMIT = int(float(os.environ.get("SG_MAX_ADMIN_BODY_MB", "20")) * MB)
GATEWAY_PREFIXES = ("/gw/", "/v1/", "/unified/", "/mcp/", "/api/admin-gw/", "/api/admin-mcp/")


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = GATEWAY_LIMIT if scope["path"].startswith(GATEWAY_PREFIXES) else ADMIN_LIMIT

        async def reject():
            body = json.dumps({"error": {"message": f"Request body too large (limit {limit // MB} MB)", "type": "invalid_request_error", "code": 413}}).encode()
            await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        for k, v in scope["headers"]:
            if k == b"content-length":
                try:
                    if int(v) > limit:
                        return await reject()
                except ValueError:
                    return await reject()
        total, over, done = 0, False, False

        async def counted():
            nonlocal total, over
            if over:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > limit:   # stop feeding the app; whatever it answers is replaced by the 413 below
                    over = True
                    return {"type": "http.disconnect"}
            return msg

        async def watch(msg):
            nonlocal done
            if done:
                return
            if over:
                done = True
                return await reject()
            await send(msg)

        await self.app(scope, counted, watch)
        if over and not done:   # the app finished without answering
            await reject()
