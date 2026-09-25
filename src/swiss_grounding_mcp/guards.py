"""ASGI guards for the HTTP transport: optional bearer token and per-client rate limit.
Origin/Host checks (DNS-rebinding protection) use FastMCP's built-in guard, configured in server.py."""

from __future__ import annotations

import hmac
import json
import time

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

OPEN_PATHS = ("/", "/health")  # the landing page and liveness checks stay reachable without a token


async def _reply(send: Send, status: int, message: str, extra: list[tuple[bytes, bytes]] = ()) -> None:
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *extra]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BearerAuth:
    """Require `Authorization: Bearer <token>` on every path except OPEN_PATHS."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in OPEN_PATHS:
            given = Headers(scope=scope).get("authorization", "").encode()
            if not hmac.compare_digest(given, self.expected):
                await _reply(send, 401, "Missing or wrong bearer token.", [(b"www-authenticate", b"Bearer")])
                return
        await self.app(scope, receive, send)


class RateLimit:
    """Token bucket per client IP: `per_minute` requests, refilled continuously, bursts up to the same."""

    def __init__(self, app: ASGIApp, per_minute: int) -> None:
        self.app = app
        self.capacity = float(per_minute)
        self.refill = per_minute / 60.0
        self.buckets: dict[str, tuple[float, float]] = {}

    def _take(self, client: str) -> float:
        """Take one token; return 0 if allowed, else the seconds until one is available."""
        now = time.monotonic()
        tokens, last = self.buckets.get(client, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        if tokens >= 1:
            self.buckets[client] = (tokens - 1, now)
            return 0.0
        self.buckets[client] = (tokens, now)
        return (1 - tokens) / self.refill

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            client = (scope.get("client") or ("unknown", 0))[0]
            if len(self.buckets) > 10_000:  # forget idle clients so the table cannot grow without bound
                self.buckets = {k: v for k, v in self.buckets.items() if time.monotonic() - v[1] < 120}
            if (wait := self._take(client)) > 0:
                await _reply(send, 429, "Too many requests; slow down.", [(b"retry-after", str(int(wait) + 1).encode())])
                return
        await self.app(scope, receive, send)
