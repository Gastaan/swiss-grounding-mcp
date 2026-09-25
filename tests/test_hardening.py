"""Offline tests for trust, resilience and HTTP safety: official-source checks, redirects, stale copies,
time limits, request coalescing, cache pruning, log privacy and the HTTP guards. No network needed."""

import asyncio
import ipaddress
import json
import logging
import os
import time
from importlib.metadata import version
from types import SimpleNamespace

import httpx
import pytest

from swiss_grounding_mcp import http, server
from swiss_grounding_mcp.authorities import classify
from swiss_grounding_mcp.config import VERSION, settings
from swiss_grounding_mcp.places import official_website
from swiss_grounding_mcp.sources import companies

from .conftest import call


@pytest.fixture
def net(monkeypatch, tmp_path):
    """Route http.fetch through a fake network. Set net.routes[url] to a Response, an exception, or an
    async function of the request; every requested URL is appended to net.calls."""
    routes, calls = {}, []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404)
        if isinstance(route, Exception):
            raise route
        if callable(route):
            return await route(request)
        return route

    async def public_addresses(host):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address("8.8.8.8")}

    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http, "client", lambda: fake)
    monkeypatch.setattr(http, "_host_addresses", public_addresses)
    for name in ("_dns", "_host_next", "_inflight", "_robots"):
        monkeypatch.setattr(http, name, {})
    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    monkeypatch.setattr(settings, "respect_robots", False)
    monkeypatch.setattr(settings, "min_interval_s", 0.0)
    return SimpleNamespace(routes=routes, calls=calls)


# ---------------------------------------------------------------- which sources count as official


@pytest.mark.parametrize(("url", "ok"), [
    ("https://www.lausanne.ch/", True), ("https://www.bubendorf.swiss", True),
    ("http://www.grindelwald.com", False), ("https://www.appenzell.org", False),
    ("https://ruetibeilyssach.jimdofree.com/", False), ("https://gemeinde.wixsite.ch/x", False),
    ("ftp://www.bern.ch", False), (None, False),
])
def test_official_website_check(url, ok):
    assert (official_website(url) is not None) is ok


def test_unverified_municipal_sites_are_not_official_sources():
    """Wikidata listed tourism and parked domains as municipal websites; they must not be cited as official."""
    for url in ("https://www.grindelwald.com/de/events", "https://www.appenzell.org/",
                "https://ruetibeilyssach.jimdofree.com/x", "http://www.villorsonnens.com/"):
        assert classify(url) is None, url
    # reviewed exceptions in data/website_overrides.json stay official
    assert classify("https://www.chamoson.net/x").publisher == "Municipality of Chamoson"


async def test_place_info_gives_no_unverified_website(client):
    r = await call(client, "swiss_place_info", place="Grindelwald")
    assert r["status"] == "ok" and r["data"]["website"] is None


# ---------------------------------------------------------------- redirects


async def test_redirect_away_from_official_domain_is_refused(client, net):
    net.routes["https://www.vd.ch/x"] = httpx.Response(302, headers={"location": "https://evil.example/steal"})
    net.routes["https://evil.example/steal"] = httpx.Response(200, text="<p>not official</p>")
    r = await call(client, "read_official_page", url="https://www.vd.ch/x")
    assert r["status"] == "not_covered" and "redirects away" in r["summary"]
    assert "https://evil.example/steal" not in net.calls


async def test_redirect_into_private_network_is_refused(net):
    net.routes["https://api.example.ch/data"] = httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    with pytest.raises(http.BlockedURL):
        await http.fetch("https://api.example.ch/data", check_robots=False)
    assert not any("169.254" in u for u in net.calls)


async def test_redirect_within_official_domains_is_followed(net):
    net.routes["https://vd.ch/page"] = httpx.Response(301, headers={"location": "https://www.vd.ch/page"})
    net.routes["https://www.vd.ch/page"] = httpx.Response(200, text="hello")
    body = await http.fetch("https://vd.ch/page", allow=lambda u: classify(u) is not None)
    assert body == "hello"


# ---------------------------------------------------------------- resilience


def _age_cache(key: str, seconds: float) -> None:
    path = http._cache_path(key)
    entry = json.loads(path.read_text())
    entry["t"] -= seconds
    path.write_text(json.dumps(entry))


async def test_stale_copy_is_served_and_labelled_when_source_is_down(net):
    url = "https://api.example.ch/rates"
    net.routes[url] = httpx.Response(200, text="rate=1.25")
    assert await http.fetch(url, ttl=3600, check_robots=False) == "rate=1.25"
    _age_cache(f"GET {url} None None", 2 * 86400)  # expired, but younger than SGM_MAX_STALE_HOURS
    net.routes[url] = httpx.ConnectError("down")
    used = http.track_stale()
    assert await http.fetch(url, ttl=3600, check_robots=False) == "rate=1.25"
    assert used and used[0]["source"] == "api.example.ch"


async def test_too_old_copy_is_not_served(net, monkeypatch):
    url = "https://api.example.ch/old"
    net.routes[url] = httpx.Response(200, text="x")
    await http.fetch(url, ttl=60, check_robots=False)
    _age_cache(f"GET {url} None None", 30 * 86400)
    net.routes[url] = httpx.ConnectError("down")
    with pytest.raises(http.FetchError):
        await http.fetch(url, ttl=60, check_robots=False)


def test_stale_result_tells_the_assistant():
    result = server.models.ToolResult(status="ok", summary="Rate is 1.25%.", data={"rate": 1.25})
    marked = server._mark_stale(result, [{"source": "www.bwo.admin.ch", "retrieved_at": "2026-09-20T08:00"}])
    assert marked.data["stale_sources"][0]["retrieved_at"] == "2026-09-20T08:00"
    assert "2026-09-20T08:00" in marked.guidance and marked.data["rate"] == 1.25


async def test_identical_concurrent_requests_share_one_upstream_call(net):
    url = "https://api.example.ch/slow"

    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, text="once")

    net.routes[url] = slow
    bodies = await asyncio.gather(*(http.fetch(url, check_robots=False) for _ in range(5)))
    assert bodies == ["once"] * 5 and net.calls.count(url) == 1


async def test_tool_call_has_a_time_limit(client, monkeypatch):
    async def hangs(name_or_uid):
        await asyncio.sleep(5)

    monkeypatch.setattr(companies, "company_lookup", hangs)
    monkeypatch.setattr(settings, "tool_timeout_s", 0.2)
    started = time.monotonic()
    r = await call(client, "company_register", name_or_uid="Swisscom")
    assert r["status"] == "source_error" and "within 0.2 s" in r["summary"]
    assert time.monotonic() - started < 2


def test_cache_is_pruned_by_age_and_size(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    folder = tmp_path / "http" / "ab"
    folder.mkdir(parents=True)
    old, new = folder / "old.json", folder / "new.json"
    old.write_text("x" * 100)
    new.write_text("y" * 100)
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    removed, _ = http.prune_cache()
    assert removed == 1 and not old.exists() and new.exists()
    monkeypatch.setattr(settings, "cache_max_mb", 0.00005)  # ~52 bytes: the remaining file must go too
    http.prune_cache()
    assert not new.exists()


async def test_fetch_logs_leave_out_query_strings(net, caplog):
    url = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
    net.routes[url + "?searchText=Bahnhofstrasse+1+Z%C3%BCrich"] = httpx.Response(200, json={"results": []})
    with caplog.at_level(logging.INFO, logger="swiss_grounding_mcp"):
        await http.fetch(url, params={"searchText": "Bahnhofstrasse 1 Zürich"}, check_robots=False)
    assert "SearchServer" in caplog.text and "Bahnhofstrasse" not in caplog.text


# ---------------------------------------------------------------- honesty in search results


async def test_search_says_when_no_local_page_matches(client):
    """'Register a dog in Winterthur' only matched federal customs pages: the result must say so."""
    r = await call(client, "search_official_info", query="Hund anmelden", place="Winterthur")
    assert r["data"]["local_match"] is False and "None is from" in r["summary"]
    assert "No page from canton ZH or Winterthur matched" in r["guidance"]


async def test_ok_results_ask_for_the_source_url(client):
    r = await call(client, "swiss_place_info", place="Scuol")
    assert "source URL" in r["guidance"]


# ---------------------------------------------------------------- HTTP transport guards


def _asgi(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://127.0.0.1:8000")


MCP_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def test_http_token_is_required_except_for_health(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "s3cret")
    async with _asgi(server.http_app()) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.post("/mcp", json=MCP_LIST, headers=MCP_HEADERS)).status_code == 401
        wrong = {**MCP_HEADERS, "Authorization": "Bearer nope"}
        assert (await c.post("/mcp", json=MCP_LIST, headers=wrong)).status_code == 401
        assert (await c.get("/metrics")).status_code == 401


async def test_http_foreign_browser_origin_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "allowed_origins", ["https://inspector.example"])
    async with _asgi(server.http_app()) as c:
        evil = await c.post("/mcp", json=MCP_LIST, headers={**MCP_HEADERS, "Origin": "http://evil.example"})
        assert evil.status_code == 403
        listed = await c.post("/mcp", json=MCP_LIST, headers={**MCP_HEADERS, "Origin": "https://inspector.example"})
        assert listed.status_code != 403


async def test_http_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", 2)
    async with _asgi(server.http_app()) as c:
        codes = [(await c.get("/health")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_metrics_report_tool_calls(client):
    await call(client, "swiss_place_info", place="Lugano")
    async with _asgi(server.http_app()) as c:
        m = (await c.get("/metrics")).json()
    assert m["tools"]["swiss_place_info"]["calls"] >= 1 and "sources" in m


# ---------------------------------------------------------------- packaging and identity


def test_version_has_one_source():
    assert VERSION == version("swiss-grounding-mcp")


def test_user_agent_identifies_the_project_without_posing_as_a_browser():
    assert "compatible; SwissGroundingMCP/" in settings.user_agent and "Chrome" not in settings.user_agent


# ---------------------------------------------------------------- terms of use (challenge requirement)


async def test_terms_of_use_are_respected_by_default(client, net):
    """zefix.ch is a web application whose use the terms registry excludes; the UID web service is used."""
    net.routes["https://www.zefix.ch/en/search/entity/list"] = httpx.Response(200, text="<p>company</p>")
    assert settings.respect_terms is True
    r = await call(client, "read_official_page", url="https://www.zefix.ch/en/search/entity/list")
    assert r["status"] == "not_covered" and "terms of use of zefix.ch" in r["summary"]
    assert not any("zefix" in u for u in net.calls)


async def test_terms_setting_can_be_switched_off(net, monkeypatch):
    monkeypatch.setattr(settings, "respect_terms", False)
    net.routes["https://www.zefix.ch/x"] = httpx.Response(200, text="ok")
    assert await http.fetch("https://www.zefix.ch/x") == "ok"


def test_terms_setting_is_configurable(monkeypatch):
    import importlib

    from swiss_grounding_mcp import config

    monkeypatch.setenv("SGM_RESPECT_TERMS", "false")
    assert importlib.reload(config).settings.respect_terms is False
    monkeypatch.delenv("SGM_RESPECT_TERMS")
    assert importlib.reload(config).settings.respect_terms is True


# ---------------------------------------------------------------- language hint


@pytest.mark.parametrize(("question", "expected"), [
    ("Comment annoncer mon arrivée à Berne?", "fr"), ("Wo muss ich mich nach dem Umzug anmelden?", "de"),
    ("Dove devo annunciare il mio arrivo?", "it"), ("How do I register my dog?", "en"),
    ("Cura èn las vacanzas d'atun?", "rm"),
])
def test_query_language(question, expected):
    from swiss_grounding_mcp.sources.search import query_language

    assert query_language(question) == expected


def test_place_languages_come_from_the_index():
    from swiss_grounding_mcp.sources.search import place_languages

    assert place_languages("CH-BE-351") == ["de"]  # City of Bern
    assert set(place_languages("CH-BE")) == {"de", "fr"}  # bilingual canton
    assert place_languages("CH-VD-5586") == ["fr"]  # Lausanne


async def test_hint_to_search_in_the_places_language(client, monkeypatch):
    """A French question about Bern: Bern's own pages are German, so the result says to search in German."""
    monkeypatch.setattr(settings, "semantic", "off")
    r = await call(client, "search_official_info", query="Comment annoncer mon arrivée?", place="Bern")
    assert r["data"]["place_languages"] == ["de"] and "German" in r["guidance"]
    retry = await call(client, "search_official_info", query="Anmeldung Zuzug neue Adresse", place="Bern")
    assert "place_languages" not in retry["data"]
    assert any("bern.ch/themen/zuzug-umzug-wegzug" in c["url"] for c in retry["citations"])


async def test_no_hint_when_the_question_is_in_the_places_language(client, monkeypatch):
    monkeypatch.setattr(settings, "semantic", "off")
    r = await call(client, "search_official_info", query="Comment annoncer mon arrivée?", place="Lausanne")
    assert "place_languages" not in r["data"]


async def test_landing_page_names_the_mcp_endpoint():
    async with _asgi(server.http_app()) as c:
        r = await c.get("/")
    assert r.status_code == 200 and "http://127.0.0.1:8000/mcp" in r.text
