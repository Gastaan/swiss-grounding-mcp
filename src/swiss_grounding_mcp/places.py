"""Resolve free-text places (municipality, postcode, locality, address, canton) to official codes.

Rules: exact/normalized matches only (fuzzy matching picks wrong municipalities), ambiguous names
return candidates, well-known foreign places are reported as not Swiss.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cache

from . import http
from .config import DATA_DIR

CANTONS: dict[str, dict[str, str]] = {
    "ZH": {"de": "Zürich", "fr": "Zurich", "it": "Zurigo", "rm": "Turitg", "en": "Zurich"},
    "BE": {"de": "Bern", "fr": "Berne", "it": "Berna", "rm": "Berna", "en": "Bern"},
    "LU": {"de": "Luzern", "fr": "Lucerne", "it": "Lucerna", "rm": "Lucerna", "en": "Lucerne"},
    "UR": {"de": "Uri", "fr": "Uri", "it": "Uri", "rm": "Uri", "en": "Uri"},
    "SZ": {"de": "Schwyz", "fr": "Schwytz", "it": "Svitto", "rm": "Sviz", "en": "Schwyz"},
    "OW": {"de": "Obwalden", "fr": "Obwald", "it": "Obvaldo", "rm": "Sursilvania", "en": "Obwalden"},
    "NW": {"de": "Nidwalden", "fr": "Nidwald", "it": "Nidvaldo", "rm": "Sutsilvania", "en": "Nidwalden"},
    "GL": {"de": "Glarus", "fr": "Glaris", "it": "Glarona", "rm": "Glaruna", "en": "Glarus"},
    "ZG": {"de": "Zug", "fr": "Zoug", "it": "Zugo", "rm": "Zug", "en": "Zug"},
    "FR": {"de": "Freiburg", "fr": "Fribourg", "it": "Friburgo", "rm": "Friburg", "en": "Fribourg"},
    "SO": {"de": "Solothurn", "fr": "Soleure", "it": "Soletta", "rm": "Soloturn", "en": "Solothurn"},
    "BS": {"de": "Basel-Stadt", "fr": "Bâle-Ville", "it": "Basilea Città", "rm": "Basilea-Citad", "en": "Basel-City"},
    "BL": {"de": "Basel-Landschaft", "fr": "Bâle-Campagne", "it": "Basilea Campagna", "rm": "Basilea-Champagna", "en": "Basel-Country"},
    "SH": {"de": "Schaffhausen", "fr": "Schaffhouse", "it": "Sciaffusa", "rm": "Schaffusa", "en": "Schaffhausen"},
    "AR": {"de": "Appenzell Ausserrhoden", "fr": "Appenzell Rhodes-Extérieures", "it": "Appenzello Esterno", "rm": "Appenzell Dadora", "en": "Appenzell Outer Rhodes"},
    "AI": {"de": "Appenzell Innerrhoden", "fr": "Appenzell Rhodes-Intérieures", "it": "Appenzello Interno", "rm": "Appenzell Dadens", "en": "Appenzell Inner Rhodes"},
    "SG": {"de": "St. Gallen", "fr": "Saint-Gall", "it": "San Gallo", "rm": "Son Gagl", "en": "St. Gallen"},
    "GR": {"de": "Graubünden", "fr": "Grisons", "it": "Grigioni", "rm": "Grischun", "en": "Grisons"},
    "AG": {"de": "Aargau", "fr": "Argovie", "it": "Argovia", "rm": "Argovia", "en": "Aargau"},
    "TG": {"de": "Thurgau", "fr": "Thurgovie", "it": "Turgovia", "rm": "Turgovia", "en": "Thurgau"},
    "TI": {"de": "Tessin", "fr": "Tessin", "it": "Ticino", "rm": "Tessin", "en": "Ticino"},
    "VD": {"de": "Waadt", "fr": "Vaud", "it": "Vaud", "rm": "Vad", "en": "Vaud"},
    "VS": {"de": "Wallis", "fr": "Valais", "it": "Vallese", "rm": "Vallais", "en": "Valais"},
    "NE": {"de": "Neuenburg", "fr": "Neuchâtel", "it": "Neuchâtel", "rm": "Neuchâtel", "en": "Neuchâtel"},
    "GE": {"de": "Genf", "fr": "Genève", "it": "Ginevra", "rm": "Genevra", "en": "Geneva"},
    "JU": {"de": "Jura", "fr": "Jura", "it": "Giura", "rm": "Jura", "en": "Jura"},
}

# Exonyms and historic names of municipalities -> official BFS name.
ALIASES = {
    "genf": "Genève", "geneva": "Genève", "ginevra": "Genève", "genevra": "Genève", "geneve": "Genève",
    "zurigo": "Zürich", "turitg": "Zürich", "zurich": "Zürich",
    "berne": "Bern", "berna": "Bern",
    "lucerne": "Luzern", "lucerna": "Luzern",
    "bale": "Basel", "basilea": "Basel", "basle": "Basel",
    "coira": "Chur", "cuira": "Chur", "coire": "Chur",
    "schuls": "Scuol", "sitten": "Sion", "neuenburg": "Neuchâtel", "freiburg": "Fribourg",
    "friburgo": "Fribourg", "pruntrut": "Porrentruy", "delsberg": "Delémont", "siders": "Sierre",
    "martinach": "Martigny", "losanna": "Lausanne", "lausanna": "Lausanne", "soleure": "Solothurn",
    "soletta": "Solothurn", "schaffhouse": "Schaffhausen", "sciaffusa": "Schaffhausen",
    "saint gall": "St. Gallen", "san gallo": "St. Gallen", "st gallen": "St. Gallen", "sankt gallen": "St. Gallen",
    "bienne": "Biel/Bienne", "biel": "Biel/Bienne", "thoune": "Thun", "morat": "Murten",
    "bellinzone": "Bellinzona", "lauis": "Lugano",
    "saint moritz": "St. Moritz", "san murezzan": "St. Moritz", "st moritz": "St. Moritz",
    "winterthour": "Winterthur", "zoug": "Zug", "glaris": "Glarus", "schwytz": "Schwyz",
}

# Well-known places just outside Switzerland that users mistake for Swiss ones.
FOREIGN = {
    "konstanz": "Germany", "constance": "Germany", "costanza": "Germany", "lorrach": "Germany",
    "loerrach": "Germany", "weil am rhein": "Germany", "singen": "Germany", "waldshut": "Germany",
    "friedrichshafen": "Germany", "freiburg im breisgau": "Germany", "munchen": "Germany",
    "muenchen": "Germany", "munich": "Germany", "stuttgart": "Germany", "berlin": "Germany",
    "lindau": "Germany", "jestetten": "Germany", "busingen": "Germany (German exclave)",
    "buesingen": "Germany (German exclave)", "busingen am hochrhein": "Germany (German exclave)",
    "bregenz": "Austria", "feldkirch": "Austria", "dornbirn": "Austria", "innsbruck": "Austria",
    "wien": "Austria", "vienna": "Austria", "vaduz": "Liechtenstein", "schaan": "Liechtenstein",
    "liechtenstein": "Liechtenstein", "annemasse": "France", "saint louis": "France",
    "mulhouse": "France", "evian": "France", "evian les bains": "France",
    "thonon": "France", "thonon les bains": "France", "ferney voltaire": "France", "divonne": "France",
    "pontarlier": "France", "chamonix": "France", "annecy": "France", "lyon": "France", "paris": "France",
    "strasbourg": "France", "como": "Italy", "varese": "Italy", "domodossola": "Italy",
    "campione d italia": "Italy (Italian exclave)", "livigno": "Italy", "milano": "Italy",
    "milan": "Italy", "aosta": "Italy", "tirano": "Italy",
}

_STRIP = re.compile(
    r"^(gemeinde|stadt|kanton|canton|commune( de)?|ville de|comune( di)?|citta di|cumun( da)?"
    r"|vschinauncha( da)?|chantun|cantone( di| del)?)\s+"
)
_KANTON_WORD = re.compile(r"(?i)^(kanton|canton|cantone|chantun)\b")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("-", " ").replace("/", " ").replace(".", " ").replace("'", " ")
    s = " ".join(s.split())
    return _STRIP.sub("", s)


@dataclass
class Municipality:
    bfs: int
    name: str
    canton: str
    district: str | None
    premium_region: int | None
    postcodes: list[int]
    localities: list[str]
    website: str | None
    population: int | None
    holiday_code: str | None

    @property
    def label(self) -> str:
        """'Lugano (TI)'; BFS names of homonyms already carry the canton: 'Buchs (SG)'."""
        return self.name if self.name.endswith(")") else f"{self.name} ({self.canton})"

    @property
    def jurisdiction(self) -> str:
        return f"CH-{self.canton}-{self.bfs}"

    def brief(self) -> dict:
        return {"municipality": self.name, "bfs_number": self.bfs, "canton": self.canton}


@dataclass
class Resolution:
    kind: str  # municipality | canton | national | ambiguous | foreign | not_found | empty
    municipality: Municipality | None = None
    canton: str | None = None
    candidates: list[Municipality] = field(default_factory=list)
    country: str | None = None
    postcode: int | None = None
    note: str | None = None


@cache
def register() -> dict:
    data = json.loads((DATA_DIR / "places.json").read_text())
    munis = [Municipality(**m) for m in data["municipalities"]]
    by_name: dict[str, list[Municipality]] = {}
    by_plz: dict[int, list[Municipality]] = {}
    by_locality: dict[str, list[Municipality]] = {}
    for m in munis:
        keys = {norm(m.name), norm(m.name.split(" (")[0])} | {norm(p) for p in m.name.split("/")}
        for k in keys:
            by_name.setdefault(k, []).append(m)
        for p in m.postcodes:
            by_plz.setdefault(p, []).append(m)
        for loc in m.localities:
            by_locality.setdefault(norm(loc), []).append(m)
    canton_names = {norm(v): code for code, names in CANTONS.items() for v in names.values()}
    return {
        "meta": {k: v for k, v in data.items() if k != "municipalities"},
        "by_bfs": {m.bfs: m for m in munis},
        "by_name": by_name,
        "by_plz": by_plz,
        "by_locality": by_locality,
        "canton_names": canton_names,
    }


def _from_list(ms: list[Municipality], **kw) -> Resolution:
    ms = list({m.bfs: m for m in ms}.values())
    if len(ms) == 1:
        return Resolution("municipality", municipality=ms[0], canton=ms[0].canton, **kw)
    return Resolution("ambiguous", candidates=ms, **kw)


async def resolve(query: str | None, canton_hint: str | None = None) -> Resolution:
    """Resolve a user place string. `canton_hint` (2-letter code) narrows ambiguous names."""
    if not query or not query.strip():
        return Resolution("empty")
    reg = register()
    raw = query.strip()
    q = norm(raw)
    hint = (canton_hint or "").upper() or None

    # "Buchs SG", "Buchs (SG)", "Lugano, TI"
    m = re.match(r"^(.*?)[\s,(]+([A-Za-z]{2})\)?$", raw)
    if m and m.group(2).upper() in CANTONS and norm(m.group(1)):
        hint = m.group(2).upper()
        q = norm(m.group(1))

    def narrow(ms: list[Municipality]) -> list[Municipality]:
        return [x for x in ms if x.canton == hint] or ms if hint else ms

    # "8003", "Zürich 8003", "8003 Zürich": a postcode alone, or one that agrees with the place name
    m_plz = re.fullmatch(r"(?:(\D*?)\s*)?(\d{4})(?:\s*(\D*))?", q)
    if m_plz and (m_plz.group(1) or m_plz.group(3)):
        # name and postcode given: use the postcode only if it agrees with the name
        name = norm(f"{m_plz.group(1) or ''} {m_plz.group(3) or ''}")
        name = norm(ALIASES.get(name, name))
        in_plz = [x for x in reg["by_plz"].get(int(m_plz.group(2)), [])
                  if name in (norm(x.name.split(" (")[0]), *map(norm, x.localities))]
        if in_plz:
            return _from_list(in_plz, postcode=int(m_plz.group(2)))
        q = name
        m_plz = None
    if m_plz:
        plz = int(m_plz.group(2))
        ms = narrow(reg["by_plz"].get(plz, []))
        if not ms:
            return Resolution("not_found", note=f"No Swiss postcode {plz}.")
        return _from_list(ms, postcode=plz)

    if q in FOREIGN:
        return Resolution("foreign", country=FOREIGN[q])
    if q in ("schweiz", "suisse", "svizzera", "svizra", "switzerland", "ch"):
        return Resolution("national")
    is_canton_code = len(raw) == 2 and raw.upper() in CANTONS
    if q in reg["canton_names"] or is_canton_code:
        code = raw.upper() if is_canton_code else reg["canton_names"][q]
        # Cities sharing the canton's name (Zürich, Bern, Luzern…) resolve to the city unless the
        # user said "Kanton"; the city result still carries the canton.
        city = reg["by_name"].get(norm(ALIASES.get(q, q)))
        if city and not is_canton_code and not _KANTON_WORD.match(raw):
            return _from_list(narrow(city))
        return Resolution("canton", canton=code)

    target = ALIASES.get(q)
    ms = (reg["by_name"].get(norm(target)) if target else None) or reg["by_name"].get(q)
    if ms:
        return _from_list(narrow(ms))
    if ms := reg["by_locality"].get(q):
        return _from_list(narrow(ms))
    # Fall back to swisstopo geocoding (addresses, quarters, localities); exact matches only.
    return await _geocode(raw, hint)


async def _geocode(raw: str, hint: str | None) -> Resolution:
    reg = register()
    try:
        data = await http.fetch_json(
            "https://api3.geo.admin.ch/rest/services/api/SearchServer",
            params={"searchText": raw, "type": "locations", "limit": 5, "sr": 4326},
            check_robots=False,
        )
    except http.FetchError:
        return Resolution("not_found", note="Geocoder unavailable.")
    head = norm(raw.split(",")[0])
    for res in data.get("results", []):
        attrs = res.get("attrs", {})
        origin = attrs.get("origin")
        label = re.sub(r"<[^>]+>", "", attrs.get("label", ""))
        if origin == "address":
            # detail looks like "bahnhofstrasse 1 8001 zuerich 261 zuerich ch zh"
            m = re.search(r"\b(\d{4}) \D+ (\d{1,4}) .*\bch [a-z]{2}$", attrs.get("detail", ""))
            if m and int(m.group(2)) in reg["by_bfs"]:
                muni = reg["by_bfs"][int(m.group(2))]
                return Resolution(
                    "municipality", municipality=muni, canton=muni.canton, postcode=int(m.group(1)),
                    note=f"Address matched: {label}",
                )
        elif origin == "gg25":
            try:
                muni = reg["by_bfs"].get(int(attrs.get("featureId")))
            except (TypeError, ValueError):
                muni = None
            if muni and norm(muni.name.split(" (")[0]) == head:
                return Resolution("municipality", municipality=muni, canton=muni.canton)
        elif origin in ("zipcode", "gazetteer") and head and head in norm(label):
            lat, lon = attrs.get("lat"), attrs.get("lon")
            muni = await municipality_at(lat, lon) if lat and lon else None
            if muni and (not hint or muni.canton == hint):
                return Resolution("municipality", municipality=muni, canton=muni.canton, note=f"Place matched: {label}")
    return Resolution("not_found")


async def municipality_at(lat: float, lon: float) -> Municipality | None:
    try:
        data = await http.fetch_json(
            "https://api3.geo.admin.ch/rest/services/api/MapServer/identify",
            params={
                "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                "layers": "all:ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill",
                "tolerance": 0, "returnGeometry": "false", "sr": 4326,
            },
            check_robots=False,
        )
    except http.FetchError:
        return None
    for r in data.get("results", []):
        attrs = r.get("attributes", {})
        if attrs.get("is_current_jahr") and attrs.get("gde_nr") in register()["by_bfs"]:
            return register()["by_bfs"][attrs["gde_nr"]]
    return None


def canton_name(code: str, lang: str = "de") -> str:
    return CANTONS.get(code, {}).get(lang, code)
