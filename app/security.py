"""Optional bearer-token protection for the REST API and MCP endpoint.

Implemented as plain ASGI middleware so MCP's streaming responses pass through untouched.
"""

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

PROTECTED_PREFIXES = ("/api", "/mcp")
PUBLIC_PATHS = {"/api/health"}


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode()

    def _requires_token(self, path: str) -> bool:
        if not self.token or path in PUBLIC_PATHS:
            return False
        return any(path == p or path.startswith(p + "/") for p in PROTECTED_PREFIXES)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, credential = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(credential.strip(), self.token)
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or not self._requires_token(scope["path"]):
            await self.app(scope, receive, send)
            return
        if self._authorized(scope):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": "Missing or invalid bearer token"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
