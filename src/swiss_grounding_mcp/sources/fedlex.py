"""Federal law from Fedlex: find acts in the Systematic Collection (SR) and quote current articles."""

from __future__ import annotations

import asyncio
import csv
import io
import re
from datetime import date

from lxml import etree

from .. import http
from ..models import Citation, ToolResult, needs, source_error

SPARQL = "https://fedlex.data.admin.ch/sparqlendpoint"
LANGS = {"de": "DEU", "fr": "FRA", "it": "ITA", "rm": "ROH", "en": "ENG"}
PREFIX = """PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""
# Common abbreviations (de/fr/it) -> SR number
ALIASES = {
    "or": "220", "co": "220", "obligationenrecht": "220", "code des obligations": "220",
    "codice delle obbligazioni": "220", "zgb": "210", "cc": "210", "zivilgesetzbuch": "210",
    "code civil": "210", "codice civile": "210", "stgb": "311.0", "cp": "311.0", "strafgesetzbuch": "311.0",
    "bv": "101", "cst": "101", "bundesverfassung": "101", "constitution": "101", "costituzione": "101",
    "svg": "741.01", "lcr": "741.01", "strassenverkehrsgesetz": "741.01", "vzv": "741.51", "oac": "741.51",
    "ahvg": "831.10", "lavs": "831.10", "kvg": "832.10", "lamal": "832.10", "avig": "837.0", "laci": "837.0",
    "aig": "142.20", "lei": "142.20", "mwstg": "641.20", "ltva": "641.20", "dbg": "642.11", "lifd": "642.11",
    "arg": "822.11", "ltr": "822.11", "arbeitsgesetz": "822.11", "rtvg": "784.40", "lrtv": "784.40",
    "zg": "631.0", "ld": "631.0", "büg": "141.0", "bug": "141.0", "ln": "141.0", "dsg": "235.1",
    "lpd": "235.1", "vmwg": "221.213.11", "olcl": "221.213.11", "bvg": "831.40", "lpp": "831.40",
    "ivg": "831.20", "lai": "831.20",
}
STOP = {"der", "die", "das", "und", "des", "über", "le", "la", "les", "de", "du", "sur", "il", "del", "della",
        "sulla", "law", "the", "of", "gesetz", "loi", "legge", "bundesgesetz", "verordnung", "ordonnance"}
PUBLISHER = "Fedlex (Federal Chancellery)"


async def _sparql(query: str, ttl: float = http.DAY) -> list[dict]:
    body = await http.fetch(SPARQL, method="POST", data={"query": PREFIX + query},
                            headers={"Accept": "text/csv"}, ttl=ttl, check_robots=False)
    return list(csv.DictReader(io.StringIO(body)))


def _article_eid(article: str) -> str | None:
    m = re.search(r"(\d+)\s*([a-z]{1,9})?", re.sub(r"(?i)art\.?", "", article).lower())
    if not m:
        return None
    return f"art_{m.group(1)}" + (f"_{m.group(2)}" if m.group(2) else "")


def _clean(el) -> str:
    for n in el.xpath('.//*[local-name()="authorialNote"]'):
        n.getparent().remove(n)
    return " ".join(" ".join(el.itertext()).split())


async def _find_by_title(query: str, lang: str) -> list[dict]:
    words = [w for w in re.findall(r"[^\W\d_]{4,}", query.lower()) if w not in STOP][:4]
    if not words:
        return []
    filters = " ".join(f'FILTER(CONTAINS(LCASE(?title), "{w}"))' for w in words)
    return await _sparql(f"""
SELECT DISTINCT ?act ?sr ?title WHERE {{
 ?act a jolux:ConsolidationAbstract ; jolux:classifiedByTaxonomyEntry/skos:notation ?sr ; jolux:isRealizedBy ?e .
 ?e jolux:language <http://publications.europa.eu/resource/authority/language/{LANGS[lang]}> ; jolux:title ?title .
 {filters}
 FILTER NOT EXISTS {{ ?act jolux:dateNoLongerInForce ?end }}
}} LIMIT 8""")


async def _act_by_sr(sr: str, lang: str) -> dict | None:
    sr = re.sub(r"[^0-9.]", "", sr)
    rows = await _sparql(f"""
SELECT DISTINCT ?act ?title WHERE {{
 ?act a jolux:ConsolidationAbstract ;
      jolux:classifiedByTaxonomyEntry/skos:notation "{sr}"^^<https://fedlex.data.admin.ch/vocabulary/notation-type/id-systematique> ;
      jolux:isRealizedBy ?e .
 ?e jolux:language <http://publications.europa.eu/resource/authority/language/{LANGS[lang]}> ; jolux:title ?title .
 FILTER NOT EXISTS {{ ?act jolux:dateNoLongerInForce ?end }}
}} LIMIT 1""", ttl=7 * http.DAY)
    return rows[0] if rows else None


async def _current_xml(act: str, lang: str) -> tuple[str, str] | None:
    rows = await _sparql(f"""
SELECT ?date ?file WHERE {{
 ?cons a jolux:Consolidation ; jolux:isMemberOf <{act}> ; jolux:dateApplicability ?date ; jolux:isRealizedBy ?e .
 ?e jolux:language <http://publications.europa.eu/resource/authority/language/{LANGS[lang]}> ;
    jolux:isEmbodiedBy/jolux:isExemplifiedBy ?file .
 FILTER(?date <= "{date.today().isoformat()}"^^xsd:date) FILTER(CONTAINS(STR(?file), "/xml/"))
}} ORDER BY DESC(?date) LIMIT 1""")
    return (rows[0]["date"], rows[0]["file"]) if rows else None


def _public_url(act: str, lang: str, eid: str | None = None) -> str:
    url = act.replace("https://fedlex.data.admin.ch/", "https://www.fedlex.admin.ch/") + f"/{lang}"
    return url + (f"#{eid}" if eid else "")


def _article_label(text: str) -> str:
    m = re.match(r"(Art\.?\s*\d+)\s*([a-z]{1,9}\b)?", text)  # "Art. 335 c 1 Das…" -> "Art. 335c"
    return (m.group(1) + (m.group(2) or "")) if m else text[:12]


async def swiss_law(
    sr_number: str | None = None, query: str | None = None, article: str | None = None, language: str = "de"
) -> ToolResult:
    lang = language if language in LANGS else "de"
    sr = (sr_number or "").strip() or None
    if sr and sr.lower() in ALIASES:
        sr = ALIASES[sr.lower()]
    if not sr and query and query.strip().lower() in ALIASES:
        sr, query = ALIASES[query.strip().lower()], None
    try:
        if not sr:
            if not query:
                return needs("sr_number", "Which federal act (name, abbreviation or SR number) is meant?")
            acts = await _find_by_title(query, lang)
            if not acts:
                return ToolResult(
                    status="not_found",
                    summary=f"No federal act in force has a title matching '{query}'.",
                    guidance="If this is a topic rather than an act's title, call again with the act "
                    "(e.g. sr_number='OR') and query='<topic>', or use search_official_info. "
                    "Cantonal law is not in Fedlex.",
                )
            if len(acts) > 1 or not article:
                return ToolResult(
                    status="ok",
                    summary=f"{len(acts)} federal act(s) match '{query}'. Call again with sr_number (and "
                    "article, or a topic query) to read the text.",
                    data={"acts": [{"sr_number": a["sr"], "title": a["title"]} for a in acts]},
                    citations=[Citation(title=a["title"], url=_public_url(a["act"], lang), publisher=PUBLISHER,
                                        level="federal", jurisdiction="CH") for a in acts[:5]],
                )
            sr = acts[0]["sr"]
        act = await _act_by_sr(sr, lang)
        if not act:
            return ToolResult(status="not_found", summary=f"SR {sr} was not found in Fedlex.",
                              guidance="Check the SR number (e.g. 220 = Code of Obligations).")
        current = await _current_xml(act["act"], lang)
        if not current:
            return ToolResult(status="not_found", summary=f"No consolidated {lang} text found for SR {sr}.",
                              guidance="Try language='de'.")
        valid_from, xml_url = current
        xml = await http.fetch(xml_url, ttl=7 * http.DAY, check_robots=False)
    except http.FetchError as e:
        return source_error("Fedlex", e)

    root = await asyncio.to_thread(lambda: etree.fromstring(xml.encode()))
    title = act["title"]
    base = {"sr_number": sr, "title": title, "version_in_force_since": valid_from}

    def cite(eid: str | None, excerpt: str | None = None, label: str = "") -> Citation:
        return Citation(
            title=f"SR {sr} {title}{label}", url=_public_url(act["act"], lang, eid), publisher=PUBLISHER,
            level="federal", jurisdiction="CH", valid_for=f"consolidated version in force since {valid_from}",
            excerpt=excerpt,
        )

    if article:
        eid = _article_eid(article)
        nodes = root.xpath(f'//*[@eId="{eid}"]') if eid else []
        if not nodes:
            return ToolResult(status="not_found", summary=f"Article '{article}' does not exist in SR {sr}.",
                              data=base, citations=[cite(None)])
        text = _clean(nodes[0])
        return ToolResult(
            status="ok",
            summary=f"SR {sr} {_article_label(text)} (version in force since {valid_from}): {text[:200]}…",
            data={**base, "article": article, "text": text[:4000]},
            citations=[cite(eid, text[:700], f", {_article_label(text)}")],
            guidance="Quote the article text and cite the Fedlex URL. Federal law only; cantonal rules may add "
            "details (use search_official_info with the canton).",
        )
    if query:
        words = [w[: max(5, len(w) - 2)] for w in re.findall(r"[^\W\d_]{4,}", query.lower()) if w not in STOP]
        scored = []
        for art in root.xpath('//*[local-name()="article"]'):
            text = _clean(art)
            low = text.lower()
            # every query word present matters more than one word repeated; the exact phrase most
            score = sum(1 for w in words if w in low) * 10 + sum(low.count(w) for w in words)
            score += 50 if query.lower() in low else 0
            if score:
                scored.append((score, art.get("eId"), text))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return ToolResult(status="not_found", summary=f"No article of SR {sr} mentions '{query}'.",
                              data=base, citations=[cite(None)])
        top = scored[:3]
        return ToolResult(
            status="ok",
            summary=f"{len(scored)} article(s) of SR {sr} mention '{query}'; the {len(top)} most relevant are quoted.",
            data={**base, "articles": [{"article": _article_label(t), "text": t[:1500]} for _, _, t in top]},
            citations=[cite(eid, t[:700], f", {_article_label(t)}") for _, eid, t in top],
            guidance="Quote the relevant article(s) and cite the Fedlex URL with its article anchor.",
        )
    return ToolResult(status="ok", summary=f"SR {sr}: {title} (consolidated version in force since {valid_from}).",
                      data=base, citations=[cite(None)],
                      guidance="Call again with article='335c' or a topic query to read specific provisions.")
