"""Shared helpers for source modules."""

from __future__ import annotations

from ..models import ToolResult, needs
from ..places import Resolution, canton_name


def place_problem(res: Resolution, what: str = "the municipality") -> ToolResult | None:
    """Return the right answer when a tool needs a municipality but did not get exactly one."""
    if res.kind == "municipality":
        return None
    if res.kind == "empty":
        return needs("municipality", f"Which municipality (or postcode) is meant? This depends on {what}.")
    if res.kind == "ambiguous":
        options = [m.label for m in res.candidates[:10]]
        prefix = f"Postcode {res.postcode} covers several municipalities" if res.postcode else "Several municipalities match"
        return needs("municipality", f"{prefix}: {', '.join(options)}. Which one is meant?", options=options)
    if res.kind == "foreign":
        return ToolResult(
            status="not_covered",
            summary=f"This place is in {res.country}, not in Switzerland. Swiss rules and sources do not apply "
            "there, and this server only covers Switzerland.",
            guidance="Tell the user clearly that the place is outside Switzerland and that you cannot answer "
            "from Swiss official sources. Do not answer from memory about the foreign country.",
        )
    if res.kind in ("canton", "national"):
        where = f"in canton {canton_name(res.canton)}" if res.canton else "in Switzerland"
        return needs("municipality", f"Which municipality {where} is meant? This depends on {what}.")
    return ToolResult(
        status="not_found",
        summary="No Swiss municipality, postcode or address matches this place name.",
        guidance="Ask the user to check the spelling or give the postcode. If the place is outside "
        "Switzerland, say that this server only covers Swiss official information.",
    )
