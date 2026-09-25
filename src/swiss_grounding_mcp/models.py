"""The one response envelope every tool returns."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "needs_context", "not_covered", "not_found", "source_error"]
Level = Literal["federal", "cantonal", "municipal", "semi-official", "community"]


class Citation(BaseModel):
    title: str
    url: str
    publisher: str
    level: Level
    jurisdiction: str = Field(description="'CH', 'CH-VD' or 'CH-VD-5586' (BFS municipality number).")
    retrieved_at: str | None = Field(default=None, description="ISO date the data was fetched.")
    valid_for: str | None = Field(default=None, description="Reference period, e.g. '2026'.")
    excerpt: str | None = Field(default=None, description="Verbatim text from the source.")


class MissingContext(BaseModel):
    field: str
    question: str
    options: list[str] | None = None


class ToolResult(BaseModel):
    status: Status
    summary: str = Field(description="Short factual statement backed by the citations.")
    data: dict[str, Any] | None = None
    citations: list[Citation] = []
    missing_context: list[MissingContext] = []
    guidance: str | None = Field(default=None, description="What the assistant should do next.")


def today() -> str:
    return date.today().isoformat()


def needs(field: str, question: str, options: list[str] | None = None, **kw: Any) -> ToolResult:
    return ToolResult(
        status="needs_context",
        summary=question,
        missing_context=[MissingContext(field=field, question=question, options=options)],
        guidance="Ask the user exactly this question, then call the tool again. Do not guess.",
        **kw,
    )


def source_error(what: str, err: Exception | str, citations: list[Citation] | None = None) -> ToolResult:
    return ToolResult(
        status="source_error",
        summary=f"{what} could not be retrieved right now ({err}).",
        citations=citations or [],
        guidance="Tell the user the official source is temporarily unreachable and give the "
        "citation URL so they can check it themselves. Do not answer from memory.",
    )
