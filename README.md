# Swiss Grounding MCP

An MCP server that gives AI assistants **authoritative, cited, jurisdiction-correct answers about
Switzerland** — from federal, cantonal and municipal sources, in German, French, Italian, Romansh
and English. Built for the Swisscom *Swiss Grounding MCP* challenge (Swiss AI Weeks, Zurich 2026).

- **No API keys, no accounts.** Everything is public Swiss open data or official web pages.
- **13 read-only tools**, one response contract, citations on every result.
- **Honest by design:** asks back only for missing essentials (e.g. the municipality), says clearly
  when something is not covered or not in Switzerland, never fills gaps from memory.
- Runs locally over **stdio** or as a **Streamable HTTP** service (`/mcp`, `/health`).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 automatically).

```sh
git clone https://github.com/Gastaan/swiss-grounding-mcp && cd swiss-grounding-mcp
uv sync                                                  # installs dependencies
uv run swiss-grounding-mcp                               # stdio (for local MCP clients)
uv run swiss-grounding-mcp --transport http --port 8000  # HTTP: http://localhost:8000/mcp
curl localhost:8000/health
```

The prebuilt data (municipality register, health premiums, search index) ships in the repository;
the first start unpacks the index (~1 s). Nothing else to download or configure.

Docker:

```sh
docker build -t swiss-grounding-mcp .
docker run -p 8000:8000 swiss-grounding-mcp               # http://localhost:8000/mcp
```

## Connect an MCP client

Use absolute paths. Replace `/path/to/swiss-grounding-mcp` with your clone.

**OpenCode** (`opencode.json`):
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "swiss": {"type": "local", "command": ["uv", "run", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"], "enabled": true},
    "swiss-http": {"type": "remote", "url": "http://localhost:8000/mcp", "enabled": false}
  }
}
```

**Claude Code:**
```sh
claude mcp add swiss -- uv run --directory /path/to/swiss-grounding-mcp swiss-grounding-mcp
claude mcp add --transport http swiss-http http://localhost:8000/mcp
```

**Claude Desktop** (`claude_desktop_config.json`; give the full path to `uv`, e.g. from `which uv`):
```json
{"mcpServers": {"swiss": {"command": "/full/path/to/uv", "args": ["run", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"]}}}
```

**VS Code** (`.vscode/mcp.json`):
```json
{"servers": {"swiss": {"type": "stdio", "command": "uv", "args": ["run", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"]}}}
```

**Cursor** (`.cursor/mcp.json`):
```json
{"mcpServers": {"swiss": {"command": "uv", "args": ["run", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"]}}}
```

Any client that speaks MCP over stdio or Streamable HTTP works. Tested with Claude Code, OpenCode,
the MCP Inspector CLI and the FastMCP client, including the legacy `initialize` handshake
(protocol 2025-06-18) and the stateless 2026-07-28 protocol. The server sends usage `instructions`.

## Coverage (declared scope)

| Topic | Geography | Source (authority) | Freshness / reference period |
|---|---|---|---|
| Procedures, rules, fees, deadlines — permits & migration, moving & registration, taxes, social insurance (AHV/IV), unemployment, driving licences & vehicles, customs & parcels, schools, housing, voting, civil status… | Federal (ch.ch in de/fr/it/rm/en, federal offices, AHV/IV, arbeit.swiss), **cantonal portals of 23 cantons** (see limitations), 12 largest cities | Full-text index of **10,630 official pages / 46,952 passages**, plus live reading of any official page | Index built 2026-09-25, refreshed weekly; `read_official_page` fetches live text |
| Federal law — any act and article, current consolidated version | Federal | Fedlex (Federal Chancellery) | Live; version in force today |
| Mandatory health insurance premiums (cheapest offers per municipality, age, deductible, model) | All 2,110 municipalities (premium regions) | FOPH premium open data (same data as priminfo.admin.ch) | 2026 premiums; 2027 added when FOPH publishes them (end of September) |
| School holidays and public holidays | All 26 cantons; municipality level where published (e.g. Scuol, Zürich) | OpenHolidays (aggregated official lists), EDK list, municipality website | 2025–2027 |
| Waste collection dates | City of Zürich (by postcode), Basel/Riehen/Bettingen (by address), St. Gallen (by street); by collection zone: Winterthur, Uster, Wetzikon, Dübendorf, Horgen, Wädenswil, Adliswil, Thalwil and 13 more | Municipal open data (ERZ Zürich via OpenERZ, data.bs.ch, daten.stadt.sg.ch) | Live, next 120 days |
| Public transport connections and departure boards | All of Switzerland | Official timetable (opentransportdata.swiss via transport.opendata.ch) | Live |
| Federal popular votes — upcoming subjects, results (national + canton) | Federal | FSO vote-day open data, Federal Chancellery | Live |
| Mortgage reference interest rate (rents); SNB exchange rates | Federal | BWO; Swiss National Bank | Live (cached 6 h) |
| Company registration — UID, legal seat, commercial register and VAT status | All of Switzerland | Federal UID register (FSO) | Live |
| Place facts — municipality, canton, BFS number, postcodes, population, official website | All 2,110 municipalities, 26 cantons | BFS register & STATPOP, swisstopo, Wikidata (websites) | Population 2025; register 2026-09-25 |
| Current weather measurements | Nearest MeteoSwiss automatic station | MeteoSwiss open data | Live (10-minute values) |

### Not covered / limitations

- **Anything outside Switzerland** — e.g. the German *Rundfunkbeitrag* in Konstanz. The server says so.
- **Cantons GR, BL and SH** block or do not serve text to automated clients, so their cantonal
  pages are not in the index (ch.ch and federal pages still apply; `read_official_page` reports
  the block honestly). VS and TI are only partially indexed.
- Municipal web pages are indexed only for the 12 largest cities; for other municipalities the
  server returns the official website and can read a given page live.
- Cantonal **law texts**, individual **tax calculations** and **weather forecasts** are not provided.
- Waste calendars exist only where municipalities publish open data (list above); elsewhere the
  server says so and links the municipality.
- School holidays come from OpenHolidays, which aggregates official lists; the official EDK list and
  the municipality site are cited alongside for verification.
- Search is keyword-based (SQLite FTS5/BM25 over all national languages); it works best with the
  key nouns of the question, in any national language.

## Tools

| Tool | Use it for |
|---|---|
| `search_official_info` | "How do I…", rules, deadlines, fees — verbatim excerpts from official pages, filtered to federal + the given canton/municipality |
| `read_official_page` | Live text of an official page (allowlisted Swiss government domains only), focused on given words |
| `swiss_federal_law` | Federal law articles (SR number or abbreviation such as OR/CO, ZGB, SVG, AIG) |
| `health_insurance_premiums` | Cheapest KVG/LAMal premiums |
| `swiss_holidays` | School or public holidays |
| `waste_collection` | Next collection dates by waste type |
| `public_transport` | Connections and departures |
| `federal_votes` | Next vote subjects or results |
| `swiss_rates` | Reference interest rate, SNB exchange rates |
| `company_register` | Company lookup by name or UID |
| `swiss_place_info` | Municipality facts, population, website |
| `current_weather` | Latest MeteoSwiss measurements |
| `swiss_coverage` | The scope above, for the assistant |

### Response contract

Every tool returns the same JSON object (as `structuredContent` with an `outputSchema`, and as text):

```json
{
  "status": "ok | needs_context | not_covered | not_found | source_error",
  "summary": "One factual sentence, ending with 'Source: <publisher> – <url>'",
  "data": {"...": "tool-specific facts"},
  "citations": [{"title": "", "url": "", "publisher": "", "level": "federal|cantonal|municipal|semi-official|community",
                 "jurisdiction": "CH | CH-VD | CH-VD-5586", "retrieved_at": "", "valid_for": "", "excerpt": "verbatim"}],
  "missing_context": [{"field": "municipality", "question": "…", "options": ["Buchs (ZH)", "Buchs (SG)"]}],
  "guidance": "what the assistant should do next"
}
```

- `needs_context` — ask the user exactly `summary` (e.g. which municipality; which of three *Buchs*).
- `not_covered` — outside Switzerland or outside the declared scope; the assistant should say so.
- `not_found` / `source_error` — nothing found / source unreachable; never answer from memory.
- Places can be given as the user wrote them: *Genf, Ginevra, Genève*, *Schuls → Scuol*, *8003*,
  *Zurich 8003*, *Bahnhofstrasse 1, Zürich*. Ambiguous names return options; foreign places are flagged.
- Invalid arguments are returned with `isError: true` and a readable message.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SGM_RESPECT_ROBOTS` | `true` | **Respect robots.txt** of every website fetched (RFC 9309, via Protego). Set `false` to disable. |
| `SGM_RESPECT_TERMS` | `true` | Only use sources whose terms allow automated access (e.g. zefix.ch's web backend is not used; the official UID web service is). |
| `SGM_OFFLINE` | `false` | Serve from cache only, never hit the network. |
| `SGM_CACHE_DIR` | `~/.cache/swiss-grounding-mcp` | HTTP response cache. |
| `SGM_HTTP_TIMEOUT` | `15` | Seconds per upstream request. |
| `SGM_MIN_INTERVAL` | `0.5` | Minimum seconds between requests to the same host. |
| `SGM_USER_AGENT` | browser-compatible string identifying `SwissGroundingMCP/<version> (+repo URL)` | Sent with every request. |
| `SGM_TRANSPORT`, `HOST`, `PORT` | `stdio`, `0.0.0.0`, `8000` | Same as `--transport/--host/--port`. |
| `SGM_LOG_LEVEL` | `INFO` | Logs go to stderr: one line per tool call (tool, status, bytes, ms) and per upstream fetch. |

No credentials are required.

## Source etiquette, caching, resilience

- robots.txt is checked per host and cached for 24 h; an unreachable robots.txt (5xx) means "disallow".
  Documented APIs (geo.admin.ch, Fedlex SPARQL, SNB, OpenHolidays, transport.opendata.ch, open-data
  portals) are called as APIs; website pages always go through the robots check.
- Requests to the same host are paced (`SGM_MIN_INTERVAL`), identify the project in the User-Agent,
  and are cached on disk with per-source TTLs (timetables 1 min, votes 10 min, rates 6 h, pages 6–24 h,
  law 7 days). Expired entries are never served except in `SGM_OFFLINE` mode.
- Upstream failures become `status: "source_error"` with the official link; no stack traces reach the model.
- `read_official_page` only accepts recognised Swiss government domains (admin.ch, ch.ch, 26 cantons,
  2,100+ municipal websites, bodies with a legal mandate), which also prevents SSRF.

## Data and refresh

| File (in `src/swiss_grounding_mcp/data/`) | Built by | Contents |
|---|---|---|
| `places.json` | `scripts/build_places.py` | 2,110 municipalities: BFS number, canton, district, premium region, postcodes, localities, population, website, holiday region |
| `premiums_<year>.csv.gz`, `premium_meta_<year>.json` | `scripts/build_premiums.py 2026 2027` | Official FOPH premium table, insurer and plan names, municipality restrictions |
| `index.sqlite.gz` | `scripts/build_index.py` | FTS5 index of official pages (robots.txt respected, per-host pacing) |

```sh
uv run --group build python scripts/build_places.py
uv run --group build python scripts/build_premiums.py 2026 2027
uv run --group build python scripts/build_index.py      # ~20 min cold, a few minutes when cached
```

`.github/workflows/refresh-data.yml` rebuilds everything weekly and opens a pull request.

## Architecture

```
MCP client ──stdio / Streamable HTTP──▶ server.py (FastMCP, 13 tools, one ToolResult contract)
                                          │
      places.py (register, exonyms, postcodes, geocoding) · authorities.py (domain → level/jurisdiction)
                                          │
  sources/  search (FTS5 index + live pages) · fedlex · premiums · holidays · waste · transport
            votes · economy · companies · weather
                                          │
                  http.py: robots.txt · per-host pacing · disk cache with TTL
```

Python 3.13, `fastmcp` 4 (on the official `mcp` 2.x SDK), `httpx`, `protego`, `trafilatura`, SQLite FTS5.
Adding a source = one module in `sources/` returning `ToolResult`, plus one decorated function in `server.py`.

## Testing

```sh
uv run pytest -q              # offline: MCP contract, place resolution, premiums, honesty rules
uv run pytest -q -m live      # live checks against every real source
uv run ruff check src scripts tests
npx -y -p node@22 -p @modelcontextprotocol/inspector -- mcp-inspector --cli http://127.0.0.1:8000/mcp -- --method tools/list
```

End-to-end with real MCP clients and LLMs (mirrors the evaluation: 2 clients × 2 LLMs):

```sh
OPENAI_API_KEY=... uv run python scripts/e2e_eval.py    # Claude Code (sonnet, haiku) + OpenCode (gpt-5.4-mini, gpt-4.1-mini)
```

It runs `eval/questions.json` — the 5 published sample questions plus 11 more (de/fr/it/rm/en,
including ask-back, out-of-scope and not-covered cases) — and writes `eval/results/<date>.md`.

### Latest end-to-end results (2026-09-25, `eval/results/2026-09-25T0159.md`)

| Client + LLM | Pass (content + citation) | Content correct | Published samples | Avg tool calls | Avg seconds |
|---|---|---|---|---|---|
| Claude Code + Sonnet | 14/16 | 14/16 | 5/5 | 1.2 | 12 |
| Claude Code + Haiku | 14/16 | 15/16 | 5/5 | 0.9 | 11 |
| OpenCode + gpt-5.4-mini | 13/16 | 15/16 | 4/5 | 1.4 | 9 |
| OpenCode + gpt-4.1-mini | 13/16 | 14/16 | 5/5 | 1.0 | 7 |

Remaining misses, honestly reported: some answers are correct but name the source without the URL
(Q8, one S3 run); two answers to the parcel-VAT question (Q12) reach the right conclusion with the
travellers' allowance instead of the CHF 5 parcel rule; and a general non-Swiss question (Q15,
"capital of Australia") is answered from model knowledge without calling any tool — a server cannot
intercept questions it is never asked (the published non-Swiss sample S5, Konstanz, passes in all four).


## License

Code: MIT. Data retrieved from the sources remains under their terms (Swiss open government data,
mostly "open use, must provide the source"); every answer carries its source.
