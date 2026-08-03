"""Thin async client for the WHOOP v2 API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .settings import Settings
from .tokens import NotAuthorisedError, TokenStore, Tokens

logger = logging.getLogger(__name__)

API_BASE = "https://api.prod.whoop.com/developer"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"

# `offline` is what makes WHOOP return a refresh token at all.
SCOPES = [
    "offline",
    "read:profile",
    "read:body_measurement",
    "read:cycles",
    "read:recovery",
    "read:sleep",
    "read:workout",
]


class WhoopError(RuntimeError):
    pass


class WhoopClient:
    def __init__(self, settings: Settings, store: TokenStore) -> None:
        self._settings = settings
        self._store = store
        self._http = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- OAuth ----------

    def authorize_url(self, state: str) -> str:
        params = httpx.QueryParams(
            client_id=self._settings.whoop_client_id,
            redirect_uri=self._settings.whoop_redirect_uri,
            response_type="code",
            scope=" ".join(SCOPES),
            state=state,
        )
        return f"{AUTH_URL}?{params}"

    async def exchange_code(self, code: str) -> Tokens:
        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._settings.whoop_client_id,
                "client_secret": self._settings.whoop_client_secret,
                "redirect_uri": self._settings.whoop_redirect_uri,
            },
        )
        if resp.status_code != 200:
            raise WhoopError(f"Token exchange failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        if "refresh_token" not in payload:
            raise WhoopError(
                "WHOOP returned no refresh_token — the 'offline' scope is missing "
                "from the authorisation request or from the app's allowed scopes."
            )
        tokens = Tokens.from_response(payload)
        self._store.save(tokens)
        return tokens

    async def _refresh(self, tokens: Tokens) -> Tokens:
        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": self._settings.whoop_client_id,
                "client_secret": self._settings.whoop_client_secret,
                "scope": "offline",
            },
        )
        if resp.status_code != 200:
            raise WhoopError(
                f"Refresh failed ({resp.status_code}): {resp.text}. "
                "Re-run the OAuth bootstrap at /oauth/start."
            )
        # The response carries a *new* refresh token; the old one is now dead,
        # so persist before doing anything else with it.
        new_tokens = Tokens.from_response(resp.json())
        self._store.save(new_tokens)
        return new_tokens

    async def _access_token(self, force_refresh: bool = False) -> str:
        async with self._store.lock:
            tokens = self._store.load()
            if force_refresh or tokens.expired():
                tokens = await self._refresh(tokens)
            return tokens.access_token

    # ---------- Requests ----------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        token = await self._access_token()
        resp = await self._http.get(
            f"{API_BASE}{path}",
            params=clean,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            # Token rejected despite not looking expired — refresh once, retry.
            token = await self._access_token(force_refresh=True)
            resp = await self._http.get(
                f"{API_BASE}{path}",
                params=clean,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 404:
            raise WhoopError(f"Not found: {path}")
        if resp.status_code >= 400:
            raise WhoopError(f"WHOOP API error ({resp.status_code}): {resp.text}")
        return resp.json()

    async def delete(self, path: str) -> dict:
        token = await self._access_token()
        resp = await self._http.delete(
            f"{API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}
        )
        if resp.status_code >= 400:
            raise WhoopError(f"WHOOP API error ({resp.status_code}): {resp.text}")
        return {"status": "ok", "http_status": resp.status_code}

    def ready(self) -> bool:
        try:
            self._store.load()
        except NotAuthorisedError:
            return False
        return True
