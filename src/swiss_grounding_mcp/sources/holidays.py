"""School holidays (canton/municipality) and public holidays via the OpenHolidays API.

OpenHolidays aggregates the official cantonal and municipal holiday lists; we label it as such and
always cite the official reference (EDK list, municipality website) next to it.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date

from .. import http
from ..models import Citation, ToolResult, needs, source_error
from ..places import canton_name, resolve
from . import search

API = "https://openholidaysapi.org"
EDK = "https://www.edk.ch/de/bildungssystem/kantonale-schulorganisation/Schulferien/ferienlisten"
LANG = {"de": "DE", "fr": "FR", "it": "IT", "en": "EN", "rm": "DE"}  # OpenHolidays has no Romansh


async def _group_names(lang: str) -> dict[str, str]:
    """School types by code (e.g. CH-BE-VS 'Volksschulen', CH-BE-EO 'Obligatorische Schulen', the
    French-speaking schools of the Bernese Jura): periods can differ by school type within a canton."""
    try:
        groups = await http.fetch_json(f"{API}/Groups", params={"countryIsoCode": "CH", "languageIsoCode": lang},
                                       ttl=30 * http.DAY, check_robots=False)
    except http.FetchError:
        return {}
    names = {g["code"]: _name(g, lang) for g in groups if g.get("code")}
    # canton Bern: OpenHolidays calls both groups "compulsory schools" in German; say which is which
    if "CH-BE-EO" in names:
        names["CH-BE-EO"] = "École obligatoire (French-speaking schools, Bernese Jura)"
    if "CH-BE-VS" in names:
        names["CH-BE-VS"] = "Volksschulen (German-speaking schools)"
    return names


def _official_page(jurisdictions: list[str], year: int) -> Citation | None:
    """The responsible authority's own holiday calendar page, if the index holds one (canton or municipality)."""
    fts = ('"schulferi"* OR "ferienplan"* OR "ferienliste"* OR "vacances scolaires" OR "vacanze scolastiche" '
           'OR "vacanzas"* OR "plan da scoula"')
    try:
        hits = search._run_search(fts, jurisdictions, None, 20)
    except Exception:  # the index is optional evidence here; the holiday data still answers
        return None
    topical = re.compile(r"ferien|vacanc|vacanz|scoula", re.I)
    hits = [h for h in hits if topical.search(h["url"] + " " + h["title"])]
    if not hits:
        return None
    # the page for the requested school year first, then the most specific jurisdiction
    best = max(hits, key=lambda h: (str(year) in h["url"] + h["title"], h["jurisdiction"].count("-")))
    return Citation(title=best["title"], url=best["url"], publisher=best["publisher"], level=best["level"],
                    jurisdiction=best["jurisdiction"], retrieved_at=best["retrieved_at"],
                    excerpt=best["body"][:500], valid_for=str(year))


MONTHS = {  # month names as official calendars write them
    1: ("januar", "janvier", "gennaio", "schaner"), 2: ("februar", "février", "febbraio", "favrer"),
    3: ("märz", "mars", "marzo", "mars"), 4: ("april", "avril", "aprile", "avrigl"), 5: ("mai", "mai", "maggio", "matg"),
    6: ("juni", "juin", "giugno", "zercladur"), 7: ("juli", "juillet", "luglio", "fanadur"),
    8: ("august", "août", "agosto", "avust"), 9: ("september", "septembre", "settembre", "settember"),
    10: ("oktober", "octobre", "ottobre", "october"), 11: ("november", "novembre", "novembre", "november"),
    12: ("dezember", "décembre", "dicembre", "december"),
}


def _date_pattern(iso: str) -> re.Pattern:
    """A date as calendars print it: 19.09.2026, 19.9., 19. September, 19 octobre, 10 d'october."""
    y, m, d = (int(x) for x in iso.split("-"))
    names = "|".join(re.escape(n) for n in MONTHS[m])
    return re.compile(rf"\b0?{d}\.\s?0?{m}\.|\b0?{d}\.?\s+(?:d')?(?:{names})\b", re.I)


def _official_text(url: str) -> str:
    with search._connect() as db:
        rows = db.execute("SELECT passages.body FROM passages JOIN pages p ON p.id = passages.page_id "
                          "WHERE p.url = ?", (url,)).fetchall()
    return "\n".join(r[0] for r in rows)


def _name(entry: dict, lang: str) -> str:
    names = {n["language"]: n["text"] for n in entry.get("name", [])}
    return names.get(lang) or names.get("DE") or next(iter(names.values()), "")


async def holidays(
    place: str | None, year: int | None = None, kind: str = "school", language: str = "de"
) -> ToolResult:
    year = year or date.today().year
    lang = LANG.get(language, "DE")
    res = await resolve(place)
    if res.kind == "foreign":
        return ToolResult(status="not_covered",
                          summary=f"This place is in {res.country}, not in Switzerland; this server only covers Swiss holidays.",
                          guidance="Say so clearly; do not answer from memory.")
    muni = res.municipality if res.kind == "municipality" else None
    canton = res.canton
    if kind == "school" and not canton:
        if res.kind == "ambiguous":
            options = [m.label for m in res.candidates[:10]]
            return needs("municipality", f"Several municipalities match: {', '.join(options)}. Which one?", options)
        return needs("municipality", "School holidays differ by canton and often by municipality. "
                     "Which municipality (or canton) is meant?")

    code = (muni.holiday_code if muni and muni.holiday_code else None) or (f"CH-{canton}" if canton else None)
    endpoint = "SchoolHolidays" if kind == "school" else "PublicHolidays"
    params = {"countryIsoCode": "CH", "languageIsoCode": lang,
              "validFrom": f"{year}-01-01", "validTo": f"{year}-12-31"}
    if code:
        params["subdivisionCode"] = code
    try:
        entries = await http.fetch_json(f"{API}/{endpoint}", params=params, ttl=7 * http.DAY, check_robots=False)
    except http.FetchError as e:
        return source_error("OpenHolidays", e)

    where = muni.name if muni else (canton_name(canton) if canton else "Switzerland")
    groups = await _group_names(lang) if kind == "school" else {}
    items, seen = [], set()
    for e in entries:
        subs = [s.get("code") or "" for s in e.get("subdivisions", [])]
        # keep only the subdivision codes related to the requested place (itself, ancestors, children)
        rel = [s for s in subs if code and (s == code or code.startswith(s + "-") or s.startswith(code + "-"))]
        school_type = ", ".join(groups.get(g.get("code", ""), g.get("code", "")) for g in e.get("groups") or [])
        key = (_name(e, lang), e["startDate"], e["endDate"], school_type)
        if key in seen:
            continue
        seen.add(key)
        applies = "nationwide" if e.get("nationwide") else ", ".join(rel[:3]) + (" …" if len(rel) > 3 else "")
        item = {"name": key[0], "start": key[1], "end": key[2], "applies_to": applies or code}
        if school_type:
            item["school_type"] = school_type
        items.append(item)
    names = [i["name"] for i in items]
    varies = sorted({n for n in names if names.count(n) > 1})
    jurisdiction = muni.jurisdiction if muni else (f"CH-{canton}" if canton else "CH")
    local = [j for j in (muni.jurisdiction if muni else None, f"CH-{canton}" if canton else None) if j]
    official = await asyncio.to_thread(_official_page, local, year) if kind == "school" and local else None
    citations = [
        Citation(title=f"OpenHolidays — {'school' if kind == 'school' else 'public'} holidays {year} ({code or 'CH'})",
                 url=f"{API}/{endpoint}?countryIsoCode=CH&subdivisionCode={code or ''}&validFrom={year}-01-01"
                 f"&validTo={year}-12-31&languageIsoCode={lang}",
                 publisher="OpenHolidays (open data aggregated from official cantonal lists)",
                 level="community", jurisdiction=jurisdiction, valid_for=str(year)),
    ]
    if kind == "school":
        citations.append(Citation(title="EDK — official school holiday lists of all cantons", url=EDK,
                                  publisher="EDK (Swiss Conference of Cantonal Ministers of Education)",
                                  level="semi-official", jurisdiction="CH", valid_for=str(year)))
    if official:  # the responsible authority's own calendar leads; the aggregated data supports it
        citations.insert(0, official)
        # mark the periods whose dates the official page itself shows, and put them first
        text = await asyncio.to_thread(_official_text, official.url)
        for item in items:
            if _date_pattern(item["start"]).search(text) and _date_pattern(item["end"]).search(text):
                item["on_official_page"] = True
        items.sort(key=lambda i: not i.get("on_official_page"))
    if muni and muni.website:
        citations.append(Citation(title=f"Official website of {muni.name}", url=muni.website,
                                  publisher=f"Municipality of {muni.name}", level="municipal",
                                  jurisdiction=muni.jurisdiction))
    if not items:
        return ToolResult(status="not_found", summary=f"No {kind} holidays for {where} in {year} are published in the data.",
                          citations=citations,
                          guidance="Point the user to the official list (EDK / municipality) instead of guessing.")
    guidance = "Give the dates for the requested period and cite the sources."
    confirmed = [i for i in items if i.get("on_official_page")]
    if official:
        guidance += (f" The first citation is the responsible authority's own calendar ({official.publisher}); "
                     "cite it, and check its excerpt or read_official_page(url) if the dates must be confirmed.")
    if confirmed and len(confirmed) < len(items):
        guidance += (" Periods marked on_official_page appear on that official page: where periods of the same "
                     "name differ, give those dates for this place.")
    types = {i.get("school_type") for i in items if i.get("school_type")}
    if len(types) > 1:
        guidance += (f" Dates differ by school type ({'; '.join(sorted(types))}), e.g. German- and French-speaking "
                     "schools of the canton: give the dates for the type that applies to the user's municipality "
                     "(its language region), or both, labelled.")
    if muni and not muni.holiday_code:
        guidance += (f" These are canton-level dates for {canton}; the school of {muni.name} may deviate — "
                     "recommend checking the municipality's school website.")
    if varies and kind == "school":
        guidance += (f" Dates for {', '.join(varies)} differ between sub-regions (see applies_to)"
                     + ("; if the user did not name the municipality, ask for it." if not muni else "."))
    level = "municipality" if muni and muni.holiday_code else "canton"
    return ToolResult(
        status="ok",
        summary=f"{len(items)} {kind} holiday period(s) for {where} in {year} ({level}-level data).",
        data={"place": where, "year": year, "subdivision_code": code, "holidays": items},
        citations=citations,
        guidance=guidance,
    )
