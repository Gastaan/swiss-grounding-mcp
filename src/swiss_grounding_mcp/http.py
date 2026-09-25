"""Polite HTTP: disk cache with TTL, robots.txt check (configurable), per-host pacing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

import httpx
from protego import Protego

from .config import settings

log = logging.getLogger("swiss_grounding_mcp")

HOUR = 3600
DAY = 24 * HOUR


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


_client: httpx.AsyncClient | None = None
_host_locks: dict[str, asyncio.Lock] = {}
_host_last: dict[str, float] = {}
_robots: dict[str, tuple[float, Protego | None]] = {}


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "de,fr,it,en;q=0.8"},
            timeout=settings.timeout_s,
            follow_redirects=True,
        )
    return _client


def _cache_path(key: str):
    digest = hashlib.sha256(key.encode()).hexdigest()
    return settings.cache_dir / "http" / digest[:2] / f"{digest}.json"


def _cache_get(key: str, ttl: float) -> str | None:
    try:
        entry = json.loads(_cache_path(key).read_text())
    except (OSError, ValueError):
        return None
    if settings.offline or time.time() - entry["t"] < ttl:
        return entry["body"]
    return None


def _cache_put(key: str, body: str) -> None:
    path = _cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"t": time.time(), "body": body}))
    except OSError as e:  # a read-only disk must not break answers
        log.warning("cache write failed: %s", e)


async def _pace(host: str) -> None:
    lock = _host_locks.setdefault(host, asyncio.Lock())
    async with lock:
        wait = _host_last.get(host, 0) + settings.min_interval_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _host_last[host] = time.monotonic()


async def robots_allowed(url: str) -> bool:
    if not settings.respect_robots:
        return True
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = _robots.get(origin)
    if cached is None or time.time() - cached[0] > DAY:
        parser: Protego | None
        try:
            r = await client().get(f"{origin}/robots.txt")
            if r.status_code >= 500:
                parser = Protego.parse("User-agent: *\nDisallow: /")  # RFC 9309: unreachable => disallow
            elif r.status_code >= 400:
                parser = None  # no robots.txt => everything allowed
            else:
                parser = Protego.parse(r.text)
        except httpx.HTTPError:
            parser = Protego.parse("User-agent: *\nDisallow: /")
        _robots[origin] = cached = (time.time(), parser)
    parser = cached[1]
    return parser is None or parser.can_fetch(url, settings.robots_token)


async def fetch(
    url: str,
    *,
    ttl: float = DAY,
    params: dict | None = None,
    method: str = "GET",
    data: dict | str | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    check_robots: bool = True,
) -> str:
    """Return the response body as text, served from cache when fresh.

    `check_robots=False` is only for documented APIs: a website's robots.txt governs crawling of
    its pages, not calls to a published API endpoint (e.g. api3.geo.admin.ch).
    """
    raw = data if isinstance(data, str) else None  # e.g. a SOAP envelope
    form = data if isinstance(data, dict) else None
    req = client().build_request(method, url, params=params, data=form, content=raw, json=json_body,
                                 headers=headers)
    key = f"{method} {req.url} {data!r} {json_body!r}"
    if (body := _cache_get(key, ttl)) is not None:
        return body
    if settings.offline:
        raise FetchError(f"offline mode and no cached copy of {req.url}")
    if check_robots and not await robots_allowed(str(req.url)):
        raise RobotsDisallowed(f"robots.txt of {req.url.host} disallows {req.url.path}")
    await _pace(req.url.host)
    started = time.monotonic()
    try:
        r = await client().send(req)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__} for {req.url}: {e}") from e
    log.info("fetch %s %s %d %dms", method, req.url, r.status_code, (time.monotonic() - started) * 1000)
    if r.charset_encoding:
        body = r.text
    else:  # no declared charset: many Swiss open-data CSVs are Latin-1
        try:
            body = r.content.decode("utf-8")
        except UnicodeDecodeError:
            body = r.content.decode("latin-1")
    _cache_put(key, body)
    return body


async def fetch_json(url: str, **kw):
    return json.loads(await fetch(url, **kw))
