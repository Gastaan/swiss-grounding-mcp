# Development

[← Back to the README](../README.md)

## Architecture

The [README diagram](assets/architecture.svg) shows the request flow; inside the server:

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

## Adding a source

1. Write `sources/<topic>.py` with one async function that returns `models.ToolResult`. Resolve places
   with `places.resolve()` and hand non-municipality results to `sources.common.place_problem()`, so
   ambiguous, foreign and unknown places behave like everywhere else.
2. Fetch only through `http.fetch()`/`fetch_json()` with a TTL that matches how often the source
   changes; pass `check_robots=False` only for documented APIs.
3. Cite every fact: publisher, `level`, `jurisdiction`, `retrieved_at`/`valid_for`, and a verbatim
   `excerpt` where there is one. Use `status` honestly (`not_covered` rather than a guess).
4. Register it in `server.py` with `@tool("Title")` and a short docstring. Parameter descriptions are
   sent on every connection, so keep them brief; `test_tool_list_is_compact_and_read_only` caps the size.
5. Add a row to `coverage.py` and to the [coverage table in the README](../README.md#coverage), an
   offline test in `tests/`, and a live check in `tests/test_live.py` (the daily workflow then watches it).

## Testing

```sh
uv run pytest -q              # offline: contract, places, premiums, honesty, redirects, stale copies, HTTP guards
uv run --extra semantic pytest -q                      # the same, plus the real embedding model
uv run --extra semantic python scripts/search_eval.py  # search quality, keyword vs hybrid
uv run pytest -q -m live      # live checks against every real source
uv run ruff check src scripts tests
npx -y -p node@22 -p @modelcontextprotocol/inspector -- mcp-inspector --cli http://127.0.0.1:8000/mcp -- --method tools/list
```

End-to-end runs with real MCP clients and LLMs, and their results: [evaluation](evaluation.md).
