"""Build src/swiss_grounding_mcp/data/places.json — the Swiss municipality register.

Sources (all official or open, key-free):
  - BFS official municipality register (agvchapp.bfs.admin.ch)            -> BFS number, name, canton
  - BAG premium regions (priminfo.admin.ch/downloads/praemienregionen.xlsx) -> premium region, postcodes
  - BFS STATPOP via PXWeb (px-x-0102010000_101)                           -> permanent resident population
  - Wikidata (P771 BFS code, P856 official website)                       -> municipality website
  - OpenHolidays subdivisions                                             -> school-holiday region code

Run: uv run --group build python scripts/build_places.py
"""

from __future__ import annotations

import csv
import io
import json
import sys
import unicodedata
from datetime import date
from pathlib import Path

import httpx
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swiss_grounding_mcp.config import DATA_DIR, settings  # noqa: E402
from swiss_grounding_mcp.places import official_website, website_overrides  # noqa: E402

H = {"User-Agent": settings.user_agent}
POP_URL = "https://www.pxweb.bfs.admin.ch/api/v1/de/px-x-0102010000_101/px-x-0102010000_101.px"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").replace("/", " ").split())


def communes(day: date) -> dict[int, dict]:
    r = httpx.get(
        f"https://www.agvchapp.bfs.admin.ch/api/communes/snapshot?date={day:%d-%m-%Y}", headers=H, timeout=60
    )
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    by_hist = {row["HistoricalCode"]: row for row in rows}
    out = {}
    for row in rows:
        if row["Level"] != "3":
            continue
        parent = by_hist.get(row["Parent"])
        while parent and parent["Level"] != "1":
            parent = by_hist.get(parent["Parent"])
        district = by_hist.get(row["Parent"])
        out[int(row["BfsCode"])] = {
            "bfs": int(row["BfsCode"]),
            "name": row["Name"],
            "canton": parent["ShortName"] if parent else None,
            "district": district["Name"] if district and district["Level"] == "2" else None,
        }
    return out


def premium_regions() -> dict[int, dict]:
    r = httpx.get("https://www.priminfo.admin.ch/downloads/praemienregionen.xlsx", headers=H, timeout=60)
    r.raise_for_status()
    ws = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True)["A_COM"]
    out: dict[int, dict] = {}
    for row in ws.iter_rows(min_row=6, values_only=True):
        bfs, _canton, _name, region, _district, plz, ort = row[:7]
        if not isinstance(bfs, int):
            continue
        entry = out.setdefault(bfs, {"premium_region": int(region), "postcodes": set(), "localities": set()})
        entry["postcodes"].add(int(plz))
        entry["localities"].add(str(ort))
    return out


def population() -> tuple[dict[int, int], str]:
    query = {
        "query": [
            {"code": "Jahr", "selection": {"filter": "top", "values": ["1"]}},
            {"code": "Kanton (-) / Bezirk (>>) / Gemeinde (......)", "selection": {"filter": "all", "values": ["*"]}},
            {"code": "Bevölkerungstyp", "selection": {"filter": "item", "values": ["1"]}},
            {"code": "Staatsangehörigkeit (Kategorie)", "selection": {"filter": "item", "values": ["-99999"]}},
            {"code": "Geschlecht", "selection": {"filter": "item", "values": ["-99999"]}},
            {"code": "Alter", "selection": {"filter": "item", "values": ["-99999"]}},
        ],
        "response": {"format": "csv"},
    }
    r = httpx.post(POP_URL, json=query, headers=H, timeout=180)
    r.raise_for_status()
    out, year = {}, ""
    for row in csv.reader(io.StringIO(r.content.decode("latin-1"))):
        if len(row) < 6 or not row[1].startswith("......"):
            continue
        year = row[0]
        out[int(row[1][6:10])] = int(row[5])
    return out, year


def websites() -> dict[int, str]:
    q = """SELECT ?bfs ?website WHERE { ?item wdt:P31 wd:Q70208; wdt:P771 ?bfs.
      FILTER NOT EXISTS { ?item wdt:P576 ?end } ?item wdt:P856 ?website }"""
    r = httpx.get(
        "https://query.wikidata.org/sparql",
        params={"query": q, "format": "json"},
        headers={**H, "Accept": "application/sparql-results+json"},
        timeout=120,
    )
    r.raise_for_status()
    out: dict[int, str] = {}
    for b in r.json()["results"]["bindings"]:
        try:
            bfs = int(b["bfs"]["value"])
        except ValueError:
            continue
        url = b["website"]["value"]
        # prefer the shortest URL (usually the root domain rather than a language sub-page)
        if bfs not in out or len(url) < len(out[bfs]):
            out[bfs] = url
    return out


def holiday_codes() -> dict[str, dict[str, str]]:
    """canton -> {normalized municipality name -> deepest OpenHolidays subdivision code}."""
    r = httpx.get(
        "https://openholidaysapi.org/Subdivisions",
        params={"countryIsoCode": "CH", "languageIsoCode": "DE"},
        headers=H,
        timeout=60,
    )
    r.raise_for_status()
    out: dict[str, dict[str, str]] = {}

    def walk(node: dict, canton: str) -> None:
        for name in node["name"]:
            for part in [name["text"], *name["text"].split("/")]:
                out.setdefault(canton, {}).setdefault(norm(part), node["code"])
        for child in node.get("children") or []:
            walk(child, canton)

    for canton_node in r.json():
        canton = canton_node["code"].split("-")[1]
        for child in canton_node.get("children") or []:
            walk(child, canton)
    return out


def main() -> None:
    day = date.today()
    print("communes…", flush=True)
    muni = communes(day)
    print(f"  {len(muni)} municipalities\npremium regions…", flush=True)
    regions = premium_regions()
    print("population (slow, ~30 s)…", flush=True)
    pop, pop_year = population()
    print(f"  {len(pop)} rows, year {pop_year}\nwebsites…", flush=True)
    sites = websites()
    print(f"  {len(sites)} websites\nholiday subdivisions…", flush=True)
    hol = holiday_codes()

    records = []
    for bfs, m in sorted(muni.items()):
        reg = regions.get(bfs, {})
        records.append(
            {
                **m,
                "premium_region": reg.get("premium_region"),
                "postcodes": sorted(reg.get("postcodes", [])),
                "localities": sorted(reg.get("localities", [])),
                "website": official_website(sites.get(bfs)),
                "population": pop.get(bfs),
                "holiday_code": hol.get(m["canton"], {}).get(norm(m["name"].split(" (")[0])),
            }
        )
    fields = ("premium_region", "website", "population", "holiday_code")
    print("missing:", {k: sum(1 for r in records if r[k] is None) for k in fields})
    out = {
        "built": day.isoformat(),
        "population_year": pop_year,
        "sources": {
            "register": "https://www.agvchapp.bfs.admin.ch/de/communes/query",
            "premium_regions": "https://www.priminfo.admin.ch/downloads/praemienregionen.xlsx",
            "population": "https://www.pxweb.bfs.admin.ch/pxweb/de/px-x-0102010000_101/",
            "websites": "https://www.wikidata.org/ (P856)",
            "holidays": "https://openholidaysapi.org/",
        },
        "municipalities": records,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "places.json"
    report = website_report(path, records, sites)
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")
    # municipal websites come from Wikidata (editable by anyone) and feed the official-domain allowlist,
    # so every change is listed for review; the refresh workflow puts this file in the pull request
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report)
    print(report)


REPORT = ROOT / "build" / "website_changes.md"


def website_report(previous_path: Path, records: list[dict], raw: dict[int, str]) -> str:
    try:
        before = {m["bfs"]: m["website"] for m in json.loads(previous_path.read_text())["municipalities"]}
    except (OSError, ValueError, KeyError):
        before = {}
    names = {r["bfs"]: r["name"] for r in records}
    changed = [(bfs, before.get(bfs), r["website"]) for bfs, r in ((r["bfs"], r) for r in records)
               if before.get(bfs) != r["website"]]
    reviewed = website_overrides()
    rejected = [(bfs, url) for bfs, url in sorted(raw.items())
                if url and not official_website(url) and bfs in names and bfs not in reviewed]
    lines = ["## Municipal website changes (from Wikidata)", ""]
    lines += [f"- {names[b]} (BFS {b}): {old or '—'} → {new or '—'}" for b, old, new in changed] or ["- none"]
    lines += ["", "## Rejected by the official-website check, not yet reviewed (add exceptions to data/website_overrides.json)", ""]
    lines += [f"- {names[b]} (BFS {b}): {url}" for b, url in rejected] or ["- none"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
