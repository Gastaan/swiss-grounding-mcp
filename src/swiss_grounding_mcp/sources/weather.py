"""Current weather observations from the nearest MeteoSwiss automatic station (open data, CC BY)."""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .. import http
from ..models import Citation, ToolResult, today
from ..places import norm, resolve
from .common import place_problem

BASE = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"
PARAMS = {"tre200s0": "temperature_c", "ure200s0": "humidity_percent", "rre150z0": "precipitation_mm_10min",
          "fu3010z0": "wind_kmh", "sre000z0": "sunshine_min_10min"}


async def _stations() -> list[dict]:
    body = await http.fetch(f"{BASE}/ogd-smn_meta_stations.csv", ttl=7 * http.DAY, check_robots=False)
    return list(csv.DictReader(io.StringIO(body), delimiter=";"))


async def _coords(name: str) -> tuple[float, float] | None:
    data = await http.fetch_json("https://api3.geo.admin.ch/rest/services/api/SearchServer",
                                 params={"searchText": name, "type": "locations", "origins": "gg25", "limit": 1,
                                         "sr": 4326}, ttl=30 * http.DAY, check_robots=False)
    res = data.get("results")
    return (res[0]["attrs"]["lat"], res[0]["attrs"]["lon"]) if res else None


def _pick(stations: list[dict], name: str, here: tuple[float, float]) -> dict:
    """The station named after the municipality, else the nearest one below 2000 m (summit stations
    do not represent a town's weather), else the nearest one."""
    key = norm(name)
    named = [s for s in stations if norm(s["station_name"].split("/")[0]) == key]
    if named:
        return min(named, key=lambda s: _dist(here, _pos(s)))
    low = [s for s in stations if float(s["station_height_masl"] or 0) < 2000] or stations
    return min(low, key=lambda s: _dist(here, _pos(s)))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _pos(s: dict) -> tuple[float, float]:
    return float(s["station_coordinates_wgs84_lat"]), float(s["station_coordinates_wgs84_lon"])


async def current_weather(place: str | None, language: str = "de") -> ToolResult:
    res = await resolve(place)
    if problem := place_problem(res, "the location of the weather station"):
        return problem
    m = res.municipality
    try:
        here = await _coords(m.name.split(" (")[0])
        if not here:
            return ToolResult(status="not_found", summary=f"No coordinates found for {m.name}.")
        best = _pick(await _stations(), m.name.split(" (")[0], here)
        abbr = best["station_abbr"].lower()
        body = await http.fetch(f"{BASE}/{abbr}/ogd-smn_{abbr}_t_now.csv", ttl=10 * 60, check_robots=False)
    except (http.FetchError, KeyError, ValueError) as e:
        return ToolResult(status="source_error", summary=f"MeteoSwiss data could not be retrieved ({e}).",
                          guidance="Tell the user to check meteoswiss.admin.ch; do not guess.")
    rows = list(csv.DictReader(io.StringIO(body), delimiter=";"))
    last = next((r for r in reversed(rows) if r.get("tre200s0")), None)
    if not last:
        return ToolResult(status="not_found", summary=f"No recent measurement at station {best['station_name']}.")
    measured = datetime.strptime(last["reference_timestamp"], "%d.%m.%Y %H:%M").replace(tzinfo=UTC)
    local = measured.astimezone(ZoneInfo("Europe/Zurich"))
    values = {}
    for code, key in PARAMS.items():
        try:
            values[key] = float(last[code]) if last.get(code) else None
        except ValueError:
            values[key] = None
    km = _dist(here, _pos(best))
    url_key = {"de": "station_url_de", "fr": "station_url_fr", "it": "station_url_it"}.get(language, "station_url_en")
    return ToolResult(
        status="ok",
        summary=f"MeteoSwiss station {best['station_name']} ({km:.0f} km from {m.name}, "
        f"{float(best['station_height_masl']):.0f} m a.s.l.) at {local:%d.%m.%Y %H:%M}: "
        f"{values['temperature_c']} °C, humidity {values['humidity_percent']} %, "
        f"precipitation {values['precipitation_mm_10min']} mm in the last 10 minutes.",
        data={"station": best["station_name"], "distance_km": round(km, 1), "measured_at": local.isoformat(),
              **values},
        citations=[Citation(title=f"MeteoSwiss measurements — station {best['station_name']}",
                            url=best.get(url_key) or "https://www.meteoswiss.admin.ch", publisher="MeteoSwiss",
                            level="federal", jurisdiction="CH", retrieved_at=today())],
        guidance="These are current measurements, not a forecast. This server does not provide forecasts; "
        "point to meteoswiss.admin.ch for them.",
    )
