"""ASGI guard: bearer auth on /mcp, unauthenticated /healthz, per-instance rate limit."""
import hashlib
import hmac
import threading
import time


def token_matches(provided: str, expected: str) -> bool:
    # Hash both so the compare is constant-time regardless of length.
    a = hashlib.sha256(provided.encode()).digest()
    b = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(a, b)


class RateLimiter:
    """Fixed-window limiter, per instance."""

    def __init__(self, per_minute: int):
        self.limit, self._lock = per_minute, threading.Lock()
        self._window, self._count = 0, 0

    def allow(self) -> bool:
        w = int(time.time() // 60)
        with self._lock:
            if w != self._window:
                self._window, self._count = w, 0
            self._count += 1
            return self._count <= self.limit


async def _respond(send, status: int, body: bytes = b"", headers=()):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-length", str(len(body)).encode()), *headers]})
    await send({"type": "http.response.body", "body": body})


class GuardMiddleware:
    def __init__(self, app, bearer_token: str, limiter: RateLimiter, fail_limiter: RateLimiter | None = None):
        if not bearer_token:
            raise RuntimeError("Bearer token must not be empty")
        self.app, self.token, self.limiter = app, bearer_token, limiter
        self.fail_limiter = fail_limiter or RateLimiter(120)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return  # fail closed: no websockets etc.
        path = scope["path"]
        if path == "/healthz":
            return await _respond(send, 200, b"ok", [(b"content-type", b"text/plain")])
        if path != "/mcp":  # exact match: no /mcp/ redirect that echoes the Host header
            return await _respond(send, 404)
        auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        scheme, _, provided = auth.partition(" ")
        if scheme.lower() != "bearer" or not provided or not token_matches(provided.strip(), self.token):
            if not self.fail_limiter.allow():  # cap unauthenticated floods too
                return await _respond(send, 429, headers=[(b"retry-after", b"60")])
            return await _respond(send, 401, headers=[(b"www-authenticate", b"Bearer")])
        if not self.limiter.allow():
            return await _respond(send, 429, headers=[(b"retry-after", b"60")])
        return await self.app(scope, receive, send)
