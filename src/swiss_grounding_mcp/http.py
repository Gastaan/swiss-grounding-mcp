"""Polite, safe HTTP: disk cache with TTL, robots.txt check (configurable), per-host pacing,
redirects re-checked hop by hop, a labelled stale copy when a source is down, and counters for /metrics."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime
from functools import cache
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

from .config import DATA_DIR, settings

log = logging.getLogger("swiss_grounding_mcp")

HOUR = 3600
DAY = 24 * HOUR
MAX_REDIRECTS = 5


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class BlockedURL(FetchError):
    """The URL (or a redirect target) is not an allowed source, or points into a private network."""


class TermsDisallowed(BlockedURL):
    """The source's terms of use do not allow automated access (SGM_RESPECT_TERMS)."""


@cache
def source_terms() -> dict[str, dict]:
    path = DATA_DIR / "source_terms.json"
    return json.loads(path.read_text())["sources"] if path.exists() else {}


def terms_for(host: str) -> tuple[str, dict] | None:
    """The terms entry that covers a host (the entry for a domain covers its subdomains)."""
    parts = host.lower().split(".")
    for i in range(len(parts) - 1):
        domain = ".".join(parts[i:])
        if domain in source_terms():
            return domain, source_terms()[domain]
    return None


_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None
_host_next: dict[str, float] = {}
_robots: dict[str, tuple[float, Protego | None]] = {}
_dns: dict[str, tuple[float, bool]] = {}
_inflight: dict[str, asyncio.Future] = {}
_stale: ContextVar[list[dict] | None] = ContextVar("sgm_stale", default=None)
_stats: dict[str, dict] = {}


def client() -> httpx.AsyncClient:
    """One client per event loop: pooled connections belong to the loop that opened them, so a client
    must not outlive its loop (tests and embedders run several loops one after another)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "de,fr,it,en;q=0.8"},
            timeout=settings.timeout_s,
            follow_redirects=False,  # redirects are followed in fetch(), re-checking every hop
        )
        _client_loop = loop
    return _client


def _stat(host: str, key: str, error: str | None = None) -> None:
    s = _stats.setdefault(host, {"requests": 0, "errors": 0, "cache_hits": 0, "stale_served": 0})
    s[key] += 1
    if error:
        s["last_error"] = error
        s["last_error_at"] = datetime.now().isoformat(timespec="seconds")


def stats() -> dict[str, dict]:
    return {host: dict(s) for host, s in sorted(_stats.items())}


def track_stale() -> list[dict]:
    """Start recording, for the current tool call, which answers came from an expired cached copy."""
    used: list[dict] = []
    _stale.set(used)
    return used


# ---------------------------------------------------------------- cache


def _cache_path(key: str):
    digest = hashlib.sha256(key.encode()).hexdigest()
    return settings.cache_dir / "http" / digest[:2] / f"{digest}.json"


def _cache_read(key: str) -> tuple[float, str] | None:
    try:
        entry = json.loads(_cache_path(key).read_text())
        return entry["t"], entry["body"]
    except (OSError, ValueError, KeyError):
        return None


def _cache_put(key: str, body: str) -> None:
    path = _cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"t": time.time(), "body": body}))
        tmp.replace(path)  # atomic: a concurrent reader never sees half a file
    except OSError as e:  # a read-only disk must not break answers
        log.warning("cache write failed: %s", e)


def prune_cache() -> tuple[int, int]:
    """Delete entries older than SGM_CACHE_MAX_DAYS, then the oldest ones until under SGM_CACHE_MAX_MB.
    Returns (files removed, bytes kept)."""
    root = settings.cache_dir / "http"
    if not root.exists():
        return 0, 0
    now, removed, files = time.time(), 0, []
    for path in root.glob("*/*"):
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime > settings.cache_max_age_s or path.suffix == ".tmp" and now - st.st_mtime > HOUR:
            path.unlink(missing_ok=True)
            removed += 1
        else:
            files.append((st.st_mtime, st.st_size, path))
    total, limit = sum(f[1] for f in files), settings.cache_max_mb * 1024 * 1024
    for _, size, path in sorted(files):
        if total <= limit:
            break
        path.unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed, total


# ---------------------------------------------------------------- checks


async def _host_addresses(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}


async def _public_host(host: str) -> bool:
    cached = _dns.get(host)
    if cached and time.time() - cached[0] < HOUR:
        return cached[1]
    try:
        addrs = await _host_addresses(host)
    except OSError as e:
        raise FetchError(f"cannot resolve {host}: {e}") from e
    ok = bool(addrs) and all(a.is_global for a in addrs)
    _dns[host] = (time.time(), ok)
    return ok


async def _check_target(url: str, allow: Callable[[str], bool] | None) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL(f"not an http(s) URL: {url}")
    if allow is not None and not allow(url):
        raise BlockedURL(f"{parts.hostname} is not an allowed source")
    if settings.respect_terms and (entry := terms_for(parts.hostname)) and not entry[1].get("automated_access", True):
        raise TermsDisallowed(f"the terms of use of {entry[0]} do not allow automated access ({entry[1]['terms']})")
    if not await _public_host(parts.hostname):
        raise BlockedURL(f"{parts.hostname} resolves to a private or reserved address")


async def robots_allowed(url: str) -> bool:
    if not settings.respect_robots:
        return True
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = _robots.get(origin)
    if cached is None or time.time() - cached[0] > DAY:
        parser: Protego | None
        try:
            r = await client().get(f"{origin}/robots.txt", follow_redirects=True)
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


def _reserve_slot(host: str) -> float:
    """Book the next request slot for a host; returns how long to wait. No lock needed: this runs
    without awaiting, so concurrent callers each get their own slot instead of queueing on a lock."""
    now = time.monotonic()
    slot = max(now, _host_next.get(host, 0.0))
    _host_next[host] = slot + settings.min_interval_s
    return slot - now


# ---------------------------------------------------------------- fetch


async def _send(req: httpx.Request, check_robots: bool, allow: Callable[[str], bool] | None) -> str:
    for _ in range(MAX_REDIRECTS + 1):
        url = str(req.url)
        await _check_target(url, allow)
        if check_robots and not await robots_allowed(url):
            raise RobotsDisallowed(f"robots.txt of {req.url.host} disallows {req.url.path}")
        if (wait := _reserve_slot(req.url.host)) > 0:
            await asyncio.sleep(wait)
        started = time.monotonic()
        try:
            r = await client().send(req)
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__} for {req.url.host}{req.url.path}: {e}") from e
        # host and path only: query strings can carry what the user typed (addresses, names)
        log.info("fetch %s %s%s %d %dms", req.method, req.url.host, req.url.path, r.status_code,
                 (time.monotonic() - started) * 1000)
        if r.is_redirect:
            target = urljoin(url, r.headers.get("location", ""))
            keep = r.status_code in (307, 308)  # only these keep the method and body
            drop = {"host", "content-length"} | (set() if keep else {"content-type"})
            req = client().build_request(req.method if keep else "GET", target,
                                         content=req.content if keep else None,
                                         headers={k: v for k, v in req.headers.items() if k.lower() not in drop})
            continue
        try:
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise FetchError(f"HTTP {r.status_code} for {req.url.host}{req.url.path}") from e
        if r.charset_encoding:
            return r.text
        try:  # no declared charset: many Swiss open-data CSVs are Latin-1
            return r.content.decode("utf-8")
        except UnicodeDecodeError:
            return r.content.decode("latin-1")
    raise FetchError(f"more than {MAX_REDIRECTS} redirects from {req.url.host}")


async def _once(key: str, make: Callable[[], Awaitable[str]]) -> str:
    """Single flight: identical requests already on their way share one upstream call."""
    if (pending := _inflight.get(key)) is not None:
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if not pending.cancelled() or (task and task.cancelling()):
                raise
            # the first caller gave up (its own deadline); this caller still wants the answer
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        body = await make()
        fut.set_result(body)
        return body
    except asyncio.CancelledError:
        fut.cancel()
        raise
    except Exception as e:
        fut.set_exception(e)
        fut.exception()  # mark retrieved when nobody else was waiting
        raise
    finally:
        if _inflight.get(key) is fut:
            _inflight.pop(key, None)


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
    allow: Callable[[str], bool] | None = None,
) -> str:
    """Return the response body as text, served from cache when fresh.

    `check_robots=False` is only for documented APIs: a website's robots.txt governs crawling of
    its pages, not calls to a published API endpoint (e.g. api3.geo.admin.ch).
    `allow` is re-applied to every redirect target (e.g. the official-domain allowlist).
    """
    raw = data if isinstance(data, str) else None  # e.g. a SOAP envelope
    form = data if isinstance(data, dict) else None
    req = client().build_request(method, url, params=params, data=form, content=raw, json=json_body,
                                 headers=headers)
    host = req.url.host
    key = f"{method} {req.url} {data!r} {json_body!r}"
    entry = _cache_read(key)
    if entry and (settings.offline or time.time() - entry[0] < ttl):
        _stat(host, "cache_hits")
        return entry[1]
    if settings.offline:
        raise FetchError(f"offline mode and no cached copy of {host}{req.url.path}")
    _stat(host, "requests")
    try:
        body = await _once(key, lambda: _send(req, check_robots, allow))
    except FetchError as e:
        _stat(host, "errors", str(e))
        age = time.time() - entry[0] if entry else None
        if age is not None and age < settings.max_stale_s and not isinstance(e, (RobotsDisallowed, BlockedURL)):
            _stat(host, "stale_served")
            retrieved = datetime.fromtimestamp(entry[0]).isoformat(timespec="minutes")
            log.warning("serving stale copy of %s%s from %s: %s", host, req.url.path, retrieved, e)
            if (used := _stale.get()) is not None:
                used.append({"source": host, "retrieved_at": retrieved})
            return entry[1]
        raise
    _cache_put(key, body)
    return body


async def fetch_json(url: str, **kw):
    return json.loads(await fetch(url, **kw))
