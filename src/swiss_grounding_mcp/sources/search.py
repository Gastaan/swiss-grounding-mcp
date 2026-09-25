"""Search the prebuilt index of official pages, and read live pages from official domains."""

from __future__ import annotations

import asyncio
import gzip
import re
import shutil
import sqlite3
import threading
import unicodedata
from functools import cache

import trafilatura

from .. import http
from ..authorities import classify
from ..config import DATA_DIR, settings
from ..models import Citation, ToolResult, source_error, today
from ..places import resolve

STOPWORDS = set(
    """
    der die das den dem des ein eine einer eines einem und oder ist sind wird werden wie was wann wo wer
    welche welcher welches ich du er sie wir ihr mein meine mich mir bei uns unser für fur mit von zu zum
    zur im in am an auf aus nach über uber um nicht kann muss soll darf viel hoch hat habe haben es
    le la les un une des du de et ou est sont comment quand quel quelle quels quelles je tu il nous vous
    mon ma mes pour avec dans sur par en au aux pas peut dois faut combien ce cette ces qui que quoi
    lo gli uno dei del della di da e o è sono come quando quale quali io noi voi mio
    mia per con nel nella su non può devo quanto quanta che cosa
    ils las ina u èn cura co tge sin cun mes nus vus quant
    the a an of and or is are how when what which who where i my me you your we our for with to on
    at from not can must should much many does do
    schweiz suisse svizzera svizra switzerland swiss kanton canton cantone chantun
    """.split()
)
MAX_EXCERPT = 700

_lock = threading.Lock()


def _unpack(gz, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".unpack")
    with gzip.open(gz, "rb") as src, open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(path)


@cache
def _db_path() -> str:
    """The repository ships index.sqlite.gz; unpack it once (next to it, or in the cache dir when the
    package directory is read-only)."""
    gz = DATA_DIR / "index.sqlite.gz"
    with _lock:
        for path in (DATA_DIR / "index.sqlite", settings.cache_dir / "index.sqlite"):
            if not gz.exists() or (path.exists() and path.stat().st_mtime >= gz.stat().st_mtime):
                if path.exists():
                    return str(path)
                continue
            try:
                _unpack(gz, path)
                return str(path)
            except OSError:
                continue
    return str(DATA_DIR / "index.sqlite")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True, check_same_thread=False)


def index_meta() -> dict:
    try:
        with _connect() as db:
            return dict(db.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return {}


def _terms(text: str) -> list[str]:
    """Query words reduced to prefixes (crude stemming: covers plural/case endings and compounds)."""
    text = unicodedata.normalize("NFKC", text.lower())
    out = []
    for tok in re.findall(r"[^\W\d_]{2,}|\d{3,}", text):
        if tok not in STOPWORDS:
            out.append(tok[: max(4, len(tok) - 2)] if len(tok) >= 5 else tok)
    return list(dict.fromkeys(out))


def _fts_query(text: str) -> str:
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in _terms(text))


def _run_search(fts: str, jurisdictions: list[str] | None, language: str | None, limit: int,
                terms: list[str] | None = None, exact: list[str] | None = None) -> list[dict]:
    sql = """
        SELECT p.url, p.title, p.lang, p.publisher, p.level, p.jurisdiction, p.fetched_at,
               passages.heading, passages.body, bm25(passages, 8.0, 4.0, 1.0) AS score
        FROM passages JOIN pages p ON p.id = passages.page_id
        WHERE passages MATCH ?
    """
    params: list = [fts]
    if jurisdictions:
        sql += f" AND p.jurisdiction IN ({','.join('?' * len(jurisdictions))})"
        params += jurisdictions
    sql += " ORDER BY score LIMIT 200"
    with _connect() as db:
        rows = db.execute(sql, params).fetchall()
    hits = []
    for url, title, lang, publisher, level, jur, fetched, heading, body, score in rows:
        # prefer the user's language and, when a place is known, the most specific jurisdiction
        bonus = (1.5 if language and lang == language else 0) + (1.0 if jurisdictions and "-" in jur else 0)
        coverage = 1.0
        if terms:  # reward passages (with their page title/heading) that cover more distinct query terms
            hay = f"{title} {heading} {body}".lower()
            matched = [t for t in terms if t in hay]
            coverage = len(matched) / len(terms)
            bonus += 2.0 * len(matched)
            bonus += 1.0 * sum(1 for w in exact or [] if re.search(rf"\b{re.escape(w)}\b", hay))
        hits.append({
            "url": url, "title": title, "language": lang, "publisher": publisher, "level": level,
            "jurisdiction": jur, "retrieved_at": fetched, "heading": heading, "body": body,
            "rank": score - bonus, "coverage": coverage,
        })
    hits.sort(key=lambda h: h["rank"])
    out, per_page = [], {}
    for h in hits:
        if per_page.get(h["url"], 0) >= 2:
            continue
        per_page[h["url"]] = per_page.get(h["url"], 0) + 1
        out.append(h)
        if len(out) >= limit:
            break
    return out


async def search_official_info(
    query: str, place: str | None = None, language: str | None = None, limit: int = 5
) -> ToolResult:
    fts = _fts_query(query)
    if not fts:
        return ToolResult(status="not_found", summary="The query contains no searchable words.",
                          guidance="Rephrase with the key nouns of the question (e.g. 'Führerausweis umtauschen').")
    jurisdictions: list[str] | None = None
    place_words: list[str] = []
    scope_note = "all of Switzerland (no place given)"
    if place:
        res = await resolve(place)
        if res.kind == "foreign":
            return ToolResult(
                status="not_covered",
                summary=f"{place} is in {res.country}, not in Switzerland; Swiss official sources do not apply.",
                guidance="Tell the user this is outside Switzerland and outside this server's scope. "
                "Do not answer from memory.",
            )
        if res.kind == "municipality":
            m = res.municipality
            jurisdictions = ["CH", f"CH-{m.canton}", m.jurisdiction]
            place_words = [m.name.lower(), (place or "").lower()]
            scope_note = f"federal + canton {m.canton} + municipality {m.name}"
        elif res.kind == "canton" and res.canton:
            jurisdictions = ["CH", f"CH-{res.canton}"]
            place_words = [(place or "").lower()]
            scope_note = f"federal + canton {res.canton}"
    try:
        words = [w for w in re.findall(r"[^\W\d_]{3,}", query.lower()) if w not in STOPWORDS]
        terms = [t for t in _terms(query) if not any(t in p for p in place_words)]  # place is already a filter
        hits = await asyncio.to_thread(_run_search, fts, jurisdictions, language, limit, terms, words)
    except sqlite3.Error as e:
        return source_error("The search index", e)
    if not hits:
        return ToolResult(
            status="not_found",
            summary=f"No passage in the index of official Swiss pages matches this query (scope: {scope_note}).",
            data={"fts_query": fts, "scope": scope_note},
            guidance="Try different keywords or another national language (the index holds de/fr/it/rm/en "
            "pages). If still nothing, tell the user this is not covered rather than guessing.",
        )
    if len(terms) >= 2 and max(h["coverage"] for h in hits) < 0.5:
        weak = hits[:2]
        return ToolResult(
            status="not_found",
            summary=f"No official page in the index answers this (scope: {scope_note}); closest pages listed.",
            data={"scope": scope_note, "closest": [{"title": h["title"], "url": h["url"]} for h in weak]},
            citations=[Citation(title=h["title"], url=h["url"], publisher=h["publisher"], level=h["level"],
                                jurisdiction=h["jurisdiction"], retrieved_at=h["retrieved_at"]) for h in weak],
            guidance="Try at most one reformulation (other keywords or another national language). If that "
            "fails too, tell the user this is not covered and point to the responsible official website "
            "(e.g. the municipality's site from swiss_place_info). Do not guess.",
        )
    cantons = sorted({h["jurisdiction"].split("-")[1] for h in hits if "-" in h["jurisdiction"]})
    citations = [
        Citation(
            title=h["title"] + (f" — {h['heading']}" if h["heading"] else ""),
            url=h["url"], publisher=h["publisher"], level=h["level"], jurisdiction=h["jurisdiction"],
            retrieved_at=h["retrieved_at"], excerpt=h["body"][:MAX_EXCERPT],
        )
        for h in hits
    ]
    guidance = (
        "Answer only from these excerpts and cite their URLs. Prefer the most specific jurisdiction. "
        "If a fact may have changed since retrieved_at (fees, rates, dates) or the excerpt is cut, "
        "call read_official_page(url) for the live text. If the excerpts do not answer the question, "
        "search at most once more, then say it is not covered and give the most relevant official link."
    )
    if not place and len(cantons) > 1:
        guidance += (f" Results come from several cantons ({', '.join(cantons)}): if the answer depends on "
                     "the canton and the user did not say which, ask for it.")
    return ToolResult(
        status="ok",
        summary=f"{len(hits)} official passages found (scope: {scope_note}).",
        data={"scope": scope_note, "index_built": index_meta().get("built")},
        citations=citations,
        guidance=guidance,
    )


def _focus(text: str, focus: str | None, max_chars: int) -> str:
    if not focus:
        return text[:max_chars]
    words = [w[: max(4, len(w) - 2)] for w in re.findall(r"[^\W\d_]{3,}|\d+", focus.lower()) if w not in STOPWORDS]
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    scored = [(sum(b.lower().count(w) for w in words), i) for i, b in enumerate(blocks)]
    keep: set[int] = set()
    for score, i in sorted(scored, reverse=True):
        if score == 0:
            break
        keep |= {i - 1, i, i + 1}  # neighbours carry the heading and continuation
        if sum(len(blocks[j]) for j in keep if 0 <= j < len(blocks)) > max_chars:
            break
    if not keep:
        return text[:max_chars]
    return "\n\n".join(blocks[j] for j in sorted(keep) if 0 <= j < len(blocks))[:max_chars]


async def read_official_page(url: str, focus: str | None = None, max_chars: int = 4000) -> ToolResult:
    auth = classify(url)
    if not auth:
        return ToolResult(
            status="not_covered",
            summary="This URL is not on a recognised official Swiss domain (admin.ch, ch.ch, cantonal or "
            "municipal websites, bodies with a legal mandate), so it is not read.",
            guidance="Use search_official_info to find the official page instead.",
        )
    cite = Citation(title=url, url=url, publisher=auth.publisher, level=auth.level, jurisdiction=auth.jurisdiction)
    try:
        html = await http.fetch(url, ttl=6 * http.HOUR)
    except http.RobotsDisallowed as e:
        return ToolResult(
            status="not_covered",
            summary=f"The site's robots.txt does not allow automated access to this page ({e}).",
            citations=[cite], guidance="Give the user the link to read it themselves.",
        )
    except http.FetchError as e:
        return source_error("The page", e, [cite])
    text = await asyncio.to_thread(
        trafilatura.extract, html, url=url, output_format="markdown", include_tables=True, include_links=False
    )
    if not text:
        return ToolResult(status="not_found", citations=[cite],
                          summary="The page has no readable text (it may be script-only or a document).")
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else url
    excerpt = _focus(text, focus, max(500, min(max_chars, 8000)))
    return ToolResult(
        status="ok",
        summary=f"Live text of '{title}' ({auth.publisher}), {len(excerpt)} of {len(text)} characters"
        + (f" around '{focus}'." if focus else "."),
        data={"text": excerpt, "truncated": len(excerpt) < len(text)},
        citations=[cite.model_copy(update={"title": title, "retrieved_at": today()})],
        guidance="Quote or paraphrase only what this text says and cite the URL.",
    )
