"""One-time browser flow that gives the server a WHOOP refresh token.

These two routes sit outside the MCP endpoint and therefore outside the MCP
OAuth layer, so /oauth/start is guarded by SETUP_TOKEN and /oauth/callback by
a single-use state value.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .client import WhoopClient
from .settings import Settings

logger = logging.getLogger(__name__)

_pending_states: set[str] = set()


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        f"<body style='font-family:system-ui;max-width:40em;margin:4em auto'>"
        f"<h1>{title}</h1>{body}</body>",
        status_code=status,
    )


def register(mcp: FastMCP, client: WhoopClient, settings: Settings) -> None:
    @mcp.custom_route("/oauth/start", methods=["GET"])
    async def oauth_start(request: Request) -> Response:
        supplied = request.query_params.get("token", "")
        # compare_digest keeps the check constant-time.
        if not hmac.compare_digest(supplied, settings.setup_token):
            return _page("Forbidden", "<p>Invalid or missing setup token.</p>", 403)

        state = secrets.token_urlsafe(24)
        _pending_states.add(state)
        return RedirectResponse(client.authorize_url(state), status_code=302)

    @mcp.custom_route("/oauth/callback", methods=["GET"])
    async def oauth_callback(request: Request) -> Response:
        error = request.query_params.get("error")
        if error:
            return _page("WHOOP returned an error", f"<p>{error}</p>", 400)

        state = request.query_params.get("state", "")
        if state not in _pending_states:
            return _page(
                "Invalid state",
                "<p>This callback did not originate from /oauth/start, or it "
                "has already been used. Start again.</p>",
                400,
            )
        _pending_states.discard(state)

        code = request.query_params.get("code")
        if not code:
            return _page("Missing code", "<p>No authorisation code supplied.</p>", 400)

        try:
            await client.exchange_code(code)
        except Exception as exc:  # surfaced to a human in a browser
            logger.exception("WHOOP token exchange failed")
            return _page("Token exchange failed", f"<pre>{exc}</pre>", 500)

        return _page(
            "WHOOP connected",
            "<p>The refresh token has been stored. You can close this tab — "
            "this step does not need repeating unless access is revoked.</p>",
        )
