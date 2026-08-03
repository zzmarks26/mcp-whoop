"""MCP tools over the WHOOP v2 API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from .client import WhoopClient

Limit = Annotated[int, Field(ge=1, le=25, description="Records per page (max 25).")]
Start = Annotated[
    str | None,
    Field(description="ISO-8601 start of range, e.g. 2026-07-01T00:00:00Z."),
]
End = Annotated[
    str | None, Field(description="ISO-8601 end of range, e.g. 2026-08-01T00:00:00Z.")
]
NextToken = Annotated[
    str | None, Field(description="next_token from a previous page, to continue it.")
]
Days = Annotated[int, Field(ge=1, le=90, description="How many days back to cover.")]


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _records(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        return payload.get("records", []) or []
    return []


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _collect(records: list[dict], path: tuple[str, ...]) -> list[float]:
    """Pull a nested numeric field out of every record that has it."""
    out: list[float] = []
    for record in records:
        node: Any = record
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, (int, float)):
            out.append(float(node))
    return out


def register(mcp: FastMCP, client: WhoopClient) -> None:
    # ---------------- diagnostics ----------------

    @mcp.tool
    async def whoop_status() -> dict:
        """Report whether the server holds a usable WHOOP token.

        Use this first when other tools fail — it distinguishes "not connected
        to WHOOP yet" from a genuine API error.
        """
        if not client.ready():
            return {
                "connected": False,
                "detail": "No WHOOP token stored; the one-time OAuth bootstrap "
                "at /oauth/start has not been completed.",
            }
        profile = await client.get("/v2/user/profile/basic")
        return {"connected": True, "user": profile}

    # ---------------- user ----------------

    @mcp.tool
    async def whoop_get_profile() -> dict:
        """Get the WHOOP user's basic profile (id, email, first and last name)."""
        return await client.get("/v2/user/profile/basic")

    @mcp.tool
    async def whoop_get_body_measurement() -> dict:
        """Get body measurements: height (m), weight (kg) and max heart rate."""
        return await client.get("/v2/user/measurement/body")

    @mcp.tool
    async def whoop_revoke_access(
        confirm: Annotated[
            bool,
            Field(
                description="Must be true. Revokes this app's WHOOP access; "
                "restoring it requires redoing the browser OAuth flow."
            ),
        ] = False,
    ) -> dict:
        """DESTRUCTIVE. Revoke this application's OAuth access to WHOOP.

        After this the server cannot read any data until the owner repeats the
        one-time OAuth bootstrap. Never call it unless the user explicitly
        asked to disconnect WHOOP.
        """
        if not confirm:
            return {
                "status": "refused",
                "detail": "Set confirm=true to revoke WHOOP access.",
            }
        return await client.delete("/v2/user/access")

    # ---------------- cycles ----------------

    @mcp.tool
    async def whoop_get_cycles(
        limit: Limit = 10,
        start: Start = None,
        end: End = None,
        next_token: NextToken = None,
    ) -> dict:
        """List physiological cycles (day strain, average and max heart rate).

        A WHOOP "cycle" is one physiological day, which starts when you fall
        asleep rather than at midnight.
        """
        return await client.get(
            "/v2/cycle",
            {"limit": limit, "start": start, "end": end, "nextToken": next_token},
        )

    @mcp.tool
    async def whoop_get_cycle(cycle_id: int) -> dict:
        """Get one physiological cycle by its numeric id."""
        return await client.get(f"/v2/cycle/{cycle_id}")

    @mcp.tool
    async def whoop_get_current_cycle() -> dict:
        """Get the cycle currently in progress (the most recent one)."""
        payload = await client.get("/v2/cycle", {"limit": 1})
        records = _records(payload)
        return records[0] if records else {"detail": "No cycles returned."}

    @mcp.tool
    async def whoop_get_sleep_for_cycle(cycle_id: int) -> dict:
        """Get the sleep that belongs to a given cycle."""
        return await client.get(f"/v2/cycle/{cycle_id}/sleep")

    @mcp.tool
    async def whoop_get_recovery_for_cycle(cycle_id: int) -> dict:
        """Get the recovery score that belongs to a given cycle."""
        return await client.get(f"/v2/cycle/{cycle_id}/recovery")

    # ---------------- recovery ----------------

    @mcp.tool
    async def whoop_get_recoveries(
        limit: Limit = 10,
        start: Start = None,
        end: End = None,
        next_token: NextToken = None,
    ) -> dict:
        """List recovery records (recovery %, HRV in ms, resting heart rate)."""
        return await client.get(
            "/v2/recovery",
            {"limit": limit, "start": start, "end": end, "nextToken": next_token},
        )

    @mcp.tool
    async def whoop_get_latest_recovery() -> dict:
        """Get the most recent recovery record."""
        payload = await client.get("/v2/recovery", {"limit": 1})
        records = _records(payload)
        return records[0] if records else {"detail": "No recovery records returned."}

    @mcp.tool
    async def whoop_get_recovery_summary(days: Days = 7) -> dict:
        """Summarise recovery, HRV and resting heart rate over the last N days.

        Returns per-day records plus averages — cheaper for trend questions
        than pulling raw pages and averaging them yourself.
        """
        payload = await client.get(
            "/v2/recovery", {"limit": 25, "start": _iso_days_ago(days)}
        )
        records = _records(payload)
        return {
            "days": days,
            "count": len(records),
            "average_recovery_score": _mean(
                _collect(records, ("score", "recovery_score"))
            ),
            "average_hrv_rmssd_milli": _mean(
                _collect(records, ("score", "hrv_rmssd_milli"))
            ),
            "average_resting_heart_rate": _mean(
                _collect(records, ("score", "resting_heart_rate"))
            ),
            "records": records,
        }

    # ---------------- sleep ----------------

    @mcp.tool
    async def whoop_get_sleeps(
        limit: Limit = 10,
        start: Start = None,
        end: End = None,
        next_token: NextToken = None,
    ) -> dict:
        """List sleep activities with stages, performance and respiratory rate."""
        return await client.get(
            "/v2/activity/sleep",
            {"limit": limit, "start": start, "end": end, "nextToken": next_token},
        )

    @mcp.tool
    async def whoop_get_sleep(sleep_id: str) -> dict:
        """Get one sleep activity by its id (a UUID in API v2)."""
        return await client.get(f"/v2/activity/sleep/{sleep_id}")

    @mcp.tool
    async def whoop_get_latest_sleep() -> dict:
        """Get the most recent sleep activity."""
        payload = await client.get("/v2/activity/sleep", {"limit": 1})
        records = _records(payload)
        return records[0] if records else {"detail": "No sleep records returned."}

    @mcp.tool
    async def whoop_get_sleep_summary(days: Days = 7) -> dict:
        """Summarise sleep performance and respiratory rate over the last N days."""
        payload = await client.get(
            "/v2/activity/sleep", {"limit": 25, "start": _iso_days_ago(days)}
        )
        records = _records(payload)
        return {
            "days": days,
            "count": len(records),
            "average_sleep_performance_percentage": _mean(
                _collect(records, ("score", "sleep_performance_percentage"))
            ),
            "average_respiratory_rate": _mean(
                _collect(records, ("score", "respiratory_rate"))
            ),
            "average_sleep_efficiency_percentage": _mean(
                _collect(records, ("score", "sleep_efficiency_percentage"))
            ),
            "records": records,
        }

    # ---------------- workouts ----------------

    @mcp.tool
    async def whoop_get_workouts(
        limit: Limit = 10,
        start: Start = None,
        end: End = None,
        next_token: NextToken = None,
    ) -> dict:
        """List workouts with strain, average/max heart rate and zone durations."""
        return await client.get(
            "/v2/activity/workout",
            {"limit": limit, "start": start, "end": end, "nextToken": next_token},
        )

    @mcp.tool
    async def whoop_get_workout(workout_id: str) -> dict:
        """Get one workout by its id (a UUID in API v2)."""
        return await client.get(f"/v2/activity/workout/{workout_id}")

    @mcp.tool
    async def whoop_get_strain_summary(days: Days = 7) -> dict:
        """Summarise day strain and workout strain over the last N days."""
        since = _iso_days_ago(days)
        cycles = _records(await client.get("/v2/cycle", {"limit": 25, "start": since}))
        workouts = _records(
            await client.get("/v2/activity/workout", {"limit": 25, "start": since})
        )
        return {
            "days": days,
            "cycle_count": len(cycles),
            "workout_count": len(workouts),
            "average_day_strain": _mean(_collect(cycles, ("score", "strain"))),
            "total_workout_strain": round(
                sum(_collect(workouts, ("score", "strain"))), 2
            )
            or None,
            "average_workout_strain": _mean(_collect(workouts, ("score", "strain"))),
            "workouts": workouts,
        }
