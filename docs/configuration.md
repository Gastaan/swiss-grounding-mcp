# Configuration and operations

[← Back to the README](../README.md)

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

## Monitoring

- `GET /health`: version, data build dates, index size, and `sources_with_errors` (hosts that failed
  since start). Used by the Docker `HEALTHCHECK`.
- `GET /metrics`: per tool (calls, statuses, average and maximum latency, average response size) and
  per upstream host (requests, cache hits, errors, stale copies served, last error and when).
- `.github/workflows/live-sources.yml` calls every real source daily and opens (or comments on) a
  `source-broken` issue when one fails, e.g. after a page layout change.

## Hosted instance

The public instance runs on Google Cloud Run in Zürich (`europe-west6`), deployed from this repository's
`main` branch (the revision carries the commit as the label `git-commit`):

- MCP endpoint (Streamable HTTP): `https://swiss-grounding-mcp-542630986415.europe-west6.run.app/mcp`
- Health: `https://swiss-grounding-mcp-542630986415.europe-west6.run.app/health` · landing page: `https://swiss-grounding-mcp-542630986415.europe-west6.run.app/`

No token is needed (read-only public information; rate-limited per client, browser origins refused).
It scales to zero when idle, so the first request after a pause starts an instance (about 2 s; hybrid
search follows about 10 s later, keyword search answers meanwhile). A scheduled workflow
(`.github/workflows/hosted-check.yml`) checks it every 6 hours. It is listed in the official MCP
Registry as `io.github.soheil1lotfi/swiss-grounding-mcp` (`server.json`), with this hosted endpoint and the
published Docker image (`ghcr.io/gastaan/swiss-grounding-mcp`, built by `.github/workflows/publish-image.yml`
on version tags).

To redeploy after a change to `main` (maintainers, with access to the Google Cloud project):

```sh
git archive origin/main | tar -x -C /tmp/sgm-deploy
gcloud run deploy swiss-grounding-mcp --source /tmp/sgm-deploy --region europe-west6 --memory 2Gi \
  --cpu 1 --cpu-boost --max-instances 3 --concurrency 40 --allow-unauthenticated \
  --set-env-vars 'FORWARDED_ALLOW_IPS=*'
```

The QR code in the README (`docs/assets/qr-live.svg`) encodes the landing page URL; if the URL changes,
regenerate it, e.g. `uv run --no-project --with segno python -c "import segno; segno.make('<url>', error='m').save('docs/assets/qr-live.svg', scale=4, dark='#0B1E33')"`.
