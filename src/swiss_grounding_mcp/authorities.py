"""Which web domains are authoritative Swiss sources, and at what level.

Used to label citations (publisher, level, jurisdiction) and as the allowlist for live page reads,
so the server never presents a commercial or foreign page as an official Swiss source.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from urllib.parse import urlsplit

from .places import register

CANTON_DOMAINS = {
    "zh.ch": "ZH", "be.ch": "BE", "lu.ch": "LU", "ur.ch": "UR", "sz.ch": "SZ", "ow.ch": "OW",
    "nw.ch": "NW", "gl.ch": "GL", "zg.ch": "ZG", "fr.ch": "FR", "so.ch": "SO", "bs.ch": "BS",
    "baselland.ch": "BL", "bl.ch": "BL", "sh.ch": "SH", "ar.ch": "AR", "ai.ch": "AI", "sg.ch": "SG",
    "gr.ch": "GR", "ag.ch": "AG", "tg.ch": "TG", "ti.ch": "TI", "vd.ch": "VD", "vs.ch": "VS",
    "ne.ch": "NE", "ge.ch": "GE", "jura.ch": "JU",
}

# Bodies with a legal mandate or joint federal/cantonal bodies (not under admin.ch).
SEMI_OFFICIAL = {
    "ahv-iv.ch": "AHV/IV Informationsstelle",
    "arbeit.swiss": "SECO / Arbeitslosenversicherung",
    "edk.ch": "EDK (Swiss Conference of Cantonal Ministers of Education)",
    "edudoc.ch": "EDK documentation",
    "asa.ch": "asa (Association of Road Traffic Offices)",
    "serafe.ch": "Serafe AG (federal fee collection agency)",
    "opentransportdata.swiss": "Open Data Platform Mobility Switzerland",
    "opendata.swiss": "opendata.swiss (Federal Statistical Office)",
    "zefix.ch": "Zefix (Federal Office of Justice)",
    "sbb.ch": "SBB CFF FFS",
}

# Municipal bodies on their own domains (not the municipality's main website): domain -> BFS number
MUNICIPAL_EXTRA = {"scoula-scuol.ch": 3762, "stadt-zuerich.ch": 261}

FEDERAL_NAMES = {
    "ch.ch": "ch.ch (Swiss Confederation portal)",
    "fedlex.admin.ch": "Fedlex (Federal Chancellery)",
    "sem.admin.ch": "State Secretariat for Migration SEM",
    "bwo.admin.ch": "Federal Office for Housing BWO",
    "bazg.admin.ch": "Federal Office for Customs and Border Security BAZG",
    "bakom.admin.ch": "Federal Office of Communications OFCOM",
    "estv.admin.ch": "Federal Tax Administration FTA",
    "bsv.admin.ch": "Federal Social Insurance Office FSIO",
    "bk.admin.ch": "Federal Chancellery",
    "admin.ch": "Swiss Federal Administration",
    "bag.admin.ch": "Federal Office of Public Health FOPH",
    "priminfo.admin.ch": "FOPH premium calculator (priminfo)",
    "seco.admin.ch": "State Secretariat for Economic Affairs SECO",
    "astra.admin.ch": "Federal Roads Office FEDRO",
    "bfs.admin.ch": "Federal Statistical Office FSO",
    "meteoschweiz.admin.ch": "MeteoSwiss",
    "bj.admin.ch": "Federal Office of Justice",
    "geo.admin.ch": "swisstopo",
    "snb.ch": "Swiss National Bank",
}


@dataclass(frozen=True)
class Authority:
    publisher: str
    level: str  # federal | cantonal | municipal | semi-official
    jurisdiction: str


def _host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _suffix_match(host: str, table: dict) -> str | None:
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in table:
            return candidate
    return None


@cache
def _municipal_domains() -> dict[str, tuple[str, str]]:
    out = {}
    for m in register()["by_bfs"].values():
        if m.website:
            host = _host(m.website)
            if host and host not in CANTON_DOMAINS:
                out.setdefault(host, (f"Municipality of {m.name}", m.jurisdiction))
    return out


def classify(url: str) -> Authority | None:
    """Return the authority behind a URL, or None if it is not a recognised official Swiss source."""
    host = _host(url)
    if not host:
        return None
    if key := _suffix_match(host, FEDERAL_NAMES):
        return Authority(FEDERAL_NAMES[key], "federal", "CH")
    if host.endswith(".admin.ch"):
        return Authority(host, "federal", "CH")
    # The most specific domain wins: the City of St. Gallen publishes on stadt.sg.ch, under the canton's
    # sg.ch, and its pages must be attributed to the city, not to the canton.
    municipal = _municipal_domains()
    candidates = []
    if key := _suffix_match(host, CANTON_DOMAINS):
        canton = CANTON_DOMAINS[key]
        candidates.append((key, Authority(f"Canton of {canton} ({key})", "cantonal", f"CH-{canton}")))
    if key := _suffix_match(host, SEMI_OFFICIAL):
        candidates.append((key, Authority(SEMI_OFFICIAL[key], "semi-official", "CH")))
    if key := _suffix_match(host, MUNICIPAL_EXTRA):
        m = register()["by_bfs"][MUNICIPAL_EXTRA[key]]
        candidates.append((key, Authority(f"Municipality of {m.name} ({key})", "municipal", m.jurisdiction)))
    if key := _suffix_match(host, municipal):
        name, jurisdiction = municipal[key]
        candidates.append((key, Authority(name, "municipal", jurisdiction)))
    return max(candidates, key=lambda c: len(c[0]))[1] if candidates else None
