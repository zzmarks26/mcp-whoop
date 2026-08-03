"""Persistent WHOOP token store.

WHOOP rotates refresh tokens: every refresh returns a new one and invalidates
the old. Losing the newest token means redoing the browser OAuth flow, so
writes are atomic (write-then-rename) and serialised behind a lock.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


class NotAuthorisedError(RuntimeError):
    """No stored WHOOP token — the one-time OAuth bootstrap has not been done."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds

    def expired(self, leeway: int = 60) -> bool:
        return time.time() >= self.expires_at - leeway

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_response(cls, payload: dict) -> "Tokens":
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
        )


class TokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._cached: Tokens | None = None

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def load(self) -> Tokens:
        if self._cached is not None:
            return self._cached
        if not self._path.exists():
            raise NotAuthorisedError(
                "No WHOOP token stored. Run the one-time OAuth bootstrap "
                "(/oauth/start) before using this server."
            )
        data = json.loads(self._path.read_text())
        self._cached = Tokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
        )
        return self._cached

    def save(self, tokens: Tokens) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens.to_dict(), indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)  # atomic within a filesystem
        self._cached = tokens

    def exists(self) -> bool:
        return self._path.exists()
