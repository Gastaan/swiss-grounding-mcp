"""Mandatory health insurance (KVG/LAMal) premiums from the official FOPH (BAG) premium data."""

from __future__ import annotations

import asyncio
import csv
import gzip
import json
from datetime import date
from functools import cache

from ..config import DATA_DIR
from ..models import Citation, ToolResult, needs
from ..places import resolve
from .common import place_problem

ADULT_FRANCHISES = [300, 500, 1000, 1500, 2000, 2500]
CHILD_FRANCHISES = [0, 100, 200, 300, 400, 500, 600]
MODEL_CODES = {"free_choice_of_doctor": "BASE", "family_doctor": "HAM", "hmo": "HMO", "telmed_or_other": "DIV"}
PLAN_TYPES = {
    "BASE": "standard model (free choice of doctor)",
    "HAM": "family-doctor model",
    "HMO": "HMO model",
    "DIV": "other alternative model (e.g. telemedicine first)",
}


def available_years() -> list[int]:
    return sorted(int(p.name.split("_")[1].split(".")[0]) for p in DATA_DIR.glob("premiums_*.csv.gz"))


@cache
def _table(year: int) -> tuple[dict, list[dict]]:
    meta = json.loads((DATA_DIR / f"premium_meta_{year}.json").read_text())
    with gzip.open(DATA_DIR / f"premiums_{year}.csv.gz", "rt", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return meta, rows


def _age_class(age: int) -> str:
    return "KIN" if age <= 18 else "JUG" if age <= 25 else "ERW"


def _citations(year: int, jurisdiction: str) -> list[Citation]:
    return [
        Citation(
            title=f"Priminfo — official premium calculator (premiums {year})",
            url="https://www.priminfo.admin.ch/de/praemien",
            publisher="Federal Office of Public Health FOPH", level="federal",
            jurisdiction=jurisdiction, valid_for=str(year),
        ),
        Citation(
            title=f"Health insurance premiums {year} (open data, Prämien_CH.csv)",
            url="https://opendata.swiss/de/dataset/health-insurance-premiums",
            publisher="Federal Office of Public Health FOPH", level="federal",
            jurisdiction="CH", valid_for=str(year),
        ),
    ]


async def premiums(
    place: str | None,
    age: int | None,
    franchise: int | None = None,
    accident_cover: bool = False,
    plan_type: str | None = None,
    year: int | None = None,
    limit: int = 5,
) -> ToolResult:
    years = available_years()
    year = year or (date.today().year if date.today().year in years else years[-1])
    if year not in years:
        return ToolResult(
            status="not_covered",
            summary=f"Premiums for {year} are not available here (available: {', '.join(map(str, years))}). "
            "The FOPH publishes next year's premiums at the end of September.",
            citations=_citations(years[-1], "CH"),
        )
    if age is None:
        return needs("age", "How old is the insured person?")
    meta, rows = await asyncio.to_thread(_table, year)  # first call parses ~1 MB; keep the loop free
    res = await resolve(place)
    if res.kind == "canton" and res.canton:
        # premium regions depend on the municipality, except in single-region cantons
        regions = {r["region"] for r in rows if r["canton"] == res.canton}
        if len(regions) > 1:
            return needs("municipality", f"In which municipality (or postcode) in canton {res.canton} does the "
                         "insured person live? Premiums differ by premium region within the canton.")
        muni, canton, region = None, res.canton, next(iter(regions))
    else:
        if problem := place_problem(res, "the insured person's municipality of residence"):
            return problem
        muni = res.municipality
        canton, region = muni.canton, str(muni.premium_region)

    model_code = MODEL_CODES.get(plan_type or "", (plan_type or "").upper() or None)
    age_class = _age_class(age)
    valid = CHILD_FRANCHISES if age_class == "KIN" else ADULT_FRANCHISES
    if franchise is not None and franchise not in valid:
        return needs("franchise", f"Which deductible (franchise)? Allowed for this age: {valid} CHF.",
                     options=[str(v) for v in valid])

    insurers = meta["insurers"]
    restricted = meta["restricted_plans"]

    def eligible(r: dict) -> bool:
        if r["canton"] != canton or r["region"] != region or r["age_class"] != age_class:
            return False
        if age_class == "KIN" and r["age_subgroup"] != "K1":
            return False
        if model_code and r["tariff_type"] != model_code:
            return False
        limited = restricted.get(f"{r['insurer']}|{canton}|{r['tariff']}")
        return not (limited and muni and muni.bfs not in limited)

    subset = [r for r in rows if eligible(r)]
    acc = "1" if accident_cover else "0"
    where = muni.name if muni else f"canton {canton}"
    jurisdiction = muni.jurisdiction if muni else f"CH-{canton}"

    def fmt(r: dict) -> dict:
        return {
            "insurer": insurers.get(r["insurer"], r["insurer"]),
            "plan": r["tariff_name"],
            "plan_type": PLAN_TYPES.get(r["tariff_type"], r["tariff_type"]),
            "franchise_chf": int(r["franchise"]),
            "monthly_premium_chf": float(r["premium"]),
        }

    base = {
        "year": year, "premium_region": f"{canton} region {region}", "place": where,
        "age_class": {"KIN": "child 0-18", "JUG": "young adult 19-25", "ERW": "adult 26+"}[age_class],
        "accident_cover": accident_cover,
    }
    if franchise is None:
        # cheapest plan per deductible — answers "cheapest premium" without asking back
        per: dict[int, dict] = {}
        for r in subset:
            if r["accident"] == acc:
                f = int(r["franchise"])
                if f not in per or float(r["premium"]) < float(per[f]["premium"]):
                    per[f] = r
        if not per:
            return ToolResult(status="not_found", summary=f"No {year} premiums found for these criteria in {where}.",
                              citations=_citations(year, jurisdiction))
        table = [fmt(per[f]) for f in sorted(per)]
        cheapest = min(table, key=lambda x: x["monthly_premium_chf"])
        return ToolResult(
            status="ok",
            summary=f"{year} lowest monthly premium in {where} for age {age} "
            f"({'with' if accident_cover else 'without'} accident cover): CHF {cheapest['monthly_premium_chf']:.2f} "
            f"({cheapest['insurer']}, {cheapest['plan']}, deductible CHF {cheapest['franchise_chf']}).",
            data={**base, "cheapest_per_franchise": table},
            citations=_citations(year, jurisdiction),
            guidance="Premiums are monthly CHF amounts from the official FOPH data. Mention the year, the "
            "deductible and the accident-cover assumption. Employees working 8+ hours a week are usually "
            "insured against accidents by their employer, hence 'without accident cover' is the default.",
        )

    subset = [r for r in subset if int(r["franchise"]) == franchise]
    ranked = sorted((r for r in subset if r["accident"] == acc), key=lambda r: float(r["premium"]))
    if not ranked:
        return ToolResult(status="not_found", summary=f"No {year} premiums found for these criteria in {where}.",
                          citations=_citations(year, jurisdiction))
    top = [fmt(r) for r in ranked[:limit]]
    std = next((fmt(r) for r in ranked if r["tariff_type"] == "BASE"), None)
    other_acc = {(r["insurer"], r["tariff"]): float(r["premium"]) for r in subset if r["accident"] != acc}
    best = ranked[0]
    alt = other_acc.get((best["insurer"], best["tariff"]))
    summary = (
        f"{year} cheapest monthly premium in {where} for age {age}, deductible CHF {franchise}, "
        f"{'with' if accident_cover else 'without'} accident cover: CHF {float(best['premium']):.2f} "
        f"({insurers.get(best['insurer'], best['insurer'])}, plan '{best['tariff_name']}', "
        f"{PLAN_TYPES.get(best['tariff_type'], best['tariff_type'])})."
    )
    if alt:
        summary += f" Same plan {'without' if accident_cover else 'with'} accident cover: CHF {alt:.2f}."
    if std:
        summary += (f" Cheapest standard model (free choice of doctor): CHF {std['monthly_premium_chf']:.2f} "
                    f"({std['insurer']}).")
    return ToolResult(
        status="ok",
        summary=summary,
        data={**base, "franchise_chf": franchise, "cheapest": top, "cheapest_standard_model": std,
              "plans_compared": len(ranked)},
        citations=_citations(year, jurisdiction),
        guidance="Answer with the cheapest premium, the insurer and plan, and state the year and "
        "accident-cover assumption. Alternative models restrict the choice of doctor.",
    )
