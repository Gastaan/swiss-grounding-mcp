"""Housing reference interest rate (BWO) and SNB exchange rates — both read live and cached."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import trafilatura

from .. import http
from ..models import Citation, ToolResult, needs, source_error, today

BWO_URL = {"de": "https://www.bwo.admin.ch/de/referenzzinssatz",
           "fr": "https://www.bwo.admin.ch/fr/taux-dinteret-de-reference",
           "it": "https://www.bwo.admin.ch/it/tasso-dinteresse-di-riferimento"}


async def reference_interest_rate(language: str = "de") -> ToolResult:
    url = BWO_URL["de"]  # the German page carries the wording we parse
    cite = Citation(title="Hypothekarischer Referenzzinssatz", url=BWO_URL.get(language, url),
                    publisher="Federal Office for Housing BWO", level="federal", jurisdiction="CH",
                    retrieved_at=today())
    try:
        html = await http.fetch(url, ttl=6 * http.HOUR)
    except http.FetchError as e:
        return source_error("The BWO page", e, [cite])
    text = trafilatura.extract(html, output_format="txt") or ""
    rate = re.search(r"Aktueller Referenzzinssatz:\s*([\d,\.]+)\s*%", text)
    since = re.search(r"gültig seit\s*(\d{2}\.\d{2}\.\d{4})", text)
    later = re.findall(r"\d{2}\.\d{2}\.\d{4}", text[since.end():]) if since else []
    upcoming = sorted(d for d in (date(int(x[6:]), int(x[3:5]), int(x[:2])) for x in later) if d > date.today())
    nxt = upcoming[0].strftime("%d.%m.%Y") if upcoming else None
    if not rate:
        return ToolResult(status="source_error", summary="The BWO page layout changed; the rate could not be read.",
                          citations=[cite], guidance="Give the user the link; do not answer from memory.")
    value = rate.group(1).replace(",", ".")
    excerpt = text[max(0, rate.start() - 10): rate.end() + 160].strip()
    return ToolResult(
        status="ok",
        summary=f"The mortgage reference interest rate for rents is {value}%"
        + (f", valid since {since.group(1)}" if since else "")
        + (f"; next publication {nxt}." if nxt else "."),
        data={"rate_percent": float(value), "valid_since": since.group(1) if since else None,
              "next_publication": nxt},
        citations=[cite.model_copy(update={"excerpt": excerpt})],
        guidance="The reference rate is the basis for rent adjustments (VMWG art. 12a). Cite the BWO page.",
    )


async def exchange_rate(currency: str | None) -> ToolResult:
    if not currency:
        return needs("currency", "Which currency (e.g. EUR, USD, GBP)?")
    cur = currency.strip().upper()[:3]
    since = (date.today().replace(day=1) - timedelta(days=62)).strftime("%Y-%m")
    cite = Citation(title="SNB — foreign exchange rates, monthly averages and month-end (cube devkum)",
                    url="https://data.snb.ch/en/topics/ziredev/cube/devkum", publisher="Swiss National Bank",
                    level="federal", jurisdiction="CH", retrieved_at=today())
    try:
        body = await http.fetch("https://data.snb.ch/api/cube/devkum/data/csv/en", params={"fromDate": since},
                                ttl=6 * http.HOUR, check_robots=False)
    except http.FetchError as e:
        return source_error("The SNB data portal", e, [cite])
    lines = body.lstrip("﻿").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith('"Date"')), None)
    if start is None:
        return source_error("The SNB data portal", "unexpected format", [cite])
    rows = [r for r in csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=";")
            if r["D1"].startswith(cur) and r["Value"]]
    if not rows:
        return ToolResult(status="not_found", summary=f"The SNB publishes no rate for '{cur}'.", citations=[cite])
    latest = max(r["Date"] for r in rows)
    pick = {r["D0"]: r for r in rows if r["Date"] == latest}
    avg, end = pick.get("M0"), pick.get("M1")
    unit = int(re.sub(r"\D", "", (avg or end)["D1"]) or 1)
    summary = f"SNB {latest}: {unit} {cur} = CHF {float(avg['Value']):.5g} (monthly average)" if avg else f"SNB {latest}"
    if end:
        summary += f", CHF {float(end['Value']):.5g} at month-end"
    return ToolResult(
        status="ok", summary=summary + ".",
        data={"currency": cur, "unit": unit, "month": latest,
              "monthly_average_chf": float(avg["Value"]) if avg else None,
              "month_end_chf": float(end["Value"]) if end else None},
        citations=[cite.model_copy(update={"valid_for": latest})],
        guidance="These are official SNB reference rates, not bank or card rates.",
    )
