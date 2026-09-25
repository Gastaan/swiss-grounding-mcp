"""Federal popular votes: subjects of the next vote and official results (FSO vote-day open data)."""

from __future__ import annotations

import re
from datetime import date

from .. import http
from ..models import Citation, ToolResult, source_error
from ..places import resolve

DATASET = "echtzeitdaten-am-abstimmungstag-zu-eidgenoessischen-abstimmungsvorlagen"
FILE = "https://ogd-static.voteinfo-app.ch/v1/ogd/sd-t-17-02-{}-eidgAbstimmung.json"
DATES_PAGE = {"de": "https://www.admin.ch/de/abstimmungstermine", "fr": "https://www.admin.ch/fr/votations",
              "it": "https://www.admin.ch/it/votazioni"}


async def _vote_dates() -> list[str]:
    data = await http.fetch_json("https://ckan.opendata.swiss/api/3/action/package_show",
                                 params={"id": DATASET}, ttl=6 * http.HOUR, check_robots=False)
    urls = " ".join(r["url"] for r in data["result"]["resources"])
    return sorted(set(re.findall(r"sd-t-17-02-(\d{8})-eidgAbstimmung", urls)))


def _title(v: dict, lang: str) -> str:
    titles = {t["langKey"]: t["text"] for t in v["vorlagenTitel"]}
    return titles.get(lang) or titles.get("de", "")


def _iso(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


async def federal_votes(vote_date: str | None = None, place: str | None = None, language: str = "de") -> ToolResult:
    lang = language if language in ("de", "fr", "it", "rm", "en") else "de"
    dates_cite = Citation(title="Federal votes — dates and proposals", url=DATES_PAGE.get(lang, DATES_PAGE["de"]),
                          publisher="Federal Chancellery", level="federal", jurisdiction="CH")
    try:
        dates = await _vote_dates()
        if vote_date:
            chosen = vote_date.replace("-", "")[:8]
            if chosen not in dates:
                return ToolResult(
                    status="not_found",
                    summary=f"No federal vote on {vote_date} in the official data. Recent and upcoming dates: "
                    + ", ".join(_iso(d) for d in dates[-6:]) + ".",
                    citations=[dates_cite],
                )
        else:
            upcoming = [d for d in dates if d >= date.today().strftime("%Y%m%d")]
            chosen = upcoming[0] if upcoming else dates[-1]
        data = await http.fetch_json(FILE.format(chosen), ttl=10 * 60, check_robots=False)
    except (http.FetchError, KeyError) as e:
        return source_error("The federal vote data", e, [dates_cite])

    canton = (await resolve(place)).canton if place else None
    items = []
    for v in data["schweiz"]["vorlagen"]:
        r = v.get("resultat") or {}
        # "provisorisch" stays true on vote day: the Federal Council validates results weeks later
        counted = bool(v.get("vorlageBeendet"))
        item = {"title": _title(v, lang), "yes_percent": r.get("jaStimmenInProzent"),
                "turnout_percent": r.get("stimmbeteiligungInProzent"), "counting_complete": counted,
                "accepted": v.get("vorlageAngenommen") if counted else None}
        if canton:
            for k in v.get("kantone", []):
                if canton in (k.get("geoLevelname", ""), str(k.get("geoLevelnummer"))) or \
                        k.get("geoLevelname", "").upper().startswith(canton):
                    item["canton_yes_percent"] = (k.get("resultat") or {}).get("jaStimmenInProzent")
        items.append(item)
    iso = _iso(chosen)
    counted = any(i["yes_percent"] is not None for i in items)

    def line(i: dict) -> str:
        if i["yes_percent"] is None:
            return i["title"]
        verdict = "accepted" if i["accepted"] else "rejected" if i["accepted"] is False else "counting in progress"
        return f"{i['title']} — {i['yes_percent']:.2f}% yes ({verdict})"

    return ToolResult(
        status="ok",
        summary=f"Federal vote of {iso}: " + "; ".join(line(i) for i in items),
        data={"vote_date": iso, "status": "results" if counted else "upcoming", "proposals": items,
              "data_timestamp": data.get("timestamp"), "known_vote_dates": [_iso(d) for d in dates[-4:]]},
        citations=[Citation(title=f"Federal vote {iso} — official vote-day data (FSO)", url=FILE.format(chosen),
                            publisher="Federal Statistical Office FSO", level="federal", jurisdiction="CH",
                            valid_for=iso, retrieved_at=date.today().isoformat()), dates_cite],
        guidance=("Not counted yet: list the proposals. For later vote dates, cite the Federal Chancellery page."
                  if not counted else "Vote-day results (FSO). Accepted/rejected is final once counting is complete; the Federal Council's formal validation follows weeks later."),
    )
