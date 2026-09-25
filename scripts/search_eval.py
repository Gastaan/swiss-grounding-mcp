"""Measure search_official_info on hand-labelled questions, keyword-only and hybrid.

A question is answered when an official page on its topic is among the 5 results the tool returns
(its honesty rules apply: `not_found` is a miss). Three kinds: `keyword` (the page's own words),
`wording` (same language, other words) and `cross` (another language than the only official page).
`rule` questions pass only when an excerpt states the answer itself (the CHF 5 parcel-VAT rule, Q12
of the end-to-end evaluation), not just when the right page appears.
`none` questions have no correct page in the index: they pass when the tool says so (not_found, or
local_match false when the question is about a place), rather than presenting a wrong page.

  uv run --extra semantic python scripts/search_eval.py            # both modes, side by side
  uv run python scripts/search_eval.py --mode keyword
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swiss_grounding_mcp import semantic  # noqa: E402
from swiss_grounding_mcp.config import settings  # noqa: E402
from swiss_grounding_mcp.sources.search import search_official_info  # noqa: E402

# acceptable = any official page on the topic, whoever publishes it (URL words in any national language)
VD_PERMIS = r"vd\.ch/.*(echanger-un-permis|permis.*etranger)"
SERAFE = (r"(bakom\.admin\.ch/.*(hoehe-der-abgabe|montant-de-la-redevance|importo-del-canone|radio-and-television-fee)"
          r"|serafe\.ch|ch\.ch/.*(serafe|radio-und-fernseh|radio-et-television|radio-e-televisione|radio-and-tv))")
LOST_PASS = r"(verloren|perdu|perte|smarrit|furto|lost|stolen|gestohlen|diebstahl|vol\b|-vol-)"
JOBLESS = r"(arbeitslos|chomage|disoccupaz|unemploy|arbeit\.swiss|stellenlos)"
NATURAL = r"(einburgerung|einbuergerung|naturalisation|naturalizzazione)"
MARRY = r"(heirat|mariage|se-marier|matrimonio|sposarsi|marriage|getting-married|trauung)"
TAX = r"(steuererklarung|steuererklaerung|declaration-d-impot|declaration-fiscale|dichiarazione-d|tax-return)"
MOVE_CUSTOMS = r"((zoll|bazg|douane|dogana|customs).*(umzug|demenag|trasloc|moving)|bazg\.admin\.ch/.*(umzug|demenag|trasloc|moving))"
BWO = r"bwo\.admin\.ch/.*(referenzzins|taux|tasso|reference|mietrecht|droit-du-bail|diritto-di-locazione|tenancy)"
VOTES = r"(abstimmungstermine|votations|votazioni|/abstimmungen/|dates-of-votes|blankette)"
# the parcel-VAT rule itself, in an excerpt: tax amounts up to CHF 5 are not collected
CHF5_RULE = r"(\b5 (Schweizer)?[Ff]ranken|\b5 Schweizerfranken|\b5 francs|\bCHF 5\b|\b5 franchi)"
# registering on arrival: the municipality's own page, or the federal ch.ch page (not the canton's pages
# for foreign nationals, which answer another question)
ARRIVAL = r"(ch\.ch/.*(annonce-de-depart-et-d-arrivee|ab-und-anmelden|notifica-di-partenza)|lausanne\.ch/.*(arriv|habitant))"
PARCEL = r"bazg\.admin\.ch/.*(empfangen-von-briefen-und-paketen|interneteinkauf|recevoir|ricevere|receiving)"

CASES = [  # (kind, question, place, acceptable URL pattern, or for `none`: what honest looks like)
    ("keyword", "Paket Deutschland Mehrwertsteuer 50 Franken", None, PARCEL),
    ("keyword", "permis de conduire étranger échanger", "Lausanne", VD_PERMIS),
    ("keyword", "Hund anmelden", "Basel", r"bs\.ch/.*hund-anmelden"),
    ("keyword", "Umzug in die Schweiz Zoll Hausrat", None, MOVE_CUSTOMS),
    ("keyword", "Referenzzinssatz Miete", None, BWO),
    ("keyword", "Abstimmungstermine", None, VOTES),
    ("keyword", "Einbürgerung Gesuch", None, NATURAL),
    # from the challenge's practice cases: the responsible authority's own calendar
    ("keyword", "Herbstferien 2026", "Bern", r"bern\.ch/themen/bildung/schule/schulferien"),
    ("keyword", "vacances scolaires 2026", "Genève", r"ge\.ch/vacances-scolaires"),
    ("wording", "Wie viel kostet die Radio- und Fernsehgebühr pro Jahr", None, SERAFE),
    ("wording", "Ich habe meine Stelle verloren, wo muss ich mich melden", None, JOBLESS),
    ("wording", "Je viens de perdre mon emploi, que dois-je faire", None, JOBLESS),
    ("wording", "Schweizer Staatsbürgerschaft beantragen", None, NATURAL),
    ("wording", "Wir wollen heiraten, welche Dokumente brauchen wir", None, MARRY),
    ("wording", "Mein Reisepass wurde gestohlen", None, LOST_PASS),
    ("wording", "Bis wann muss ich meine Steuern deklarieren", None, TAX),
    ("wording", "Muss ich Steuern zahlen wenn ich im Ausland etwas bestelle", None, PARCEL),
    # Lausanne's residents' office page is not indexed: the federal ch.ch page on registering is right
    ("wording", "Où dois-je annoncer mon arrivée dans la ville de Lausanne", "Lausanne", ARRIVAL),
    ("cross", "how much is the TV licence fee", None, SERAFE),
    ("cross", "I lost my passport, what should I do", None, LOST_PASS),
    ("cross", "become a Swiss citizen", None, NATURAL),
    ("cross", "exchange my foreign driving licence", "Lausanne", VD_PERMIS),
    ("cross", "ausländischen Führerausweis umtauschen", "Lausanne", VD_PERMIS),
    ("cross", "foreign driving licence exchange", "Lugano", r"ti\.ch/.*licenza-di-condurre-estera"),
    ("cross", "échanger permis de conduire étranger", "Zürich", r"zh\.ch/.*fuehrerausweis"),
    ("cross", "school holidays 2026", "Scuol", r"scoula-scuol\.ch"),
    ("cross", "Schulferien Herbst", "Scuol", r"scoula-scuol\.ch"),
    ("cross", "when is paper and cardboard collected", "Lausanne", r"lausanne\.ch/ramassage"),
    ("cross", "Karton Abfuhr Termine", "Lausanne", r"lausanne\.ch/ramassage"),
    ("cross", "register my dog", "Basel", r"bs\.ch/.*hund-anmelden"),
    ("cross", "mortgage reference interest rate for rents", None, BWO),
    ("cross", "dates of the next federal votes", None, VOTES),
    ("cross", "annoncer mon arrivée", "Bern", r"(bern\.ch/themen/zuzug|ch\.ch/.*(annonce-de-depart-et-d-arrivee|ab-und-anmelden))"),
    ("rule", "Paket Deutschland Mehrwertsteuer 50 Franken", None, CHF5_RULE),
    ("rule", "Muss ich Mehrwertsteuer zahlen wenn ich ein Paket aus Deutschland bestelle", None, CHF5_RULE),
    ("rule", "Mehrwertsteuer Paket Bestellung Deutschland Freigrenze", None, CHF5_RULE),
    ("rule", "TVA colis commandé en Allemagne", None, CHF5_RULE),
    ("rule", "VAT on a parcel ordered from Germany", None, CHF5_RULE),
    ("none", "Hund anmelden", "Winterthur", "no_local"),
    ("none", "Parkkarte Anwohner", "St. Gallen", "no_local"),
    ("none", "abbonamento annuale piscina comunale", "Lugano", "no_local"),
    ("none", "capital of Australia population", None, "not_found"),
    ("none", "best pizza restaurant", "Zürich", "no_local"),
]
KINDS = ["keyword", "wording", "cross", "rule", "none"]
ANSWERABLE = KINDS[:4]


def passed(result, expect: str, kind: str) -> bool:
    if kind == "rule":
        return result.status == "ok" and any(re.search(expect, c.excerpt or "") for c in result.citations)
    if kind != "none":
        return result.status == "ok" and any(re.search(expect, c.url) for c in result.citations)
    if result.status in ("not_found", "not_covered"):
        return True
    return expect == "no_local" and (result.data or {}).get("local_match") is False


async def run(mode: str) -> dict[str, list[bool]]:
    settings.semantic = "off" if mode == "keyword" else "auto"
    semantic.reset()
    if mode == "hybrid" and semantic.backend() is None:
        sys.exit(f"hybrid search unavailable: {semantic.status()}")
    scores: dict[str, list[bool]] = {k: [] for k in KINDS}
    hinted = 0
    for kind, question, place, expect in CASES:
        result = await search_official_info(question, place, None, 5)
        ok = passed(result, expect, kind)
        scores[kind].append(ok)
        hinted += (not ok and kind != "none" and bool((result.data or {}).get("place_languages")))
    HINTED[mode] = hinted
    return scores


HINTED: dict[str, int] = {}  # misses whose result tells the assistant which language to search in


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=["keyword", "hybrid", "both"], default="both")
    modes = ["keyword", "hybrid"] if ap.parse_args().mode == "both" else [ap.parse_args().mode]
    results = {mode: await run(mode) for mode in modes}
    print(f"\n{'question':<58}" + "".join(f"{m:>9}" for m in modes))
    for i, (kind, question, place, _) in enumerate(CASES):
        label = f"[{kind}] {question}" + (f" ({place})" if place else "")
        marks = []
        for m in modes:
            before = sum(len(results[m][k]) for k in KINDS[:KINDS.index(kind)])
            marks.append("ok" if results[m][kind][i - before] else "-")
        print(f"{label[:57]:<58}" + "".join(f"{x:>9}" for x in marks))
    print(f"\n{'mode':<10}" + "".join(f"{k:>10}" for k in KINDS) + f"{'answered':>11}")
    for m in modes:
        cells = [f"{sum(results[m][k])}/{len(results[m][k])}" for k in KINDS]
        answered = sum(sum(results[m][k]) for k in ANSWERABLE)
        print(f"{m:<10}" + "".join(f"{c:>10}" for c in cells) + f"{answered:>8}/{sum(len(results[m][k]) for k in ANSWERABLE)}")
    for m in modes:
        print(f"{m}: {HINTED.get(m, 0)} missed question(s) carry a language hint for a second search")


if __name__ == "__main__":
    asyncio.run(main())
