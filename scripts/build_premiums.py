"""Build the health-insurance premium tables from the official BAG open data.

Source: opendata.swiss dataset "health-insurance-premiums" (Federal Office of Public Health),
files hosted on opendata.bagnet.ch. Each year's archive contains Prämien_CH.csv (monthly premiums
per insurer, canton, premium region, age class, accident cover, plan, deductible),
Einzugsgebiete.csv (plans limited to some municipalities) and Tarife.csv (plan names).

Writes src/swiss_grounding_mcp/data/premiums_<year>.csv.gz and premium_meta_<year>.json.
Run: uv run --group build python scripts/build_premiums.py 2026 [2027]
"""

from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import sys
import unicodedata
import zipfile
from pathlib import Path

import httpx
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swiss_grounding_mcp.config import DATA_DIR, settings  # noqa: E402

H = {"User-Agent": settings.user_agent}
DATASET = "https://ckan.opendata.swiss/api/3/action/package_show?id=health-insurance-premiums"


def _decode(b: bytes) -> str:
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _path_of(url: str) -> str:
    return base64.b64decode(httpx.URL(url).params.get("path", "") + "==").decode("utf-8", "ignore")


def archive_files(year: int) -> dict[str, str]:
    """{filename: text} from the year's archive, or from the loose 'current' files when that year
    has not been archived yet (new premiums are first published as loose files)."""
    resources = httpx.get(DATASET, headers=H, timeout=60).json()["result"]["resources"]
    by_path = {_path_of(r["url"]): r["url"] for r in resources}
    zip_url = next((u for p, u in by_path.items() if p.endswith(f"Archiv_Praemien_{year}.zip")), None)
    files: dict[str, str] = {}
    if zip_url:
        z = zipfile.ZipFile(io.BytesIO(httpx.get(zip_url, headers=H, timeout=300).content))
        for name in z.namelist():
            if name.endswith(".csv"):
                files[unicodedata.normalize("NFC", Path(name).name)] = _decode(z.read(name))
        return files
    for p, u in by_path.items():
        name = unicodedata.normalize("NFC", Path(p).name)
        if name.endswith(".csv"):
            files[name] = _decode(httpx.get(u, headers=H, timeout=300).content)
    return files


def insurer_names() -> dict[str, str]:
    url = "https://www.priminfo.admin.ch/downloads/zugelassene-krankenversicherer-{}-01-01.xlsx"
    for year in (2027, 2026):
        r = httpx.get(url.format(year), headers=H, timeout=60)
        if r.status_code == 200 and r.content[:2] == b"PK":
            ws = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True)["Index "]
            return {
                f"{row[1]:04d}": str(row[3]).strip()
                for row in ws.iter_rows(values_only=True)
                if isinstance(row[1], int) and row[3]
            }
    return {}


def build(year: int) -> None:
    files = archive_files(year)
    rows = [r for r in csv.DictReader(io.StringIO(files.get("Prämien_CH.csv", ""))) if r.get("Geschäftsjahr") == str(year)]
    if not rows:
        print(f"{year}: no premium rows published yet — skipped")
        return
    tariffs = {
        (r["Versicherer"], r["Tarif"]): r["Name_DE"]
        for r in csv.DictReader(io.StringIO(files.get("Tarife.csv", "")), delimiter=";")
    }
    restricted = {}
    for r in csv.DictReader(io.StringIO(files.get("Einzugsgebiete.csv", "")), delimiter=";"):
        if r.get("Eingeschränkt") == "Y" and r.get("Gemeinden-BFS"):
            key = f"{int(r['Versicherer']):04d}|{r['Kanton']}|{r['Tarif']}"
            restricted[key] = sorted({int(x) for x in r["Gemeinden-BFS"].replace(",", " ").split() if x.isdigit()})

    out = DATA_DIR / f"premiums_{year}.csv.gz"
    with gzip.open(out, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["insurer", "canton", "region", "age_class", "age_subgroup", "accident", "tariff",
                    "tariff_type", "tariff_name", "franchise", "premium", "is_base"])
        for r in rows:
            ins = f"{int(r['Versicherer']):04d}"
            w.writerow([
                ins, r["Kanton"], int(r["Region"].split("CH")[-1]), r["Altersklasse"].removeprefix("AKL-"),
                r["Altersuntergruppe"], 1 if r["Unfalleinschluss"] == "MIT-UNF" else 0, r["Tarif"],
                r["Tariftyp"].removeprefix("TAR-"), r["Tarifbezeichnung"] or tariffs.get((ins, r["Tarif"]), ""),
                int(r["Franchise"].removeprefix("FRA-")), r["Prämie"],
                1 if r["isBaseP"] == "1" and r["isBaseF"] == "1" else 0,
            ])
    meta = {"year": year, "insurers": insurer_names(), "restricted_plans": restricted, "rows": len(rows)}
    (DATA_DIR / f"premium_meta_{year}.json").write_text(json.dumps(meta, ensure_ascii=False))
    print(f"{year}: {len(rows)} rows, {len(restricted)} restricted plans -> {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["2026"]:
        build(int(arg))
