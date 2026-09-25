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
uv sync --extra semantic                                                  # dependencies, with hybrid search
uv run --extra semantic swiss-grounding-mcp                               # stdio (for local MCP clients)
uv run --extra semantic swiss-grounding-mcp --transport http --port 8000  # HTTP: http://localhost:8000/mcp
curl localhost:8000/health                                                # "search": "hybrid (…)"
```

The prebuilt data (municipality register, health premiums, search index, passage vectors) ships in
the repository; the first start unpacks the index (~1 s) and downloads the small embedding model once
(~240 MB, in the background: searches use keywords until it is ready). No keys, nothing to configure.

Lighter install without hybrid search (no model, ~130 MB fewer packages): drop `--extra semantic` from
the commands above and from the client configuration below. See [Search](#search) for the difference.

Docker:

```sh
docker build -t swiss-grounding-mcp .
docker run -p 8000:8000 swiss-grounding-mcp               # http://localhost:8000/mcp
```

The image (~1.4 GB, including hybrid search and its model) listens on all interfaces
(`HOST=0.0.0.0`) as an unprivileged user and never downloads anything at runtime. When its port is
reachable from outside a trusted network, add `-e SGM_AUTH_TOKEN=<secret>` (see [HTTP security](#http-security)).

Published image (GitHub Container Registry, same build): `docker run -p 8000:8000 ghcr.io/gastaan/swiss-grounding-mcp`
over HTTP, or `docker run -i --rm ghcr.io/gastaan/swiss-grounding-mcp --transport stdio` for an MCP
client that starts the server itself.

Without cloning (keyword search): `uvx --from git+https://github.com/Gastaan/swiss-grounding-mcp swiss-grounding-mcp`.

## Hosted endpoint

A public instance runs on Google Cloud Run in Zürich (`europe-west6`), deployed from this repository's
`main` branch (the revision carries the commit as the label `git-commit`):

- MCP endpoint (Streamable HTTP): `https://swiss-grounding-mcp-542630986415.europe-west6.run.app/mcp`
- Health: `https://swiss-grounding-mcp-542630986415.europe-west6.run.app/health` · landing page: `https://swiss-grounding-mcp-542630986415.europe-west6.run.app/`

```sh
claude mcp add --transport http swiss https://swiss-grounding-mcp-542630986415.europe-west6.run.app/mcp
```

No token is needed (read-only public information; rate-limited per client, browser origins refused).
It scales to zero when idle, so the first request after a pause starts an instance (about 2 s; hybrid
search follows about 10 s later, keyword search answers meanwhile). A scheduled workflow
(`.github/workflows/hosted-check.yml`) checks it every 6 hours. It is listed in the official MCP
Registry as `io.github.soheil1lotfi/swiss-grounding-mcp` (`server.json`), with this hosted endpoint and the
published Docker image. The code in this repository runs
locally with the setup below; the hosted instance runs the same commit.

To redeploy after a change to `main` (maintainers, with access to the Google Cloud project):

```sh
git archive origin/main | tar -x -C /tmp/sgm-deploy
gcloud run deploy swiss-grounding-mcp --source /tmp/sgm-deploy --region europe-west6 --memory 2Gi \
  --cpu 1 --cpu-boost --max-instances 3 --concurrency 40 --allow-unauthenticated \
  --set-env-vars 'FORWARDED_ALLOW_IPS=*'
```

## Connect an MCP client

Use absolute paths. Replace `/path/to/swiss-grounding-mcp` with your clone.

**OpenCode** (`opencode.json`):
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "swiss": {"type": "local", "command": ["uv", "run", "--extra", "semantic", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"], "enabled": true, "timeout": 30000},
    "swiss-http": {"type": "remote", "url": "http://localhost:8000/mcp", "enabled": false, "timeout": 30000}
  }
}
```
Set `timeout`: OpenCode waits only 5 s for an MCP request by default, while a slow official source can
take longer; the server ends every tool call within `SGM_TOOL_TIMEOUT` (30 s) with a clean
`source_error`, so a client timeout of 30 s or more lets that answer arrive.

**Claude Code:**
```sh
claude mcp add swiss -- uv run --extra semantic --directory /path/to/swiss-grounding-mcp swiss-grounding-mcp
claude mcp add --transport http swiss-http http://localhost:8000/mcp
```

**Claude Desktop** (`claude_desktop_config.json`; give the full path to `uv`, e.g. from `which uv`):
```json
{"mcpServers": {"swiss": {"command": "/full/path/to/uv", "args": ["run", "--extra", "semantic", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"]}}}
```

**VS Code** (`.vscode/mcp.json`):
```json
{"servers": {"swiss": {"type": "stdio", "command": "uv", "args": ["run", "--extra", "semantic", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"]}}}
```

**Cursor** (`.cursor/mcp.json`):
```json
{"mcpServers": {"swiss": {"command": "uv", "args": ["run", "--extra", "semantic", "--directory", "/path/to/swiss-grounding-mcp", "swiss-grounding-mcp"]}}}
```

Any client that speaks MCP over stdio or Streamable HTTP works. Tested with Claude Code, OpenCode,
the MCP Inspector CLI and the FastMCP client, including the legacy `initialize` handshake
(protocol 2025-06-18) and the stateless 2026-07-28 protocol. The server sends usage `instructions`.

## Coverage (declared scope)

| Topic | Geography | Source (authority) | Freshness / reference period |
|---|---|---|---|
| Procedures, rules, fees, deadlines — permits & migration, moving & registration, taxes, social insurance (AHV/IV), unemployment, driving licences & vehicles, customs & parcels, schools, housing, voting, civil status… | Federal (ch.ch in de/fr/it/rm/en, federal offices, AHV/IV, arbeit.swiss), **cantonal portals of 23 cantons** (see limitations), city pages of Lucerne, Lugano, Winterthur, Biel/Bienne, St. Gallen, Bern, Geneva, Lausanne and Thun | Full-text index of **10,630 official pages / 46,952 passages**, plus live reading of any official page | Index built 2026-09-25, refreshed weekly; `read_official_page` fetches live text |
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
  the block honestly). VS (7 pages), TI (45) and TG (52) are only partly indexed.
- Municipal web pages are indexed for Lucerne, Lugano, Winterthur, Biel/Bienne, St. Gallen, Bern, Geneva, Lausanne and Thun (120–250 pages each; Lausanne 66, Thun 32). Zürich has
  only a few pages, and Bellinzona and Fribourg none yet (the next refresh crawls Fribourg's own domain,
  ville-fribourg.ch). For other municipalities the server returns the official website and can read a
  given page live; waste, holidays, premiums and place facts cover all municipalities through their tools.
- Cantonal **law texts**, individual **tax calculations** and **weather forecasts** are not provided.
- Waste calendars exist only where municipalities publish open data (list above); elsewhere the
  server says so and links the municipality.
- School holidays come from OpenHolidays, which aggregates official lists. Where the index holds the
  responsible authority's own calendar (e.g. ge.ch, bern.ch, the school of Scuol), that page is cited
  first; the EDK list and the municipality site are cited alongside. Periods are labelled by school type
  where a canton publishes several (canton Bern: German- and French-speaking schools).
- Registering on arrival in Lausanne is weak: the city's residents' office page is not in the index.
- A question in another language than the place's pages first returns a language hint, not the page:
  the assistant has to search again. In end-to-end runs of such a question (Q17, French question about
  Bern), Sonnet followed the hint and answered from Bern's page; Haiku did so in 1 of 4 runs.
- Search is keyword-based by default. With the optional `semantic` extra it is hybrid and also finds
  pages worded differently or written in another language, but still misses some (see [Search](#search));
  Romansh is not covered by the embedding model.

## Challenge self-check

The organisers' [practice cases](https://github.com/Swiss-ai-Weeks/swisscom-2026/tree/main/swiss-grounding-mcp#submission-self-check-pack)
(read from the published file; their launcher was not run), checked against this server:

| Practice case | Behaviour |
|---|---|
| Cardboard collection, no place given | asks only for the municipality (`needs_context`), never a date |
| Geneva school holidays 2026 | cites `ge.ch/vacances-scolaires-2026-2027` first, no ask-back; periods the official page confirms are marked `on_official_page` |
| Licence fee in Konstanz | "Konstanz is in Germany, Swiss sources do not apply" (`not_covered`) |
| Romansh: autumn holidays in Scuol | 10–25 Oct 2026, citing the Scuol school's own 2026/27 plan |
| Current reference interest rate | BWO page, 1.25 % with its effective date, cached at most 6 h |
| Registering on arrival in Lausanne, then Bern | Bern: the French question gets a hint that Bern publishes in German; searching again in German finds the city's own page (`bern.ch/themen/zuzug-umzug-wegzug`). Lausanne: the city's residents' office page is not indexed, so answers rest on the federal ch.ch page and the canton's pages |
| Which source supports a deadline | every citation carries the verbatim passage (`excerpt`) |
| Source unavailable | a cached copy is labelled with its date (`data.stale_sources`), otherwise `source_error` with the link |

**End to end** (`scripts/e2e_eval.py --questions eval/practice_questions.json`, Claude Code, answers
also reviewed by hand against the pack's criteria, `eval/results/2026-09-25T0704.md`): Sonnet 11/11,
Haiku 10/11. The practice cases are in `eval/practice_questions.json`, together with the pack's three
extra sample questions and a source-failure case (the server offline with an empty cache: both models
said the housing office was unreachable, gave its link and did not guess a rate). Haiku's miss: the
notice-period answer is right but cites ch.ch instead of the article on Fedlex. Reviewing the answers
found one error the automated checks had passed: the City of Bern's autumn holidays were given with the
French-speaking schools' dates. Holiday periods are now checked against the official page (the one on
bern.ch confirms 19 September to 11 October 2026) and school types are named by language, and both
models answer correctly.

Robots.txt and terms of use are respected by default and both are configurable (`SGM_RESPECT_ROBOTS`,
`SGM_RESPECT_TERMS`, see Configuration). No credentials are needed.

## Search

`search_official_info` ranks passages of the index with SQLite FTS5/BM25 plus rules (the user's
language, the most specific jurisdiction, how many query words a passage covers). With the optional
`semantic` extra it becomes **hybrid**: a local multilingual embedding model
(`paraphrase-multilingual-MiniLM-L12-v2`, ONNX, no API key) ranks the same passages by meaning, and the
two rankings are merged (reciprocal-rank fusion, with the same preference for cantonal and municipal
pages when a place is given). The two best keyword hits keep their places, so exact matches are never
pushed out: with only one, passages about a neighbouring rule (the travellers' CHF 150 allowance) took
the place of the CHF 5 parcel rule.

```sh
uv sync --extra semantic      # then start the server as usual; /health shows "search": "hybrid (…)"
```

Measured with `scripts/search_eval.py`: 38 hand-labelled questions whose answer is in the index (including
the challenge's practice cases), plus 5 without one. A question counts as answered when an official page on its topic is among the 5 results
the tool returns; for the "rule" questions the excerpt itself must state the rule (the CHF 5 parcel-VAT
rule behind end-to-end question Q12).

| Question kind | Keyword only | Hybrid |
|---|---|---|
| Uses the page's own words (9) | 9 | 9 |
| Same language, other words (9) | 5 | 6 |
| Another language than the only official page (15) | 2 | 8 |
| The rule itself in an excerpt (5 phrasings of Q12) | 3 | 2 |
| **Answered (38)** | **19** | **25** |
| No correct page exists: says so instead of passing off another page (5) | 5 | 5 |

For example, "exchange my foreign driving licence" in Lausanne now finds Vaud's French-only page, and
"register my dog" in Basel finds the German one. Still missed: the Romansh school calendar of Scuol,
German or English questions about Lausanne's French waste calendar, and parcel-VAT questions worded
with terms the official pages do not use ("Freigrenze", French "colis").

**Honesty is kept:** a result only counts as evidence if it covers at least half of the query's words
or is close in meaning (cosine ≥ 0.7). With no such result the tool answers `not_found`, as before.
The threshold was set so that none of the questions without a correct page gets through by similarity
alone.

**Costs:** ~240 MB for the model (downloaded once into `SGM_MODEL_DIR`, or at image build time in
Docker) and ~130 MB of Python packages (`fastembed`, `onnxruntime`, `numpy` and their dependencies); `data/embeddings.npz` adds 12 MB
(int8 vectors); about 20 ms per search; the weekly refresh re-embeds all passages (~15 min on a laptop,
longer on CI runners). Without the extra, or with `SGM_SEMANTIC=off`, nothing of this is loaded.
The server only uses embeddings built from the exact index it serves, and falls back to keyword search
if the vector part fails.

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
- `data.stale_sources` appears when a live source was down and an earlier cached copy was used (at most
  `SGM_MAX_STALE_HOURS` old); `guidance` then tells the assistant to give the user that date.
- `search_official_info` sets `data.local_match: false` when a place was given but no cantonal or
  municipal page matched as well as the federal ones; the summary says so and the municipality's
  website is cited, so a federal page is not passed off as the local rule.
- `search_official_info` sets `data.place_languages` (e.g. `["de"]`) when the question is in another
  language than the place publishes in and none of the place's own pages matched. The summary and
  guidance then ask the assistant to search again with its key words translated into that language: a
  French question about registering in Bern leads to a German search that finds Bern's own page. The
  place's languages come from the language of its pages in the index.
- The output schema lists the top-level fields only; the fields inside `citations` and
  `missing_context` are described once in the server instructions, which keeps `tools/list` small.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SGM_RESPECT_ROBOTS` | `true` | **Respect robots.txt** of every website fetched (RFC 9309, via Protego), on every redirect hop. Set `false` to disable. |
| `SGM_RESPECT_TERMS` | `true` | **Respect terms of use** recorded in `data/source_terms.json`: hosts whose terms do not allow automated access are never fetched (e.g. the zefix.ch web application; company data comes from the official UID register web service). Set `false` to disable. |
| `SGM_OFFLINE` | `false` | Serve from cache only, never hit the network. |
| `SGM_CACHE_DIR` | `~/.cache/swiss-grounding-mcp` | HTTP response cache. |
| `SGM_CACHE_MAX_DAYS`, `SGM_CACHE_MAX_MB` | `30`, `500` | At startup, entries untouched this long are deleted, then the oldest until the cache fits. |
| `SGM_MAX_STALE_HOURS` | `168` | When a source is down, serve an expired cached copy up to this old, labelled as stale. `0` disables. |
| `SGM_HTTP_TIMEOUT` | `15` | Seconds per upstream request. |
| `SGM_TOOL_TIMEOUT` | `30` | Seconds for a whole tool call, however many upstream requests it makes. |
| `SGM_MIN_INTERVAL` | `0.5` | Minimum seconds between requests to the same host. |
| `SGM_USER_AGENT` | `Mozilla/5.0 (compatible; SwissGroundingMCP/<version>; +<repo URL>)` | Standard crawler form: names the project and links to it. |
| `SGM_TRANSPORT`, `HOST`, `PORT` | `stdio`, `127.0.0.1`, `8000` | Same as `--transport/--host/--port`. The Docker image sets `HOST=0.0.0.0`. |
| `SGM_AUTH_TOKEN` | unset | HTTP only: require `Authorization: Bearer <token>` on `/mcp` and `/metrics` (`/health` stays open). |
| `SGM_ALLOWED_ORIGINS` | none | HTTP only: comma-separated browser origins allowed to call `/mcp` (e.g. a web inspector). |
| `SGM_RATE_LIMIT` | `600` | HTTP only: requests per minute per client IP. `0` disables. |
| `SGM_SEMANTIC` | `auto` | `auto`: hybrid search when the `semantic` extra and `data/embeddings.npz` are present; `off`: keyword only. |
| `SGM_MODEL_DIR` | `~/.cache/swiss-grounding-mcp/models` | Where the embedding model is stored (the Docker image bakes it into `/app/models`). |
| `SGM_LOG_LEVEL` | `INFO` | Logs go to stderr: one line per tool call (tool, status, bytes, ms) and per upstream fetch (host and path, never query strings). |

No credentials are required to use the sources.

## Source etiquette, caching, resilience

- robots.txt is checked per host and cached for 24 h; an unreachable robots.txt (5xx) means "disallow".
  Documented APIs (geo.admin.ch, Fedlex SPARQL, SNB, OpenHolidays, transport.opendata.ch, open-data
  portals) are called as APIs; website pages always go through the robots check.
- Requests identify the project in the User-Agent (standard crawler form, not a browser string) and
  are paced per host (`SGM_MIN_INTERVAL`). Identical requests already in flight share one upstream call.
- Responses are cached on disk with per-source TTLs (timetables 1 min, weather and votes 10 min,
  pages and rates 6 h, waste 12 h, register 1 day, law and holidays 7 days) and pruned at startup.
- **When a source is down**, an expired copy up to `SGM_MAX_STALE_HOURS` old is served instead of an
  error, and the result says so (`data.stale_sources`, with the retrieval date). Without a usable copy
  the result is `status: "source_error"` with the official link; no stack traces reach the model.
- **Every tool call has a time limit** (`SGM_TOOL_TIMEOUT`), so a chain of slow upstream calls ends in
  a clear `source_error` instead of a client timeout.
- `read_official_page` only reads recognised Swiss government domains (admin.ch, ch.ch, 26 cantons,
  about 2,090 municipal websites, bodies with a legal mandate). **Redirects are followed one hop at a time**
  and each target is checked again (allowlist, robots.txt, and never a private or reserved address),
  so an official URL cannot lead to another site or into an internal network.

## HTTP security

The stdio transport has no network exposure. For `--transport http`:

- The server listens on `127.0.0.1` unless `HOST`/`--host` says otherwise, and logs a warning when it
  listens on another interface without a token.
- **Origin check (DNS-rebinding protection):** browser requests whose `Origin` is not in
  `SGM_ALLOWED_ORIGINS` get `403`. MCP clients send no `Origin` header and are unaffected. On localhost
  the `Host` header is checked as well.
- **Optional bearer token:** with `SGM_AUTH_TOKEN` set, `/mcp` and `/metrics` answer `401` without
  `Authorization: Bearer <token>`. Clients pass it as a header, e.g.
  `claude mcp add --transport http swiss-http http://host:8000/mcp --header "Authorization: Bearer <token>"`.
- **Rate limit:** `SGM_RATE_LIMIT` requests per minute per client IP (token bucket), `429` with
  `Retry-After` beyond that.
- The Docker image runs as an unprivileged user.

## Monitoring

- `GET /health`: version, data build dates, index size, and `sources_with_errors` (hosts that failed
  since start). Used by the Docker `HEALTHCHECK`.
- `GET /metrics`: per tool (calls, statuses, average and maximum latency, average response size) and
  per upstream host (requests, cache hits, errors, stale copies served, last error and when).
- `.github/workflows/live-sources.yml` calls every real source daily and opens (or comments on) a
  `source-broken` issue when one fails, e.g. after a page layout change.

## Data and refresh

| File (in `src/swiss_grounding_mcp/data/`) | Built by | Contents |
|---|---|---|
| `places.json` | `scripts/build_places.py` | 2,110 municipalities: BFS number, canton, district, premium region, postcodes, localities, population, website, holiday region |
| `premiums_<year>.csv.gz`, `premium_meta_<year>.json` | `scripts/build_premiums.py 2026 2027` | Official FOPH premium table, insurer and plan names, municipality restrictions |
| `index.sqlite.gz` | `scripts/build_index.py` | FTS5 index of official pages (robots.txt respected, per-host pacing) |
| `embeddings.npz` | `scripts/build_embeddings.py` | Vectors of every index passage for hybrid search (int8), tagged with the index they belong to |

```sh
uv run --group build python scripts/build_places.py
uv run --group build python scripts/build_premiums.py 2026 2027
uv run --group build python scripts/build_index.py      # ~20 min cold, a few minutes when cached
uv run --extra semantic --group build python scripts/build_embeddings.py   # ~15 min, after the index
uv run python scripts/relabel_index.py                  # after changing authorities.py, without a rebuild
```

`.github/workflows/refresh-data.yml` rebuilds everything weekly (the embeddings right after the index, so they always match) and opens a pull request that includes the search-quality numbers.

Municipal websites come from Wikidata, which anyone can edit, and they decide which domains count as
official. So only `.ch`/`.swiss` addresses that are not on a free hosting platform are accepted
(`places.official_website`), reviewed exceptions live in `data/website_overrides.json`, and every
weekly pull request lists each website that changed or was rejected, for review before merging.

The data files are committed so that a clone runs offline and every build is reproducible. The cost
is repository size (the index is ~19 MB compressed per refresh); if that becomes a problem, the files
can move to release assets downloaded on first start.

## Architecture

```
MCP client ──stdio / Streamable HTTP──▶ server.py (FastMCP, 13 tools, one ToolResult contract)
                                          │
      places.py (register, exonyms, postcodes, geocoding) · authorities.py (domain → level/jurisdiction)
                                          │
  sources/  search (FTS5 index + live pages) · fedlex · premiums · holidays · waste · transport
            votes · economy · companies · weather
                                          │
   http.py: robots.txt · per-host pacing · disk cache with TTL · stale fallback · safe redirects
                                          │
      guards.py (HTTP only): bearer token · rate limit   + FastMCP Host/Origin check
```

Python 3.13, `fastmcp` 4 (on the official `mcp` 2.x SDK), `httpx`, `protego`, `trafilatura`, SQLite FTS5.
### Adding a source

1. Write `sources/<topic>.py` with one async function that returns `models.ToolResult`. Resolve places
   with `places.resolve()` and hand non-municipality results to `sources.common.place_problem()`, so
   ambiguous, foreign and unknown places behave like everywhere else.
2. Fetch only through `http.fetch()`/`fetch_json()` with a TTL that matches how often the source
   changes; pass `check_robots=False` only for documented APIs.
3. Cite every fact: publisher, `level`, `jurisdiction`, `retrieved_at`/`valid_for`, and a verbatim
   `excerpt` where there is one. Use `status` honestly (`not_covered` rather than a guess).
4. Register it in `server.py` with `@tool("Title")` and a short docstring. Parameter descriptions are
   sent on every connection, so keep them brief; `test_tool_list_is_compact_and_read_only` caps the size.
5. Add a row to `coverage.py` and to the coverage table above, an offline test in `tests/`, and a
   live check in `tests/test_live.py` (the daily workflow then watches it).

## Testing

```sh
uv run pytest -q              # offline: contract, places, premiums, honesty, redirects, stale copies, HTTP guards
uv run --extra semantic pytest -q                      # the same, plus the real embedding model
uv run --extra semantic python scripts/search_eval.py  # search quality, keyword vs hybrid
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

### Compared with Claude without this server

The same 28 questions (the 17 above and the organisers' 11 practice cases), Claude Code with Sonnet,
three ways that differ only in their tools (same neutral assistant prompt, `--assistant-prompt`):
this server; web search and fetch, no MCP server (`claude-web`); no tools at all (`claude-base`).
Results: `eval/results/2026-09-25T111906.md` and `…T112059.md` (practice runs first blocked by the
account's spend limit were rerun, so every mode has all 28 answers).

| Mode | 17 questions | Practice cases | Avg calls (17 q.) | Avg seconds (17 q.) | Links to official Swiss authorities (28 answers) |
|---|---|---|---|---|---|
| **With this server** | **16/17** | **11/11** | 1.2 | 14 | **94%** of 34 links |
| Web search, no server | 11/17 | 7/11 | 2.4 searches | 28 | 72% of 67 links (others: Wikipedia, bonus.ch, bern.com, jurawelt.com…) |
| Model knowledge only | 6/17 | 3/11 | 0 | 15 | 1 link in 28 answers |

Reading the answers: web search often reaches the right figure but mixes in comparison sites, news and
tourism pages, rounds official figures ("circa 45.000" inhabitants of Bellinzona instead of the FSO's
45'828), dates the reference rate by its publication rather than its effective date, answers "where I
live" questions by saying it has no access instead of asking for the municipality, and gives canton-wide
advice where the city's own page applies (registering in Bern). It is also slower: twice the calls and
twice the time. Without any tools the model mostly declines or answers from dated knowledge.
Question Q15 ("capital of Australia") is answered from general knowledge in all three modes.

### Latest end-to-end results (2026-09-25, `eval/results/2026-09-25T0606.md`, hybrid search)

All four runs of the evaluation setup (2 clients x 2 LLMs), scored with the current checks:

| Client + LLM | Pass | Avg tool calls | Avg seconds | First run (`…T0159`) |
|---|---|---|---|---|
| Claude Code + Sonnet | 15/16 | 1.0 | 13 | 14/16 |
| Claude Code + Haiku | 13/16 | 0.9 | 12 | 14/16 |
| OpenCode + gpt-5.4-mini | 15/16 | 1.4 | 9 | 13/16 |
| OpenCode + gpt-4.1-mini | 14/16 | 1.0 | 8 | 13/16 |

Checks: asking back may be a polite request as well as a question; Q12 accepts ch.ch's customs pages,
which state the CHF 5 rule word for word; Q15 ("capital of Australia") follows the challenge text for a
question not about Switzerland: the right response is to say so, without calling the Swiss server.

Remaining misses, honestly reported:

- **Q15 (all four):** the models answer "Canberra" from general knowledge. This run was recorded when
  the server instructions only said not to call its tools for such questions; they now also say to state
  that the question is outside this Swiss service. Rerun with Claude after that change: both still answer
  from general knowledge without calling any tool, so the server never sees the question. The
  challenge's own non-Swiss sample (S5, a fee after moving to Konstanz) passes in all four, because
  there the model does ask the server and gets `not_covered`.
- **Q12 (Haiku, gpt-4.1-mini):** the parcel-VAT question. The CHF 5 parcel rule is in the tool's
  results, but these models apply a neighbouring rule (the travellers' CHF 150 allowance, or "import tax
  on every parcel"). The larger models answer it correctly.
- **Q7 (Haiku):** correct answer, cited from a search result instead of the law text on Fedlex.

Runs are not deterministic: the same model can pass or miss a question between runs (Haiku passed Q7
and Q12 in `…T0552`).

Q17 (French question about registering in Bern, testing the language hint) was added after this run:
Sonnet passed it by searching again in German; Haiku passed 1 of 4 runs.

### Earlier end-to-end results (2026-09-25, `eval/results/2026-09-25T0159.md`)

| Client + LLM | Pass (content + citation) | Content correct | Published samples | Avg tool calls | Avg seconds |
|---|---|---|---|---|---|
| Claude Code + Sonnet | 14/16 | 14/16 | 5/5 | 1.2 | 12 |
| Claude Code + Haiku | 14/16 | 15/16 | 5/5 | 0.9 | 11 |
| OpenCode + gpt-5.4-mini | 13/16 | 15/16 | 4/5 | 1.4 | 9 |
| OpenCode + gpt-4.1-mini | 13/16 | 14/16 | 5/5 | 1.0 | 7 |

Remaining misses, honestly reported: some answers are correct but name the source without the URL
(Q8, one S3 run); two answers to the parcel-VAT question (Q12) reach the right conclusion with the
travellers' allowance instead of the CHF 5 parcel rule; and a general non-Swiss question (Q15,
"capital of Australia") is answered from model knowledge without calling any tool, which the check at
the time counted as a miss (it now counts as correct, see above; the published non-Swiss sample S5,
Konstanz, passes in all four).


## License

Code: MIT. Data retrieved from the sources remains under their terms (Swiss open government data,
mostly "open use, must provide the source"); every answer carries its source.
