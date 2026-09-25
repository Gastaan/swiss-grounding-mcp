"""Waste collection dates from municipal open data.

Precise (address-level) coverage:
  - City of Zürich, by postcode (official ERZ data, served through OpenERZ)
  - Basel, Riehen, Bettingen (canton Basel-Stadt), by address -> collection zone (data.bs.ch)
  - City of St. Gallen, by street (daten.stadt.sg.ch)
Zone-level coverage (dates per collection zone): the other OpenERZ municipalities.
Everything else: not covered — the tool returns the municipality's official website instead.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from .. import http
from ..models import Citation, ToolResult, needs, source_error
from ..places import Municipality, norm, resolve
from .common import place_problem

TYPES = {
    "cardboard": ["karton", "carton", "cartone", "cartun", "cardboard"],
    "paper": ["papier", "carta", "palpiri", "paper"],
    "waste": ["kehricht", "abfall", "ordures", "dechets", "déchets", "rifiuti", "waste", "garbage", "rument",
              "müll", "muell"],
    "organic": ["grün", "gruen", "bio", "compost", "verts", "vegetali", "organic", "green"],
    "metal": ["metall", "métal", "metallo", "metal"],
    "bulky_goods": ["sperrgut", "encombrants", "ingombranti", "bulky"],
    "textile": ["textil", "tessili"],
    "special": ["sonderabfall", "spéciaux", "speciali", "special"],
}
# OpenERZ municipality slug -> (official name, canton); matched against the BFS register
OPENERZ = {
    "zurich": ("Zürich", "ZH"), "winterthur": ("Winterthur", "ZH"), "adliswil": ("Adliswil", "ZH"),
    "bassersdorf": ("Bassersdorf", "ZH"), "duebendorf": ("Dübendorf", "ZH"), "embrach": ("Embrach", "ZH"),
    "gossau-zh": ("Gossau (ZH)", "ZH"), "hombrechtikon": ("Hombrechtikon", "ZH"), "horgen": ("Horgen", "ZH"),
    "kilchberg": ("Kilchberg (ZH)", "ZH"), "langnau": ("Langnau am Albis", "ZH"),
    "oberrieden": ("Oberrieden", "ZH"), "richterswil": ("Richterswil", "ZH"),
    "rueschlikon": ("Rüschlikon", "ZH"), "thalwil": ("Thalwil", "ZH"), "uster": ("Uster", "ZH"),
    "waedenswil": ("Wädenswil", "ZH"), "wangen-bruttisellen": ("Wangen-Brüttisellen", "ZH"),
    "wetzikon": ("Wetzikon (ZH)", "ZH"), "wiesendangen": ("Wiesendangen", "ZH"), "seon": ("Seon", "AG"),
}
BASEL_STADT = {"Basel", "Riehen", "Bettingen"}
BS_TYPES = {"waste": "Kehrichtabfuhr", "paper": "Papierabfuhr", "cardboard": "Papierabfuhr",
            "organic": "Grünabfuhr", "metal": "Metallabfuhr", "bulky_goods": "Grobsperrgut"}
SG_TYPES = {"waste": "Kehricht", "paper": "Papier", "cardboard": "Karton", "organic": "Grüngut",
            "metal": "Metall"}


def waste_type_of(text: str | None) -> str | None:
    if not text:
        return None
    t = unicodedata.normalize("NFC", text.lower().strip())
    for key, words in TYPES.items():
        if t == key or any(w in t for w in words):
            return key
    return None


def _official(m: Municipality) -> list[Citation]:
    return [Citation(title=f"Official website of {m.name}", url=m.website, publisher=f"Municipality of {m.name}",
                     level="municipal", jurisdiction=m.jurisdiction)] if m.website else []


def _street(text: str | None) -> str | None:
    if not text:
        return None
    m = re.match(r"\s*([^\d,]+?)\s*(\d+\w?)?\s*(,|$)", text)
    return m.group(1).strip() if m else None


def _weekday(d: str) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][date.fromisoformat(d).weekday()]


async def waste_collection(
    place: str | None, waste_type: str | None = None, street: str | None = None, from_date: str | None = None
) -> ToolResult:
    kind = waste_type_of(waste_type) if waste_type else None
    if waste_type and not kind:
        return needs("waste_type", "Which kind of waste?", options=list(TYPES))
    start = date.fromisoformat(from_date) if from_date else date.today()
    end = start + timedelta(days=120)
    address = ", ".join(x for x in (street, place) if x) or None
    res = await resolve(address)
    if res.kind != "municipality" and street and place:
        res = await resolve(place)
    if problem := place_problem(res, "the local waste collection calendar"):
        return problem
    m = res.municipality
    try:
        if m.name == "Zürich":
            return await _zurich(m, kind, res.postcode, start, end)
        if m.canton == "BS" and m.name in BASEL_STADT:
            return await _basel(m, kind, address, start, end)
        if m.name == "St. Gallen":
            return await _stgallen(m, kind, _street(street) or (_street(place) if place and "," in place else None),
                                   start, end)
        slug = next((s for s, (name, canton) in OPENERZ.items() if name == m.name and canton == m.canton), None)
        if slug:
            return await _openerz_zones(m, slug, kind, start, end)
    except http.FetchError as e:
        return source_error("The waste calendar", e, _official(m))
    return ToolResult(
        status="not_covered",
        summary=f"Waste collection dates for {m.name} ({m.canton}) are not available as open data in this server.",
        citations=_official(m),
        guidance="Tell the user this municipality's calendar is not covered and give the official website. "
        "You may call read_official_page on the municipality website if a calendar page is linked there. "
        "Do not guess dates.",
        data={"covered_municipalities": "Zürich (by postcode), Basel/Riehen/Bettingen (by address), "
              "St. Gallen (by street), and by zone: " + ", ".join(n for n, _ in OPENERZ.values())},
    )


def _result(m: Municipality, kind: str | None, where: str, items: list[dict], cites: list[Citation],
            note: str | None = None) -> ToolResult:
    if not items:
        return ToolResult(status="not_found", citations=cites + _official(m),
                          summary=f"No {kind or 'waste'} collection found for {where} in the next 120 days.",
                          guidance="Say that no date is published for this period and give the official link.")
    first = items[0]
    summary = (f"Next {first['type']} collection in {where}: {first['date']} ({first['weekday']})."
               if kind else f"{len(items)} upcoming collections in {where}; next: {first['type']} on {first['date']}.")
    return ToolResult(status="ok", summary=summary, data={"place": where, "collections": items[:12]},
                      citations=cites + _official(m), guidance=note or "Give the next date(s) and cite the source.")


async def _zurich(m, kind, postcode, start, end) -> ToolResult:
    if not postcode:
        return needs("postcode", "Collection days in the city of Zürich depend on the postcode. "
                     "What is your postcode (e.g. 8003)?")
    params = {"region": "zurich", "zip": postcode, "start": start.isoformat(), "end": end.isoformat(), "limit": 50}
    if kind:
        params["types"] = kind
    data = await http.fetch_json("https://openerz.metaodi.ch/api/calendar.json", params=params,
                                 ttl=12 * http.HOUR, check_robots=False)
    items = [{"date": r["date"], "weekday": _weekday(r["date"]), "type": r["waste_type"]} for r in data["result"]]
    cites = [Citation(title="Entsorgungskalender Stadt Zürich (Entsorgung + Recycling Zürich, open data)",
                      url="https://data.stadt-zuerich.ch/dataset?q=entsorgungskalender", publisher="Stadt Zürich",
                      level="municipal", jurisdiction=m.jurisdiction, valid_for=str(start.year),
                      retrieved_at=date.today().isoformat())]
    return _result(m, kind, f"Zürich {postcode}", items, cites)


async def _basel(m, kind, address, start, end) -> ToolResult:
    if not address or not re.search(r"\d", address):
        return needs("street", f"Collection days in {m.name} depend on the collection zone of the address. "
                     "What is the street and house number?")
    geo = await http.fetch_json("https://api3.geo.admin.ch/rest/services/api/SearchServer",
                                params={"searchText": address, "type": "locations", "origins": "address",
                                        "limit": 1, "sr": 4326}, check_robots=False)
    if not geo.get("results"):
        return needs("street", "I could not locate this address. Which street and house number?")
    a = geo["results"][0]["attrs"]
    zones = await http.fetch_json(
        "https://data.bs.ch/api/explore/v2.1/catalog/datasets/100095/records",
        params={"where": f"intersects(geo_shape, geom'POINT({a['lon']} {a['lat']})')", "select": "zone", "limit": 1},
        ttl=30 * http.DAY, check_robots=False)
    if not zones.get("results"):
        return ToolResult(status="not_found", summary="This address is outside the Basel-Stadt collection zones.",
                          citations=_official(m))
    zone = zones["results"][0]["zone"]
    where = f"zone='{zone}' and termin>='{start.isoformat()}' and termin<='{end.isoformat()}'"
    if kind:
        if kind not in BS_TYPES:
            return ToolResult(status="not_covered", citations=_official(m),
                              summary=f"Basel-Stadt publishes no collection calendar for '{kind}'.")
        where += f" and art='{BS_TYPES[kind]}'"
    data = await http.fetch_json("https://data.bs.ch/api/explore/v2.1/catalog/datasets/100096/records",
                                 params={"where": where, "order_by": "termin", "limit": 30,
                                         "select": "termin,art,zone"}, ttl=12 * http.HOUR, check_robots=False)
    items = [{"date": r["termin"], "weekday": _weekday(r["termin"]), "type": r["art"], "zone": zone}
             for r in data["results"]]
    cites = [Citation(title="Abfuhrtermine Kanton Basel-Stadt (dataset 100096)",
                      url="https://data.bs.ch/explore/dataset/100096/", publisher="Kanton Basel-Stadt",
                      level="cantonal", jurisdiction=m.jurisdiction, retrieved_at=date.today().isoformat())]
    note = ("In Basel-Stadt cardboard is collected together with paper (Papierabfuhr)."
            if kind == "cardboard" else None)
    label = re.sub(r"<[^>]+>", "", a.get("label", address))
    return _result(m, kind, f"{label} (zone {zone})", items, cites, note)


async def _stgallen(m, kind, street, start, end) -> ToolResult:
    if not street:
        return needs("street", "Collection days in St. Gallen depend on the street. Which street?")
    street_q = street.replace("'", "")
    where = f"strasse='{street_q}' and datum>='{start.isoformat()}' and datum<='{end.isoformat()}'"
    if kind:
        if kind not in SG_TYPES:
            return ToolResult(status="not_covered", citations=_official(m),
                              summary=f"St. Gallen publishes no street calendar for '{kind}'.")
        where += f" and sammlung like '{SG_TYPES[kind]}*'"
    url = "https://daten.stadt.sg.ch/api/explore/v2.1/catalog/datasets/abfuhrdaten-stadt-stgallen/records"
    data = await http.fetch_json(url, params={"where": where, "order_by": "datum", "limit": 30,
                                              "select": "datum,sammlung,strasse"},
                                 ttl=12 * http.HOUR, check_robots=False)
    if not data.get("results"):
        streets = await http.fetch_json(url, params={"where": f"search(strasse, '{street_q[:20]}')",
                                                     "group_by": "strasse", "select": "strasse", "limit": 8},
                                        ttl=http.DAY, check_robots=False)
        options = [r["strasse"] for r in streets.get("results", [])]
        if options and norm(street) not in [norm(o) for o in options]:
            return needs("street", f"Which street exactly? Candidates: {', '.join(options)}", options)
    items = [{"date": r["datum"], "weekday": _weekday(r["datum"]), "type": r["sammlung"]} for r in data["results"]]
    cites = [Citation(title="Abfuhrdaten Stadt St.Gallen (open data)",
                      url="https://daten.stadt.sg.ch/explore/dataset/abfuhrdaten-stadt-stgallen/",
                      publisher="Stadt St.Gallen", level="municipal", jurisdiction=m.jurisdiction,
                      retrieved_at=date.today().isoformat())]
    return _result(m, kind, f"{street}, St. Gallen", items, cites)


async def _openerz_zones(m, slug, kind, start, end) -> ToolResult:
    params = {"region": slug, "start": start.isoformat(), "end": end.isoformat(), "limit": 200}
    if kind:
        params["types"] = kind
    data = await http.fetch_json("https://openerz.metaodi.ch/api/calendar.json", params=params,
                                 ttl=12 * http.HOUR, check_robots=False)
    rows = data["result"]
    areas = sorted({r["area"] for r in rows if r.get("area")})
    cites = [Citation(title=f"OpenERZ waste calendar ({m.name}, from the municipality's official calendar)",
                      url=f"https://openerz.metaodi.ch/api/calendar.json?region={slug}",
                      publisher="OpenERZ (open data from the municipal calendar)", level="community",
                      jurisdiction=m.jurisdiction, retrieved_at=date.today().isoformat())]
    if len(areas) <= 1:
        items = [{"date": r["date"], "weekday": _weekday(r["date"]), "type": r["waste_type"]} for r in rows]
        return _result(m, kind, m.name, items, cites)
    nxt: dict[str, dict] = {}
    for r in rows:
        nxt.setdefault(r["area"], {"date": r["date"], "weekday": _weekday(r["date"]), "type": r["waste_type"],
                                   "zone": r["area"]})
    return _result(m, kind, m.name, [nxt[a] for a in areas], cites,
                   note=f"{m.name} is split into collection zones ({', '.join(areas)}); the next date per zone is "
                   "listed. If the user's zone is unknown, give all zones or ask for it, and link the official "
                   "calendar.")
