"""Search the prebuilt index of official pages, and read live pages from official domains."""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
import shutil
import sqlite3
import threading
import unicodedata
from functools import cache

import trafilatura

from .. import http, semantic
from ..authorities import classify
from ..config import DATA_DIR, settings
from ..models import Citation, ToolResult, source_error, today
from ..places import resolve

log = logging.getLogger("swiss_grounding_mcp")

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
    où là où quoi stadt ville città citta city town gemeinde commune comune cumün vschinauncha dorf village
    """.split()
)
MAX_EXCERPT = 700
TITLE_WEIGHT = 4.0  # per query term in the page title; chosen on scripts/search_eval.py (0-6 tried)

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


def _fts(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms)


def _fts_query(text: str) -> str:
    return _fts(_terms(text))


_SELECT = """
    SELECT passages.rowid, p.url, p.title, p.lang, p.publisher, p.level, p.jurisdiction, p.fetched_at,
           passages.heading, passages.body{score}
    FROM passages JOIN pages p ON p.id = passages.page_id
"""


def _hit(row, jurisdictions: list[str] | None, language: str | None, terms: list[str] | None,
         exact: list[str] | None, score: float = 0.0) -> dict:
    rowid, url, title, lang, publisher, level, jur, fetched, heading, body = row[:10]
    # prefer the user's language and, when a place is known, the most specific jurisdiction
    bonus = (1.5 if language and lang == language else 0) + (1.0 if jurisdictions and "-" in jur else 0)
    coverage = 1.0
    if terms:  # reward passages (with their page title/heading) that cover more distinct query terms
        hay = f"{title} {heading} {body}".lower()
        matched = [t for t in terms if t in hay]
        coverage = len(matched) / len(terms)
        bonus += 2.0 * len(matched)
        # the page title says what the page is about: a title that names the question's terms beats a
        # passage that merely mentions them ("Annonce de départ et d'arrivée" vs a customs notice)
        bonus += TITLE_WEIGHT * sum(1 for t in terms if t in title.lower())
        bonus += 1.0 * sum(1 for w in exact or [] if re.search(rf"\b{re.escape(w)}\b", hay))
    return {
        "rowid": rowid, "url": url, "title": title, "language": lang, "publisher": publisher, "level": level,
        "jurisdiction": jur, "retrieved_at": fetched, "heading": heading, "body": body,
        "rank": score - bonus, "coverage": coverage, "similarity": None,
    }


def _per_page(hits: list[dict], limit: int) -> list[dict]:
    """Keep the ranking, with at most two passages from the same page."""
    out, per_page = [], {}
    for h in hits:
        if per_page.get(h["url"], 0) >= 2:
            continue
        per_page[h["url"]] = per_page.get(h["url"], 0) + 1
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _run_search(fts: str, jurisdictions: list[str] | None, language: str | None, limit: int,
                terms: list[str] | None = None, exact: list[str] | None = None) -> list[dict]:
    sql = _SELECT.format(score=", bm25(passages, 8.0, 4.0, 1.0)") + " WHERE passages MATCH ?"
    params: list = [fts]
    if jurisdictions:
        sql += f" AND p.jurisdiction IN ({','.join('?' * len(jurisdictions))})"
        params += jurisdictions
    sql += " ORDER BY 11 LIMIT 200"
    with _connect() as db:
        rows = db.execute(sql, params).fetchall()
    hits = [_hit(r, jurisdictions, language, terms, exact, score=r[10]) for r in rows]
    hits.sort(key=lambda h: h["rank"])
    return _per_page(hits, limit)


def _hybrid(query: str, fts: str, jurisdictions: list[str] | None, language: str | None, limit: int,
            terms: list[str], exact: list[str], sem: semantic.Semantic) -> list[dict]:
    """Reciprocal-rank fusion of the keyword and vector rankings. Passages found only by meaning (e.g. a
    French page for an English question) join the results, carrying their similarity as evidence."""
    keyword = _run_search(fts, jurisdictions, language, 60, terms, exact) if fts else []
    vector = sem.search(query, jurisdictions, 60)
    similarity = dict(vector)
    by_id = {h["rowid"]: h for h in keyword}
    missing = [rid for rid, _ in vector if rid not in by_id]
    if missing:
        sql = _SELECT.format(score="") + f" WHERE passages.rowid IN ({','.join('?' * len(missing))})"
        with _connect() as db:
            by_id |= {r[0]: _hit(r, jurisdictions, language, terms, exact) for r in db.execute(sql, missing)}
    fused: dict[int, float] = {}
    for weight, ranking in ((1.0, [h["rowid"] for h in keyword]), (semantic.VECTOR_WEIGHT, [r for r, _ in vector])):
        for position, rid in enumerate(ranking):
            fused[rid] = fused.get(rid, 0.0) + weight / (61 + position)
    if jurisdictions:  # like the keyword ranking, prefer the canton's and municipality's own pages
        for rid in fused:
            if rid in by_id and "-" in by_id[rid]["jurisdiction"]:
                fused[rid] += semantic.LOCAL_BONUS
    # the best keyword hits keep their place: a passage ranked first by exact words must not be pushed
    # out by passages that are only moderately good in both rankings (a known weakness of rank fusion)
    pinned = [h["rowid"] for h in keyword[:semantic.KEYWORD_PINNED]]
    ordered = pinned + [rid for rid in sorted(fused, key=fused.get, reverse=True) if rid not in pinned]
    return _per_page([{**by_id[rid], "similarity": similarity.get(rid)} for rid in ordered if rid in by_id], limit)


LANGUAGE_NAMES = {"de": "German", "fr": "French", "it": "Italian", "rm": "Romansh", "en": "English"}
# a few function words per language, enough to tell the language of a short question
_FUNCTION_WORDS = {
    "de": set("der die das und ist wie wo wann ich mein meine bei nach mit für muss kann einen einer im zum zur "
              "wir sie".split()),
    "fr": set("le la les des du de et est comment où quand je mon ma mes pour avec dans sur dois faut à au aux "
              "une un".split()),
    "it": set("il lo gli della del di e è come dove quando io mio mia per con nel nella devo quale una".split()),
    "rm": set("il la ils las da e èn cura co tge sin cun per nus vus in ina".split()),
    "en": set("the a an of and is how when where what which my i for with to in on do can must".split()),
}


def query_language(query: str) -> str | None:
    """Best guess at the language of a short question, from its function words (None if unclear)."""
    tokens = re.findall(r"[^\W\d_]+", query.lower())
    scores = {lang: sum(t in words for t in tokens) for lang, words in _FUNCTION_WORDS.items()}
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    return best if ranked[0] > 0 and ranked[0] > ranked[1] else None


@cache
def _languages_by_jurisdiction() -> dict[str, dict[str, int]]:
    with _connect() as db:
        rows = db.execute("SELECT jurisdiction, lang, count(*) FROM pages GROUP BY jurisdiction, lang").fetchall()
    out: dict[str, dict[str, int]] = {}
    for jur, lang, n in rows:
        if lang in LANGUAGE_NAMES:
            out.setdefault(jur, {})[lang] = n
    return out


def place_languages(jurisdiction: str) -> list[str]:
    """Languages a canton or municipality publishes in, most used first: those with at least a fifth of
    its indexed pages (Bern: German; canton Bern: German and French). Empty if it has too few pages."""
    counts = _languages_by_jurisdiction().get(jurisdiction, {})
    total = sum(counts.values())
    if total < 5:
        return []
    return [lang for lang, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n / total >= 0.2]


def _language_hint(query: str, language: str | None, hits: list[dict], municipality, canton: str | None,
                   local_found: bool) -> tuple[str, list[str]] | None:
    """(place, its languages) when the question is in another language than the place publishes in and
    none of that place's pages matched: the caller should search again in the place's language."""
    asked = language or query_language(query)
    candidates = []
    if municipality is not None:
        own = any(h["jurisdiction"] == municipality.jurisdiction and _supported(h) for h in hits)
        if not own:
            candidates.append((municipality.name, place_languages(municipality.jurisdiction)))
    if canton and not local_found:
        candidates.append((f"canton {canton}", place_languages(f"CH-{canton}")))
    for where, langs in candidates:
        if langs and asked not in langs:
            return where, langs
    return None


def _hint_text(hint: tuple[str, list[str]], query: str) -> str:
    where, langs = hint
    names = " and ".join(LANGUAGE_NAMES[lang] for lang in langs)
    return (f"First, call search_official_info again with the key words of '{query}' translated into "
            f"{LANGUAGE_NAMES[langs[0]]}, for the same place: {where} publishes mainly in {names}, and none of "
            "its pages matched this question's language. ")


def _hint_summary(hint: tuple[str, list[str]]) -> str:
    where, langs = hint
    return f" {where} publishes in {' and '.join(LANGUAGE_NAMES[lang] for lang in langs)}: search again in that language."


def _supported(hit: dict) -> bool:
    """Evidence that a passage is about the question: half of its keywords, or close in meaning."""
    return hit["coverage"] >= 0.5 or (hit["similarity"] or 0.0) >= semantic.SUPPORT


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
    local_name, local_site = "", None  # the canton or municipality asked about, and its official website
    municipality, canton = None, None
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
            m = municipality = res.municipality
            canton = m.canton
            jurisdictions = ["CH", f"CH-{m.canton}", m.jurisdiction]
            place_words = [m.name.lower(), (place or "").lower()]
            scope_note = f"federal + canton {m.canton} + municipality {m.name}"
            local_name = f"canton {m.canton} or {m.name}"
            if m.website:
                local_site = Citation(title=f"Official website of {m.name}", url=m.website,
                                      publisher=f"Municipality of {m.name}", level="municipal",
                                      jurisdiction=m.jurisdiction)
        elif res.kind == "canton" and res.canton:
            canton = res.canton
            jurisdictions = ["CH", f"CH-{res.canton}"]
            place_words = [(place or "").lower()]
            scope_note = f"federal + canton {res.canton}"
            local_name = f"canton {res.canton}"
    words = [w for w in re.findall(r"[^\W\d_]{3,}", query.lower()) if w not in STOPWORDS]
    terms = [t for t in _terms(query) if not any(t in p for p in place_words)]  # place is already a filter
    # the database query uses the same terms: a place name in it would rank every page of that place
    # (Lausanne's childcare pages for "annoncer mon arrivée à Lausanne") above the pages on the topic
    fts = _fts(terms) or fts
    sem = await asyncio.to_thread(semantic.backend, False)  # None: keyword-only, or still loading at startup
    mode, hits = ("hybrid" if sem else "keyword"), None
    try:
        if sem:
            try:
                hits = await asyncio.to_thread(_hybrid, query, fts, jurisdictions, language, limit, terms, words, sem)
            except sqlite3.Error:
                raise
            except Exception as e:  # the optional vector part failed: keyword results are still valid
                log.warning("vector search failed, using keywords only: %s: %s", type(e).__name__, e)
                mode = "keyword"
        if hits is None:
            hits = await asyncio.to_thread(_run_search, fts, jurisdictions, language, limit, terms, words)
    except sqlite3.Error as e:  # the index itself is unreadable
        return source_error("The search index", e)
    if not hits:
        hint = _language_hint(query, language, [], municipality, canton, False)
        return ToolResult(
            status="not_found",
            summary=f"No passage in the index of official Swiss pages matches this query (scope: {scope_note})."
            + (_hint_summary(hint) if hint else ""),
            data={"fts_query": fts, "scope": scope_note, **({"place_languages": hint[1]} if hint else {})},
            guidance=(_hint_text(hint, query) if hint else "")
            + "Try different keywords or another national language (the index holds de/fr/it/rm/en "
            "pages). If still nothing, tell the user this is not covered rather than guessing.",
        )
    if len(terms) >= 2 and not any(_supported(h) for h in hits):
        weak = hits[:2]
        hint = _language_hint(query, language, [], municipality, canton, False)
        return ToolResult(
            status="not_found",
            summary=f"No official page in the index answers this (scope: {scope_note}); closest pages listed."
            + (_hint_summary(hint) if hint else ""),
            data={"scope": scope_note, "search": mode,
                  "closest": [{"title": h["title"], "url": h["url"]} for h in weak],
                  **({"place_languages": hint[1]} if hint else {})},
            citations=[Citation(title=h["title"], url=h["url"], publisher=h["publisher"], level=h["level"],
                                jurisdiction=h["jurisdiction"], retrieved_at=h["retrieved_at"]) for h in weak],
            guidance=(_hint_text(hint, query) if hint else "")
            + "Try at most one reformulation (other keywords or another national language). If that "
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
    # a local page only counts if it matches the question as well as the best hit does, or is close in
    # meaning (a 2018 dog-fair parking notice from Winterthur must not pass for its dog registration)
    best = max(h["coverage"] for h in hits)
    local_found = not local_name or any(
        "-" in h["jurisdiction"] and (h["coverage"] >= best or (h["similarity"] or 0.0) >= semantic.SUPPORT)
        for h in hits)
    if not local_found:
        # e.g. "register a dog in Winterthur" matching only federal customs pages: say so, don't imply
        guidance += (f" No page from {local_name} matched; these are federal pages only. If the question is "
                     f"about a cantonal or municipal procedure, say that no official page from {local_name} "
                     "was found and give the official website"
                     + (" cited last." if local_site else " (from swiss_place_info)."))
        if local_site:
            citations.append(local_site)
    # the place publishes in another language than the question: say which, so the assistant can
    # translate its key words and search once more (e.g. Bern's German pages for a French question)
    hint = _language_hint(query, language, hits, municipality, canton, local_found)
    if hint:
        guidance = _hint_text(hint, query) + guidance
    return ToolResult(
        status="ok",
        summary=f"{len(hits)} official passages found (scope: {scope_note})."
        + ("" if local_found else f" None is from {local_name}.") + (_hint_summary(hint) if hint else ""),
        data={"scope": scope_note, "index_built": index_meta().get("built"), "local_match": local_found,
              "search": mode, **({"place_languages": hint[1]} if hint else {})},
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
        # the allowlist is re-applied to every redirect, so an official URL cannot lead elsewhere
        html = await http.fetch(url, ttl=6 * http.HOUR, allow=lambda u: classify(u) is not None)
    except http.RobotsDisallowed as e:
        return ToolResult(
            status="not_covered",
            summary=f"The site's robots.txt does not allow automated access to this page ({e}).",
            citations=[cite], guidance="Give the user the link to read it themselves.",
        )
    except http.TermsDisallowed as e:
        return ToolResult(
            status="not_covered",
            summary=f"This page is not read automatically because {e}.",
            citations=[cite], guidance="Give the user the link to read it themselves.",
        )
    except http.BlockedURL as e:
        return ToolResult(
            status="not_covered",
            summary=f"This page redirects away from official Swiss sources, so it is not read ({e}).",
            citations=[cite], guidance="Use search_official_info to find the official page instead.",
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
