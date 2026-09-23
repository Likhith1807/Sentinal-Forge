"""Access control, input limits and abuse protection for the dashboard API.

Modes (chosen from configuration, never from where the code happens to run):

  * LOCAL DEMO  - no `SF_API_TOKENS`, `SF_REQUIRE_AUTH` unset/false. The API answers ONLY clients connecting
    from the loopback interface. Anything else gets 403, so simply binding to 0.0.0.0 in a container does
    not silently expose an unauthenticated service.
  * TOKEN AUTH  - `SF_API_TOKENS="name:token[:role],..."` (role: analyst | viewer, default analyst) or
    `SF_REQUIRE_AUTH=1`. Every /api call except /api/health needs `Authorization: Bearer <token>`. Tokens are
    compared in constant time. Viewers can read; only analysts can create, run, approve or change schemas.

The resolved identity ("name") is what gets recorded as the analyst on decisions - the client cannot choose it.
"""
from __future__ import annotations

import hmac
import ipaddress
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from sentinelforge.service.settings import Settings, new_token

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@dataclass
class Principal:
    name: str
    role: str

    @property
    def can_write(self) -> bool:
        return self.role == "analyst"


class Security:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tokens: dict[str, tuple[str, str]] = {}
        for part in filter(None, (p.strip() for p in settings.api_tokens.split(","))):
            bits = part.split(":")
            if len(bits) >= 2:
                self.tokens[bits[1]] = (bits[0], bits[2] if len(bits) > 2 else "analyst")
        self.auth_required = bool(self.tokens) or bool(settings.require_auth)
        self.generated_token: str | None = None
        if settings.require_auth and not self.tokens:
            self.generated_token = new_token()                # printed once at start-up by the app factory
            self.tokens[self.generated_token] = ("admin", "analyst")
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "token" if self.auth_required else "local-demo"

    def authenticate(self, request: Request) -> Principal:
        if not self.auth_required:
            host = request.client.host if request.client else ""
            if host not in LOOPBACK_HOSTS and not _is_loopback(host):
                raise HTTPException(403, "This instance runs in local-demo mode and only answers loopback clients. "
                                         "Set SF_API_TOKENS to expose it.")
            if request.headers.get("x-forwarded-for"):
                raise HTTPException(403, "Forwarded requests are refused in local-demo mode. Set SF_API_TOKENS to expose it.")
            return Principal("local-analyst", "analyst")
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(401, "Missing bearer token.", headers={"WWW-Authenticate": "Bearer"})
        supplied = header[7:].strip()
        found = None
        for token, ident in self.tokens.items():
            if hmac.compare_digest(supplied.encode(), token.encode()):
                found = ident
        if not found:
            raise HTTPException(401, "Invalid token.", headers={"WWW-Authenticate": "Bearer"})
        return Principal(*found)

    def rate_limit(self, key: str) -> None:
        """Token bucket per caller: at most `rate_limit_per_minute` state-changing calls a minute."""
        limit = self.settings.rate_limit_per_minute
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._buckets.get(key, []) if now - t < 60.0]
            if len(hits) >= limit:
                raise HTTPException(429, "Too many requests; slow down.", headers={"Retry-After": "30"})
            hits.append(now)
            self._buckets[key] = hits


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
}
