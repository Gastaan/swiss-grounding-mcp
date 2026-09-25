"""The server's declared scope (kept in sync with the README 'Coverage' section)."""

from __future__ import annotations

from .models import ToolResult
from .places import register

COVERAGE = [
    {"topic": "Place facts: municipality, canton, postcodes, population, official website",
     "geography": "all 2,110 Swiss municipalities and 26 cantons", "source": "BFS, swisstopo",
     "tool": "swiss_place_info"},
    {"topic": "Procedures, rules, fees, deadlines (permits, moving, taxes, social insurance, driving licences, "
              "customs, schools, housing, voting, unemployment…)",
     "geography": "federal (ch.ch in de/fr/it/rm/en, federal offices) + 26 cantonal portals + 12 largest cities",
     "source": "prebuilt index of official pages (keyword, or hybrid with the semantic extra) + live page reading",
     "tool": "search_official_info, read_official_page"},
    {"topic": "Federal law (current consolidated text, any article)", "geography": "federal",
     "source": "Fedlex", "tool": "swiss_federal_law"},
    {"topic": "Mandatory health insurance premiums", "geography": "all municipalities (premium regions)",
     "source": "FOPH premium open data (priminfo)", "tool": "health_insurance_premiums"},
    {"topic": "School holidays and public holidays", "geography": "all cantons; municipality level where published",
     "source": "OpenHolidays (aggregated official lists) + EDK reference", "tool": "swiss_holidays"},
    {"topic": "Waste collection dates",
     "geography": "Zürich (by postcode), Basel/Riehen/Bettingen (by address), St. Gallen (by street), and 20 more "
                  "municipalities (by zone); others: official website only",
     "source": "municipal open data", "tool": "waste_collection"},
    {"topic": "Public transport connections and departures", "geography": "all of Switzerland",
     "source": "official timetable (transport.opendata.ch)", "tool": "public_transport"},
    {"topic": "Federal popular votes: next subjects and results", "geography": "federal (+ canton results)",
     "source": "FSO vote-day data, Federal Chancellery", "tool": "federal_votes"},
    {"topic": "Mortgage reference interest rate; SNB exchange rates", "geography": "federal",
     "source": "BWO, SNB", "tool": "swiss_rates"},
    {"topic": "Company registration (UID, seat, commercial register, VAT)", "geography": "all of Switzerland",
     "source": "federal UID register", "tool": "company_register"},
    {"topic": "Current weather measurements", "geography": "nearest MeteoSwiss automatic station",
     "source": "MeteoSwiss open data", "tool": "current_weather"},
]

NOT_COVERED = [
    "Anything outside Switzerland (neighbouring countries' rules, e.g. German Rundfunkbeitrag in Konstanz)",
    "Cantonal law texts (only cantonal web pages, not the cantonal law collections)",
    "Individual tax calculations (point to the official cantonal tax calculators)",
    "Weather forecasts",
    "Waste calendars of municipalities without open data (the official website is returned instead)",
    "Personal or confidential data of any kind",
]


def coverage_report() -> ToolResult:
    meta = register()["meta"]
    return ToolResult(
        status="ok",
        summary="Swiss public information from official sources; see data.covered and data.not_covered.",
        data={"covered": COVERAGE, "not_covered": NOT_COVERED, "places_built": meta["built"],
              "population_year": meta["population_year"]},
        guidance="If the question falls under not_covered, tell the user it is not covered.",
    )
