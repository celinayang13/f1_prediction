"""
OpenF1 REST API client with tenacity retry and exponential backoff.

Provides an async context-manager client (OpenF1Client) and top-level
convenience functions for synchronous callers.

Rate limit handling (DATA-01, Pitfall 3):
  HTTP 429 raises RateLimitedError → tenacity retries with exponential backoff
  Timeout → tenacity retries with exponential backoff
  stop_after_attempt(6) caps total retries (D-12)

Security:
  - All requests use HTTPS (T-02-06: httpx default TLS verification)
  - Responses logged at INFO by URL only, never full body (T-02-03)
  - No f-string SQL — this module is HTTP-only (T-02-05)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openf1.org/v1"
DEFAULT_TIMEOUT = 20.0

__all__ = [
    "OpenF1Client",
    "fetch_sessions",
    "fetch_laps",
    "fetch_stints",
    "fetch_pit_stops",
    "fetch_positions",
    "fetch_weather",
    "fetch_intervals",
    "fetch_drivers",
]


class RateLimitedError(Exception):
    """Raised when OpenF1 returns HTTP 429 (rate limited)."""


class OpenF1Client:
    """
    Async context manager wrapping httpx.AsyncClient for OpenF1 REST API calls.

    Usage:
        async with OpenF1Client() as client:
            laps = await client.laps(session_key=9158)

    Retry policy (D-12, RESEARCH Pattern 4):
      - Retries on httpx.TimeoutException and RateLimitedError
      - Exponential backoff: wait 2–60 seconds between attempts
      - Maximum 6 attempts total
    """

    def __init__(self, *, _transport=None):
        """
        Args:
            _transport: Optional httpx transport override for testing.
                        Pass an httpx.AsyncBaseTransport (or httpx.BaseTransport
                        wrapped in AsyncTransport) to intercept requests in tests.
        """
        self._transport = _transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenF1Client":
        kwargs: dict[str, Any] = {
            "timeout": DEFAULT_TIMEOUT,
            "base_url": BASE_URL,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, RateLimitedError)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    async def _fetch(self, endpoint: str, params: dict) -> list[dict]:
        """
        Fetch a single OpenF1 endpoint with retry.

        Raises RateLimitedError on HTTP 429 to trigger tenacity retry.
        Returns an empty list on HTTP 404 — OpenF1 returns 404 (not an empty
        array) when no data exists for a query (e.g. /pit for a qualifying
        session that has no pit stops). Treating 404 as [] is correct per
        OpenF1 API semantics.
        Raises httpx.HTTPStatusError on other non-2xx responses.

        Security: logs URL and status only, never response body (T-02-03).
        """
        assert self._client is not None, "Client not open — use 'async with OpenF1Client()'"
        url = f"/{endpoint}"
        logger.info("GET %s params=%s", url, params)
        resp = await self._client.get(url, params=params)
        logger.info("GET %s → %d", url, resp.status_code)

        if resp.status_code == 429:
            raise RateLimitedError(f"Rate limited on {url}")

        # OpenF1 returns 404 when no data exists for a query (not an error —
        # e.g. /pit returns 404 for qualifying sessions). Treat as empty list.
        if resp.status_code == 404:
            logger.debug("GET %s returned 404 — treating as empty result", url)
            return []

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Endpoint methods
    # ------------------------------------------------------------------

    async def sessions(self, session_key: int | None = None, **filters) -> list[dict]:
        """Fetch sessions from /v1/sessions."""
        params: dict = {**filters}
        if session_key is not None:
            params["session_key"] = session_key
        return await self._fetch("sessions", params)

    async def laps(self, session_key: int, **filters) -> list[dict]:
        """Fetch laps from /v1/laps for a session."""
        params: dict = {"session_key": session_key, **filters}
        return await self._fetch("laps", params)

    async def stints(self, session_key: int) -> list[dict]:
        """Fetch stints from /v1/stints for a session."""
        return await self._fetch("stints", {"session_key": session_key})

    async def pit(self, session_key: int) -> list[dict]:
        """Fetch pit stops from /v1/pit for a session."""
        return await self._fetch("pit", {"session_key": session_key})

    async def position(self, session_key: int) -> list[dict]:
        """Fetch position data from /v1/position for a session."""
        return await self._fetch("position", {"session_key": session_key})

    async def weather(self, session_key: int) -> list[dict]:
        """Fetch weather data from /v1/weather for a session."""
        return await self._fetch("weather", {"session_key": session_key})

    async def intervals(self, session_key: int) -> list[dict]:
        """Fetch intervals from /v1/intervals for a session."""
        return await self._fetch("intervals", {"session_key": session_key})

    async def drivers(self, session_key: int) -> list[dict]:
        """Fetch driver metadata from /v1/drivers for a session."""
        return await self._fetch("drivers", {"session_key": session_key})


# ---------------------------------------------------------------------------
# Synchronous convenience functions (thin asyncio.run wrappers)
# ---------------------------------------------------------------------------

def fetch_sessions(session_key: int | None = None, **filters) -> list[dict]:
    """Fetch sessions synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.sessions(session_key=session_key, **filters)
    return asyncio.run(_run())


def fetch_laps(session_key: int, **filters) -> list[dict]:
    """Fetch laps synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.laps(session_key=session_key, **filters)
    return asyncio.run(_run())


def fetch_stints(session_key: int) -> list[dict]:
    """Fetch stints synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.stints(session_key=session_key)
    return asyncio.run(_run())


def fetch_pit_stops(session_key: int) -> list[dict]:
    """Fetch pit stops synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.pit(session_key=session_key)
    return asyncio.run(_run())


def fetch_positions(session_key: int) -> list[dict]:
    """Fetch positions synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.position(session_key=session_key)
    return asyncio.run(_run())


def fetch_weather(session_key: int) -> list[dict]:
    """Fetch weather synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.weather(session_key=session_key)
    return asyncio.run(_run())


def fetch_intervals(session_key: int) -> list[dict]:
    """Fetch intervals synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.intervals(session_key=session_key)
    return asyncio.run(_run())


def fetch_drivers(session_key: int) -> list[dict]:
    """Fetch driver metadata synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.drivers(session_key=session_key)
    return asyncio.run(_run())
