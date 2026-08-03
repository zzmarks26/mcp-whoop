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
    """Cache backed by the file, not a substitute for it.

    Because WHOOP invalidates a refresh token as soon as it is used, an
    in-memory copy goes permanently stale the moment anything else rotates
    the token — another process, a bootstrap re-run, a restored backup. The
    cache is therefore keyed on the file's mtime and dropped when it moves.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._cached: Tokens | None = None
        self._cached_mtime: float | None = None

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def _mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return None

    def load(self) -> Tokens:
        mtime = self._mtime()
        if mtime is None:
            raise NotAuthorisedError(
                "No WHOOP token stored. Run the one-time OAuth bootstrap "
                "(/oauth/start) before using this server."
            )
        if self._cached is not None and mtime == self._cached_mtime:
            return self._cached

        data = json.loads(self._path.read_text())
        self._cached = Tokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
        )
        self._cached_mtime = mtime
        return self._cached

    def save(self, tokens: Tokens) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens.to_dict(), indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)  # atomic within a filesystem
        self._cached = tokens
        self._cached_mtime = self._mtime()

    def reload(self) -> Tokens:
        """Force a read from disk, discarding whatever is cached."""
        self._cached = None
        self._cached_mtime = None
        return self.load()

    def exists(self) -> bool:
        return self._path.exists()
