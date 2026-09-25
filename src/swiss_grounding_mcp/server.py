"""Swiss Grounding MCP server: authoritative Swiss public information for AI assistants."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from functools import wraps
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.tools import ToolResult as MCPResult
from mcp.types import TextContent
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import models
from .config import VERSION, settings
from .coverage import coverage_report
from .places import register, resolve
from .sources import companies, economy, fedlex, holidays, premiums, search, transport, votes, waste, weather
from .sources.common import place_problem

log = logging.getLogger("swiss_grounding_mcp")

INSTRUCTIONS = """\
Swiss Grounding: authoritative, cited answers about Switzerland from official Swiss sources
(federal, cantonal, municipal and bodies with a legal mandate). Every result has a `status`,
a `summary`, `citations` (URL, publisher, level, jurisdiction, date) and `guidance`.

Scope first: this is a Swiss information service. If a question is not about Switzerland (a foreign
place such as Konstanz, or a general topic unrelated to Switzerland), say clearly that it is outside the
scope of Swiss official information instead of answering from memory. Never answer Swiss questions from
memory when a tool can ground them.

How to use:
- Pick the specific tool first (premiums, law, holidays, waste, transport, votes, rates, companies,
  weather, place facts). For procedures, rules, fees and "how do I…" questions use
  search_official_info, then read_official_page for detail.
- Pass places exactly as the user wrote them (any language, postcode or address); the server resolves them.
- status=needs_context: ask the user exactly the question in `summary`, nothing more.
  status=not_covered / not_found: say clearly that it is not covered; do not guess.
  status=source_error: say the official source is unavailable and give the citation link.
- Answer in the user's language, cite the URLs, and state the reference year or date.
"""

mcp = FastMCP("swiss-grounding", instructions=INSTRUCTIONS, version=VERSION,
              website_url="https://github.com/Gastaan/swiss-grounding-mcp")

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True, "destructiveHint": False}
# Compact form of models.ToolResult's schema: it is repeated for every tool in tools/list, so field
# descriptions live in INSTRUCTIONS instead (a test checks both stay in sync).
_STR = {"type": "string"}
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "summary"],
    "properties": {
        "status": {"enum": ["ok", "needs_context", "not_covered", "not_found", "source_error"]},
        "summary": _STR,
        "data": {"type": "object"},
        "citations": {"type": "array", "items": {
            "type": "object", "required": ["title", "url", "publisher", "level", "jurisdiction"],
            "properties": {k: _STR for k in ("title", "url", "publisher", "level", "jurisdiction",
                                              "retrieved_at", "valid_for", "excerpt")}}},
        "missing_context": {"type": "array", "items": {
            "type": "object", "required": ["field", "question"],
            "properties": {"field": _STR, "question": _STR, "options": {"type": "array", "items": _STR}}}},
        "guidance": _STR,
    },
}

Place = Annotated[str | None, Field(description="Municipality, postcode, address or canton as the user said it, "
                                                 "in any language (e.g. 'Lugano', '8003', 'Genf', 'Bahnhofstrasse 1, Zürich').")]
Lang = Annotated[Literal["de", "fr", "it", "rm", "en"], Field(description="Language of the user's question.")]


def respond(result: models.ToolResult) -> MCPResult:
    if result.citations and "Source:" not in result.summary:
        c = result.citations[0]  # models echo the summary, so carry the main source in it
        result = result.model_copy(update={"summary": f"{result.summary} Source: {c.publisher} – {c.url}"})
    data = result.model_dump(exclude_none=True)
    for key in ("citations", "missing_context"):
        if not data.get(key):
            data.pop(key, None)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return MCPResult(content=[TextContent(type="text", text=text)], structured_content=data)


def tool(title: str):
    """Register a read-only tool with shared logging, output schema and error containment."""
    def deco(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            started = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:  # never leak a stack trace to the model; say what failed
                log.exception("tool %s failed", fn.__name__)
                result = models.source_error(f"The '{fn.__name__}' tool", type(e).__name__)
            out = respond(result)
            log.info("tool=%s status=%s bytes=%d ms=%d", fn.__name__, result.status,
                     len(out.content[0].text), (time.monotonic() - started) * 1000)
            return out
        return mcp.tool(title=title, annotations=READ_ONLY, output_schema=OUTPUT_SCHEMA)(wrapper)
    return deco


@tool("What this server covers")
async def swiss_coverage() -> models.ToolResult:
    """List the topics, geography, sources and freshness this server covers, and what it does not.
    Call this only when unsure whether a question is in scope."""
    return coverage_report()


def _place_citations(jurisdiction: str, meta: dict, website: str | None = None, name: str = "") -> list[models.Citation]:
    out = [
        models.Citation(title=f"STATPOP permanent resident population {meta['population_year']}",
                        url=meta["sources"]["population"], publisher="Federal Statistical Office FSO",
                        level="federal", jurisdiction=jurisdiction, valid_for=meta["population_year"],
                        retrieved_at=meta["built"]),
        models.Citation(title="Official register of Swiss municipalities", url=meta["sources"]["register"],
                        publisher="Federal Statistical Office FSO", level="federal", jurisdiction=jurisdiction,
                        retrieved_at=meta["built"]),
    ]
    if website:
        out.append(models.Citation(title=f"Official website of {name}", url=website,
                                   publisher=f"Municipality of {name}", level="municipal", jurisdiction=jurisdiction))
    return out


def _n(x: int) -> str:
    return f"{x:,}".replace(",", "'")


@tool("Swiss place facts")
async def swiss_place_info(place: Place = None) -> models.ToolResult:
    """Resolve a Swiss place to its official municipality: BFS number, canton, district, postcodes,
    permanent resident population (latest BFS figure) and official website. Use for "how many people
    live in X", "which canton is X in", or to find a municipality's website."""
    res = await resolve(place)
    meta = register()["meta"]
    if res.kind == "canton" and res.canton:
        munis = [m for m in register()["by_bfs"].values() if m.canton == res.canton]
        pop = sum(m.population or 0 for m in munis)
        return models.ToolResult(
            status="ok",
            summary=f"Canton {res.canton}: {len(munis)} municipalities, permanent resident population "
            f"{_n(pop)} ({meta['population_year']}, sum of its municipalities).",
            data={"canton": res.canton, "municipalities": len(munis), "population": pop,
                  "population_year": meta["population_year"]},
            citations=_place_citations(f"CH-{res.canton}", meta),
        )
    if problem := place_problem(res, "which municipality is meant"):
        return problem
    m = res.municipality
    return models.ToolResult(
        status="ok",
        summary=f"{m.name} is a municipality in canton {m.canton} (BFS no. {m.bfs}) with "
        f"{_n(m.population or 0)} permanent residents ({meta['population_year']}).",
        data={"municipality": m.name, "bfs_number": m.bfs, "canton": m.canton, "district": m.district,
              "postcodes": m.postcodes, "localities": m.localities[:10], "population": m.population,
              "population_year": meta["population_year"], "website": m.website,
              **({"note": res.note} if res.note else {})},
        citations=_place_citations(m.jurisdiction, meta, m.website, m.name),
    )


@tool("Search official Swiss information")
async def search_official_info(
    query: Annotated[str, Field(description="Key words of the question, ideally in the language of the "
                                            "source (e.g. 'permis de conduire étranger échanger').")],
    place: Place = None,
    language: Lang | None = None,
    limit: Annotated[int, Field(ge=1, le=8)] = 5,
) -> models.ToolResult:
    """Full-text search over official Swiss web pages: ch.ch (all languages), federal offices, the 26
    cantons and large cities. Use for procedures, rules, deadlines, fees and "how do I…" questions
    (permits, moving, taxes, social insurance, driving licences, customs, schools, housing, voting).
    Give `place` to restrict results to federal + that canton/municipality. Returns verbatim excerpts
    with URLs."""
    return await search.search_official_info(query, place, language, limit)


@tool("Read an official page")
async def read_official_page(
    url: Annotated[str, Field(description="URL on an official Swiss domain (admin.ch, ch.ch, a cantonal or "
                                          "municipal site), typically from search_official_info.")],
    focus: Annotated[str | None, Field(description="Words to focus on, e.g. 'délai 12 mois'.")] = None,
    max_chars: Annotated[int, Field(ge=500, le=8000)] = 3000,
) -> models.ToolResult:
    """Fetch the current text of an official Swiss page (live, respecting robots.txt) and return the
    passages around `focus`. Only official Swiss domains are allowed."""
    return await search.read_official_page(url, focus, max_chars)


@tool("Swiss federal law (Fedlex)")
async def swiss_federal_law(
    sr_number: Annotated[str | None, Field(description="SR number or abbreviation: '220' or 'OR'/'CO', "
                                                       "'ZGB'/'CC', 'SVG', 'VZV', 'AIG', 'KVG', 'AHVG', 'MWSTG'…")] = None,
    article: Annotated[str | None, Field(description="Article number, e.g. '335c', '42'.")] = None,
    query: Annotated[str | None, Field(description="Title words to find an act, or (with sr_number) a topic "
                                                   "to find the relevant articles, e.g. 'Kündigungsfrist'.")] = None,
    language: Lang = "de",
) -> models.ToolResult:
    """Quote federal law from the official consolidated text on Fedlex (version currently in force),
    with article links. Federal law only; cantonal law is not included."""
    return await fedlex.swiss_law(sr_number, query, article, language)


@tool("Health insurance premiums (KVG/LAMal)")
async def health_insurance_premiums(
    place: Place = None,
    age: Annotated[int | None, Field(ge=0, le=120, description="Age of the insured person.")] = None,
    franchise: Annotated[int | None, Field(description="Deductible in CHF (adults 300-2500, children 0-600). "
                                                       "Omit to get the cheapest offer for every deductible.")] = None,
    accident_cover: Annotated[bool, Field(description="Include accident cover (default no: employees are "
                                                      "insured by their employer).")] = False,
    insurance_model: Annotated[Literal["free_choice_of_doctor", "family_doctor", "hmo", "telmed_or_other"] | None,
                               Field(description="Only if the user explicitly asks for one insurance model. "
                                                 "Leave empty to compare all models (the cheapest is usually an "
                                                 "alternative model).")] = None,
    year: Annotated[int | None, Field(description="Premium year; default current year.")] = None,
) -> models.ToolResult:
    """Cheapest mandatory basic health insurance premiums (Grundversicherung, assurance de base,
    assicurazione di base — every plan here is basic insurance) for a municipality, age and deductible,
    from the official FOPH premium data (same data as priminfo.admin.ch)."""
    return await premiums.premiums(place, age, franchise, accident_cover, insurance_model, year)


@tool("School and public holidays")
async def swiss_holidays(
    place: Place = None,
    year: Annotated[int | None, Field(description="Calendar year; default current year.")] = None,
    kind: Annotated[Literal["school", "public"], Field(description="School holidays or public holidays.")] = "school",
    language: Lang = "de",
) -> models.ToolResult:
    """School holidays by canton or municipality, or public holidays, for a year."""
    return await holidays.holidays(place, year, kind, language)


@tool("Waste collection dates")
async def waste_collection(
    place: Place = None,
    waste_type: Annotated[str | None, Field(description="e.g. Karton/carton/cartone, Papier, Kehricht, "
                                                        "Grüngut, Metall. Omit for all types.")] = None,
    street: Annotated[str | None, Field(description="Street (and number) if the user gave one.")] = None,
    from_date: Annotated[str | None, Field(description="ISO date to start from; default today.")] = None,
) -> models.ToolResult:
    """Next waste collection dates (cardboard, paper, household waste, green waste…) from municipal open
    data. Needs the municipality; some cities also need postcode or street (the tool will say)."""
    return await waste.waste_collection(place, waste_type, street, from_date)


@tool("Public transport timetable")
async def public_transport(
    origin: Annotated[str | None, Field(description="Departure station or place, e.g. 'Zürich HB'.")] = None,
    destination: Annotated[str | None, Field(description="Destination; omit for a departure board.")] = None,
    when: Annotated[str | None, Field(description="ISO date-time (Europe/Zurich), e.g. 2026-10-01T08:30; "
                                                  "default now.")] = None,
    arrival: Annotated[bool, Field(description="Interpret `when` as arrival time.")] = False,
    limit: Annotated[int, Field(ge=1, le=6)] = 3,
) -> models.ToolResult:
    """Swiss public transport connections or next departures (official timetable data)."""
    return await transport.public_transport(origin, destination, when, arrival, limit)


@tool("Federal votes")
async def federal_votes(
    vote_date: Annotated[str | None, Field(description="ISO date of a vote; omit for the next vote "
                                                       "(or the latest if none is scheduled).")] = None,
    place: Annotated[str | None, Field(description="Canton to add its result, optional.")] = None,
    language: Lang = "de",
) -> models.ToolResult:
    """Subjects of the next federal popular vote, or official results of a past vote."""
    return await votes.federal_votes(vote_date, place, language)


@tool("Reference interest rate and exchange rates")
async def swiss_rates(
    kind: Annotated[Literal["reference_interest_rate", "exchange_rate"],
                    Field(description="Mortgage reference rate for rents (BWO), or SNB exchange rate.")],
    currency: Annotated[str | None, Field(description="For exchange_rate: EUR, USD, GBP…")] = None,
    language: Lang = "de",
) -> models.ToolResult:
    """Current mortgage reference interest rate for rents (BWO) or SNB CHF exchange rates."""
    if kind == "reference_interest_rate":
        return await economy.reference_interest_rate(language)
    return await economy.exchange_rate(currency)


@tool("Company register (UID)")
async def company_register(
    name_or_uid: Annotated[str, Field(description="Company name or UID (CHE-123.456.789).")],
) -> models.ToolResult:
    """Check whether a company is registered: UID, legal seat, commercial register and VAT status,
    from the federal UID register."""
    return await companies.company_lookup(name_or_uid)


@tool("Current weather (MeteoSwiss)")
async def current_weather(place: Place = None, language: Lang = "de") -> models.ToolResult:
    """Latest measured weather (temperature, humidity, precipitation, wind) at the MeteoSwiss station
    nearest to a Swiss municipality. Measurements only, no forecasts."""
    return await weather.current_weather(place, language)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    meta = search.index_meta()
    return JSONResponse({
        "status": "ok", "version": VERSION, "respect_robots": settings.respect_robots,
        "index_built": meta.get("built"), "index_pages": meta.get("pages"),
        "places_built": register()["meta"]["built"], "premium_years": premiums.available_years(),
    })


def main() -> None:
    parser = argparse.ArgumentParser(prog="swiss-grounding-mcp", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.getenv("SGM_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    # logs go to stderr: stdout is the MCP channel in stdio mode
    logging.basicConfig(level=os.getenv("SGM_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port, path="/mcp", stateless_http=True,
                show_banner=False)
    else:
        mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
