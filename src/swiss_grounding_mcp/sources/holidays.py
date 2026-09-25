"""School holidays (canton/municipality) and public holidays via the OpenHolidays API.

OpenHolidays aggregates the official cantonal and municipal holiday lists; we label it as such and
always cite the official reference (EDK list, municipality website) next to it.
"""

from __future__ import annotations

from datetime import date

from .. import http
from ..models import Citation, ToolResult, needs, source_error
from ..places import canton_name, resolve

API = "https://openholidaysapi.org"
EDK = "https://www.edk.ch/de/bildungssystem/kantonale-schulorganisation/Schulferien/ferienlisten"
LANG = {"de": "DE", "fr": "FR", "it": "IT", "en": "EN", "rm": "DE"}  # OpenHolidays has no Romansh


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
    items, seen = [], set()
    for e in entries:
        subs = [s.get("code") or "" for s in e.get("subdivisions", [])]
        # keep only the subdivision codes related to the requested place (itself, ancestors, children)
        rel = [s for s in subs if code and (s == code or code.startswith(s + "-") or s.startswith(code + "-"))]
        key = (_name(e, lang), e["startDate"], e["endDate"])
        if key in seen:
            continue
        seen.add(key)
        applies = "nationwide" if e.get("nationwide") else ", ".join(rel[:3]) + (" …" if len(rel) > 3 else "")
        items.append({"name": key[0], "start": key[1], "end": key[2], "applies_to": applies or code})
    names = [i["name"] for i in items]
    varies = sorted({n for n in names if names.count(n) > 1})
    jurisdiction = muni.jurisdiction if muni else (f"CH-{canton}" if canton else "CH")
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
    if muni and muni.website:
        citations.append(Citation(title=f"Official website of {muni.name}", url=muni.website,
                                  publisher=f"Municipality of {muni.name}", level="municipal",
                                  jurisdiction=muni.jurisdiction))
    if not items:
        return ToolResult(status="not_found", summary=f"No {kind} holidays for {where} in {year} are published in the data.",
                          citations=citations,
                          guidance="Point the user to the official list (EDK / municipality) instead of guessing.")
    guidance = "Give the dates for the requested period and cite the sources."
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
