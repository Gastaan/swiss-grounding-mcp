"""Build src/swiss_grounding_mcp/data/index.sqlite — full-text index of authoritative Swiss pages.

What gets indexed (robots.txt respected, per-host pacing, cached so re-runs are cheap):
  - ch.ch, the Confederation's citizen portal: every page in de/fr/it/rm/en (sitemap)
  - cantonal portals, federal offices and semi-official bodies: sitemap pages whose URL matches
    citizen topics (tax, permits, driving licence, school, holidays, waste, social insurance…)
  - the websites of the largest cities, same topic filter
  - SEED_URLS: hand-picked pages that must always be present

Run: uv run --group build python scripts/build_index.py [--limit-per-domain 250]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import html as htmllib
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import trafilatura

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swiss_grounding_mcp import http  # noqa: E402
from swiss_grounding_mcp.authorities import classify  # noqa: E402
from swiss_grounding_mcp.config import DATA_DIR  # noqa: E402

BUILD_TTL = 30 * http.DAY

TOPIC = re.compile(
    r"steuer|fuehrerausweis|fuhrerausweis|fahrzeug|strassenverkehr|verkehr|schul|ferien|einwohner|"
    r"anmeld|umzug|zuzug|wegzug|aufenthalt|bewilligung|auslaend|migration|abfall|entsorg|kehricht|"
    r"abfuhr|recycl|krankenvers|praemie|pramie|verbillig|ahv|rente|vorsorge|arbeitslos|rav|stelle|"
    r"miete|wohn|referenzzins|abstimm|wahl|handelsregister|unternehm|mwst|zoll|einfuhr|paket|zivilstand|"
    r"heirat|ehe|geburt|todesfall|hund|parkier|einbuerger|buergerrecht|gebuehr|sozialhilfe|familie|"
    r"kinder|ausweis|pass|identitaet|bildung|lehre|studium|gesundheit|impot|fiscal|permis|conduire|"
    r"vehicule|circulation|automobile|ecole|scolaire|vacances|habitant|arrivee|demenag|domicil|sejour|"
    r"autorisation|etranger|dechet|ramassage|voirie|assurance|prime|subside|avs|chomage|orp|loyer|"
    r"logement|bail|vot|election|registre|entreprise|tva|douane|colis|importation|etat-civil|mariage|"
    r"naissance|deces|chien|stationnement|naturalisation|emolument|aide-sociale|allocation|passeport|"
    r"identite|formation|sante|impost|licenza|condurre|veicol|circolazione|scuol|vacanze|abitant|"
    r"trasloc|permesso|dimora|stranier|migrazion|rifiut|raccolta|ecocentro|premi|sussidi|disoccupaz|"
    r"affitt|allogg|locazione|votazion|elezion|registro|impresa|iva|dogana|pacc|stato-civile|matrimon|"
    r"nascit|decess|cane|naturalizzaz|tass|assistenza|famigli|assegn|passaport|identita|scola|vacanzas",
    re.I,
)

CANTON_SITES = [
    "https://www.zh.ch", "https://www.be.ch", "https://www.lu.ch", "https://www.ur.ch", "https://www.sz.ch",
    "https://www.ow.ch", "https://www.nw.ch", "https://www.gl.ch", "https://zg.ch", "https://www.fr.ch",
    "https://so.ch", "https://www.bs.ch", "https://www.baselland.ch", "https://sh.ch", "https://ar.ch",
    "https://www.ai.ch", "https://www.sg.ch", "https://www.gr.ch", "https://www.ag.ch", "https://www.tg.ch",
    "https://www4.ti.ch", "https://www.vd.ch",
    # Bern publishes most content on directorate subdomains
    "https://www.sv.fin.be.ch", "https://www.svsa.sid.be.ch", "https://www.bkd.be.ch", "https://www.sid.be.ch",
    "https://www.gsi.be.ch", "https://www.dij.be.ch", "https://www.vs.ch", "https://www.ne.ch", "https://www.ge.ch",
    "https://www.jura.ch",
]
FEDERAL_SITES = [
    "https://www.sem.admin.ch", "https://www.bwo.admin.ch", "https://www.bazg.admin.ch",
    "https://www.bakom.admin.ch", "https://www.estv.admin.ch", "https://www.bsv.admin.ch",
    "https://www.bk.admin.ch", "https://www.bag.admin.ch", "https://www.seco.admin.ch",
    "https://www.astra.admin.ch", "https://www.bj.admin.ch", "https://www.ahv-iv.ch",
    "https://www.arbeit.swiss", "https://www.admin.ch",
]
CITY_SITES = [
    "https://www.stadt-zuerich.ch", "https://www.geneve.ch", "https://www.lausanne.ch", "https://www.bern.ch",
    "https://stadt.winterthur.ch", "https://www.stadtluzern.ch", "https://www.stadt.sg.ch",
    "https://www.lugano.ch", "https://www.biel-bienne.ch", "https://www.thun.ch", "https://www.bellinzona.ch",
    "https://www.ville-fribourg.ch",  # the city's own domain (fribourg.ch is not the municipality's)
]
SEED_URLS = [
    "https://www.vd.ch/mobilite/automobile-et-navigation/permis/echanger-un-permis-etranger",
    "https://www.vd.ch/prestation/echanger-un-permis-de-conduire-etranger",
    "https://www.zh.ch/de/mobilitaet/fuehrerausweis-fahren-lernen/auslaendischer-fuehrerausweis/auslaendischen-fuehrerausweis-umtauschen.html",
    "https://www.svsa.sid.be.ch/de/start/fuehrerausweise/rund-um-fuehrerausweis/umtausch-fuehrerausweis-ausland.html",
    "https://www.ge.ch/echanger-son-permis-conduire-etranger",
    "https://www4.ti.ch/di/sc/conducenti/licenza-di-condurre-estera",
    "https://www.bwo.admin.ch/de/referenzzinssatz",
    "https://www.bwo.admin.ch/fr/taux-dinteret-de-reference",
    "https://www.bazg.admin.ch/de/empfangen-von-briefen-und-paketen",
    "https://www.bazg.admin.ch/de/interneteinkauf-postsendungen-informationen-einfuhr",
    "https://www.bakom.admin.ch/de/hoehe-der-abgabe-und-rechnungsstellung",
    "https://www.serafe.ch/de/",
    "https://www.serafe.ch/fr/",
    "https://www.serafe.ch/it/",
    "https://www.scoula-scuol.ch/plan-da-scoula-e-vacanzas/plan-da-scoula-e-vacanzas-2026-27/",
    "https://www.edk.ch/de/bildungssystem/kantonale-schulorganisation/Schulferien/ferienlisten",
    "https://www.admin.ch/de/abstimmungstermine",
    "https://www.priminfo.admin.ch/de/praemien",
    "https://www.lausanne.ch/ramassage",
]

LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
HREF = re.compile(r'href="([^"#]+)"')
SKIP_EXT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|jpe?g|png|gif|svg|mp4|mp3|ics|xml|json|css|js)(\?|$)", re.I)


def lang_of(url: str, html: str) -> str:
    m = re.search(r"/(de|fr|it|rm|en)(/|$|-)", urlsplit(url).path)
    if m:
        return m.group(1)
    m = re.search(r'<html[^>]*\blang="([a-zA-Z]{2})', html)
    return m.group(1).lower() if m else "und"


async def sitemap_urls(sitemap: str, depth: int = 0) -> list[str]:
    try:
        body = await http.fetch(sitemap, ttl=BUILD_TTL)
    except http.FetchError:
        return []
    locs = [x.replace("&amp;", "&") for x in LOC.findall(body)]
    if "<sitemapindex" in body and depth < 2:
        out: list[str] = []
        for sub in locs:
            out += await sitemap_urls(sub, depth + 1)
        return out
    return locs


async def site_sitemaps(site: str) -> list[str]:
    try:
        robots = await http.fetch(f"{site}/robots.txt", ttl=BUILD_TTL, check_robots=False)
        maps = re.findall(r"(?im)^sitemap:\s*(\S+)", robots)
    except http.FetchError:
        maps = []
    return [m for m in maps if not m.endswith(".gz")] or [f"{site}/sitemap.xml"]


async def link_crawl(site: str, limit: int) -> list[str]:
    """Shallow crawl (depth 2) from the home page, keeping topic-matching same-site links."""
    host = urlsplit(site).hostname
    seen, frontier, found = {site}, [site], []
    for _depth in range(2):
        nxt = []
        for url in frontier:
            try:
                html = await http.fetch(url, ttl=BUILD_TTL)
            except http.FetchError:
                continue
            for href in HREF.findall(html):
                link = urljoin(url, href).split("#")[0]
                if urlsplit(link).hostname != host or link in seen or SKIP_EXT.search(link):
                    continue
                seen.add(link)
                if TOPIC.search(urlsplit(link).path):
                    found.append(link)
                    nxt.append(link)
            if len(found) >= limit:
                return found[:limit]
        frontier = nxt[:40]
    return found[:limit]


async def candidates(site: str, limit: int, topic_filter: bool = True) -> list[str]:
    urls: list[str] = []
    for sm in await site_sitemaps(site):
        urls += await sitemap_urls(sm)
    urls = [u for u in dict.fromkeys(urls) if not SKIP_EXT.search(u)]
    unfiltered = urls
    if topic_filter:
        urls = [u for u in urls if TOPIC.search(urlsplit(u).path)]
        # prefer pages whose path matches many topics, then shorter (more general) pages
        urls.sort(key=lambda u: (-len(TOPIC.findall(urlsplit(u).path)), len(u)))
        if len(urls) < 20:
            # opaque URL schemes (e.g. /dienstleistungen/12345) defeat the path filter: take the
            # sitemap unfiltered, shortest (most general) pages first; ranking handles relevance
            urls = sorted(set(unfiltered), key=len) if unfiltered else await link_crawl(site, limit)
    urls = list(dict.fromkeys(urls))
    return urls[:limit] if limit else urls


def chunks(markdown: str, max_len: int = 900) -> list[tuple[str, str]]:
    """Split trafilatura markdown into (heading, passage) pairs."""
    out, heading, buf = [], "", ""
    for block in re.split(r"\n\s*\n", markdown):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            if buf:
                out.append((heading, buf))
                buf = ""
            heading = block.lstrip("#").strip()[:200]
            continue
        if buf and len(buf) + len(block) > max_len:
            out.append((heading, buf))
            buf = ""
        buf = f"{buf}\n{block}" if buf else block[: max_len * 2]
    if buf:
        out.append((heading, buf))
    return out


async def process(url: str) -> dict | None:
    auth = classify(url)
    if not auth:
        return None
    try:
        html = await http.fetch(url, ttl=BUILD_TTL)
    except http.FetchError as e:
        print(f"  skip {url}: {e}", file=sys.stderr)
        return None
    text = await asyncio.to_thread(
        trafilatura.extract, html, url=url, output_format="markdown", include_tables=True,
        include_links=False, favor_precision=True,
    )
    if not text or len(text) < 200:
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else url
    return {
        "url": url, "title": htmllib.unescape(title)[:300], "lang": lang_of(url, html),
        "publisher": auth.publisher, "level": auth.level, "jurisdiction": auth.jurisdiction,
        "passages": chunks(text),
    }


def write_db(pages: list[dict]) -> Path:
    # Drop boilerplate passages that repeat on many pages (menus, cookie banners).
    counts = Counter(hashlib.sha1(body.encode()).digest() for p in pages for _, body in p["passages"])
    out = DATA_DIR / "index.sqlite"
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE pages(id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, lang TEXT,
                           publisher TEXT, level TEXT, jurisdiction TEXT, fetched_at TEXT);
        CREATE VIRTUAL TABLE passages USING fts5(title, heading, body, page_id UNINDEXED,
                           tokenize="unicode61 remove_diacritics 2");
        """
    )
    today = date.today().isoformat()
    n_pass = 0
    for p in pages:
        cur = db.execute(
            "INSERT OR IGNORE INTO pages(url,title,lang,publisher,level,jurisdiction,fetched_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (p["url"], p["title"], p["lang"], p["publisher"], p["level"], p["jurisdiction"], today),
        )
        if not cur.rowcount:
            continue
        for heading, body in p["passages"]:
            if counts[hashlib.sha1(body.encode()).digest()] > 3:
                continue
            db.execute("INSERT INTO passages(title,heading,body,page_id) VALUES(?,?,?,?)",
                       (p["title"], heading, body, cur.lastrowid))
            n_pass += 1
    db.executemany("INSERT INTO meta VALUES(?,?)",
                   [("built", today), ("pages", str(len(pages))), ("passages", str(n_pass))])
    db.commit()
    db.execute("INSERT INTO passages(passages) VALUES('optimize')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    with open(out, "rb") as src, gzip.open(out.with_suffix(".sqlite.gz"), "wb", compresslevel=9) as dst:
        dst.write(src.read())
    return out


async def main(limit: int) -> None:
    started = time.time()
    print("collecting ch.ch…", flush=True)
    plan = await candidates("https://www.ch.ch", 0, topic_filter=False)
    for group in (FEDERAL_SITES, CANTON_SITES, CITY_SITES):
        results = await asyncio.gather(*(candidates(s, limit) for s in group))
        for site, urls in zip(group, results, strict=False):
            print(f"  {site}: {len(urls)} urls", flush=True)
            plan += urls
    plan = list(dict.fromkeys(SEED_URLS + plan))
    # Interleave hosts round-robin: requests to one host are paced, so grouping by host would leave
    # every worker waiting on the same host.
    by_host: dict[str, list[str]] = {}
    for u in plan:
        by_host.setdefault(urlsplit(u).hostname or "", []).append(u)
    queues = list(by_host.values())
    plan = [q[i] for i in range(max(map(len, queues))) for q in queues if i < len(q)]
    print(f"{len(plan)} pages to fetch from {len(queues)} hosts", flush=True)

    sem = asyncio.Semaphore(24)
    done = 0

    async def run(u: str):
        nonlocal done
        async with sem:
            r = await process(u)
        done += 1
        if done % 250 == 0:
            print(f"  {done}/{len(plan)} ({time.time() - started:.0f}s)", flush=True)
        return r

    pages = [p for p in await asyncio.gather(*(run(u) for u in plan)) if p]
    out = write_db(pages)
    print(f"indexed {len(pages)} pages in {time.time() - started:.0f}s; "
          f"levels={dict(Counter(p['level'] for p in pages))} "
          f"langs={dict(Counter(p['lang'] for p in pages))}; {out.stat().st_size // 1_000_000} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-per-domain", type=int, default=250)
    asyncio.run(main(ap.parse_args().limit_per_domain))
