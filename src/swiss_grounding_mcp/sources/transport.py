"""Public transport timetable (connections and departure boards) via transport.opendata.ch."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from .. import http
from ..models import Citation, ToolResult, needs, source_error

API = "https://transport.opendata.ch/v1"


def _hm(ts: str | None) -> str | None:
    return ts[11:16] if ts else None


def _cites(url: str) -> list[Citation]:
    return [
        Citation(title="Swiss public transport timetable (transport.opendata.ch, based on the official "
                 "opentransportdata.swiss timetable data)", url=url,
                 publisher="Opendata.ch / Open Data Platform Mobility Switzerland", level="semi-official",
                 jurisdiction="CH", retrieved_at=datetime.now().isoformat(timespec="minutes")),
        Citation(title="SBB online timetable (to verify and buy tickets)", url="https://www.sbb.ch/",
                 publisher="SBB CFF FFS", level="semi-official", jurisdiction="CH"),
    ]


async def public_transport(
    origin: str | None, destination: str | None = None, when: str | None = None, arrival: bool = False,
    limit: int = 3,
) -> ToolResult:
    if not origin:
        return needs("origin", "From which station or place?")
    try:
        dt = datetime.fromisoformat(when) if when else None
    except ValueError:
        return needs("when", "At what date and time (e.g. 2026-10-01T08:30)?")
    try:
        if destination:
            params = {"from": origin, "to": destination, "limit": max(1, min(limit, 6))}
            if dt:
                params |= {"date": dt.date().isoformat(), "time": dt.strftime("%H:%M"), "isArrivalTime": int(arrival)}
            data = await http.fetch_json(f"{API}/connections", params=params, ttl=60, check_robots=False)
            conns = []
            for c in data.get("connections", []):
                legs = [
                    {"line": f"{s['journey']['category']}{s['journey'].get('number') or ''}".strip(),
                     "from": s["departure"]["station"]["name"], "dep": _hm(s["departure"]["departure"]),
                     "platform": s["departure"].get("platform"), "to": s["arrival"]["station"]["name"],
                     "arr": _hm(s["arrival"]["arrival"])}
                    for s in c.get("sections", []) if s.get("journey")
                ]
                conns.append({
                    "departure": c["from"]["departure"], "arrival": c["to"]["arrival"],
                    "duration": c["duration"].replace("00d", "").strip(), "transfers": c["transfers"],
                    "platform": c["from"].get("platform"), "legs": legs,
                })
            if not conns:
                return ToolResult(status="not_found", summary=f"No connection found from {origin} to {destination}.",
                                  guidance="Check the station names (use the official station name, e.g. 'Zürich HB').")
            f = conns[0]
            return ToolResult(
                status="ok",
                summary=f"Next connection {data['from']['name']} → {data['to']['name']}: departs {_hm(f['departure'])}"
                f" (platform {f['platform'] or '?'}), arrives {_hm(f['arrival'])}, {f['transfers']} transfer(s).",
                data={"from": data["from"]["name"], "to": data["to"]["name"], "connections": conns},
                citations=_cites(f"{API}/connections?from={quote(origin)}&to={quote(destination)}"),
                guidance="Times are scheduled times (local, Europe/Zurich). Mention that delays may apply.",
            )
        params = {"station": origin, "limit": max(1, min(limit * 3, 15))}
        if dt:
            params["datetime"] = dt.strftime("%Y-%m-%d %H:%M")
        data = await http.fetch_json(f"{API}/stationboard", params=params, ttl=60, check_robots=False)
        board = [
            {"time": _hm(s["stop"]["departure"]), "line": f"{s['category']}{s.get('number') or ''}",
             "to": s["to"], "platform": s["stop"].get("platform"), "delay_min": s["stop"].get("delay")}
            for s in data.get("stationboard", [])
        ]
        if not board:
            return ToolResult(status="not_found", summary=f"No departures found for '{origin}'.",
                              guidance="Check the station name.")
        return ToolResult(
            status="ok",
            summary=f"Next departures from {data['station']['name']}: "
            + "; ".join(f"{b['time']} {b['line']} to {b['to']}" for b in board[:3]) + ".",
            data={"station": data["station"]["name"], "departures": board},
            citations=_cites(f"{API}/stationboard?station={quote(origin)}"),
        )
    except (http.FetchError, KeyError, TypeError) as e:
        return source_error("The timetable service", e)
