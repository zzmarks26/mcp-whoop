# mcp-whoop

An MCP server that exposes the [WHOOP v2 API](https://developer.whoop.com/api/)
over Streamable HTTP, authenticated with GitHub OAuth.

It is built for **remote** use — as a custom connector in claude.ai / Claude
Desktop, or over `claude mcp add --transport http` — rather than as a local
stdio server. Two consequences follow from that:

- **Clients authenticate with OAuth**, handled by FastMCP's `GitHubProvider`.
  The server publishes `/.well-known/oauth-authorization-server` and supports
  Dynamic Client Registration, which is what claude.ai requires.
- **WHOOP credentials are held server-side.** The refresh token is stored on a
  mounted volume and rotated automatically, so the browser OAuth flow is done
  once, not after every restart.

## Access control

Holding a valid GitHub identity is not enough. `ALLOWED_GITHUB_LOGINS` lists
the accounts permitted to use the server, and every message is checked against
it. If the list is empty the server refuses to start — an unset allowlist fails
closed rather than exposing health data to any GitHub user.

## Tools

**Diagnostics** — `whoop_status`

**User** — `whoop_get_profile`, `whoop_get_body_measurement`,
`whoop_revoke_access` (destructive, requires `confirm=true`)

**Cycles** — `whoop_get_cycles`, `whoop_get_cycle`, `whoop_get_current_cycle`,
`whoop_get_sleep_for_cycle`, `whoop_get_recovery_for_cycle`

**Recovery** — `whoop_get_recoveries`, `whoop_get_latest_recovery`,
`whoop_get_recovery_summary`

**Sleep** — `whoop_get_sleeps`, `whoop_get_sleep`, `whoop_get_latest_sleep`,
`whoop_get_sleep_summary`

**Workouts** — `whoop_get_workouts`, `whoop_get_workout`,
`whoop_get_strain_summary`

A WHOOP *cycle* is a physiological day that begins at sleep onset, not at
midnight — which is why cycle ids, not dates, tie sleep and recovery together.

## Setup

### 1. GitHub OAuth app

Create one at **Settings → Developer settings → OAuth Apps** with the callback
URL `<BASE_URL>/auth/callback`. Copy the client id and secret.

### 2. WHOOP developer app

Create one at [developer.whoop.com](https://developer.whoop.com/) with:

- redirect URI `<BASE_URL>/oauth/callback`
- scopes including **`offline`** — without it WHOOP issues no refresh token and
  access dies after the first hour.

### 3. Configure and run

```bash
cp .env.example .env   # then fill it in
docker build -t mcp-whoop .
docker run -d --name mcp-whoop --env-file .env -v /srv/whoop-data:/data -p 8000:8000 mcp-whoop
```

### 4. Connect WHOOP (once)

Open `<BASE_URL>/oauth/start?token=<SETUP_TOKEN>` in a browser and approve.
The refresh token is written to `/data/whoop_token.json` with mode `0600`.
Verify with the `whoop_status` tool.

### 5. Add the connector

- **Claude Code** — `claude mcp add --transport http whoop <BASE_URL>/mcp`
- **claude.ai / Claude Desktop** — Settings → Connectors → Add custom
  connector → `<BASE_URL>/mcp`

Both then send you through GitHub to sign in.

## Configuration

| Variable | Purpose |
|---|---|
| `BASE_URL` | Public URL of this server, no trailing slash |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth app credentials |
| `ALLOWED_GITHUB_LOGINS` | Comma-separated logins permitted to connect |
| `JWT_SIGNING_KEY` | Stable key for client tokens; unset means clients are signed out on restart |
| `WHOOP_CLIENT_ID` / `WHOOP_CLIENT_SECRET` | WHOOP developer app credentials |
| `SETUP_TOKEN` | Guards `/oauth/start` |
| `DATA_DIR` | Token store location (default `/data`) |
| `HOST` / `PORT` | Bind address (default `0.0.0.0:8000`) |

## Notes

WHOOP rotates refresh tokens: each refresh returns a new one and invalidates
the old. The store therefore writes atomically and under a lock, so a crash or
two concurrent requests cannot strand the server without a valid token.

## Licence

MIT
