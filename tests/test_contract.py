"""Offline tests: MCP contract, place resolution, local data, honesty rules. No network needed."""

import json

import pytest

from swiss_grounding_mcp import models
from swiss_grounding_mcp.authorities import classify
from swiss_grounding_mcp.places import resolve
from swiss_grounding_mcp.server import OUTPUT_SCHEMA

from .conftest import call

EXPECTED_TOOLS = {
    "swiss_coverage", "swiss_place_info", "search_official_info", "read_official_page", "swiss_federal_law",
    "health_insurance_premiums", "swiss_holidays", "waste_collection", "public_transport", "federal_votes",
    "swiss_rates", "company_register", "current_weather",
}


async def test_tool_list_is_compact_and_read_only(client):
    tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.title and t.description
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert t.output_schema["required"] == ["status", "summary"]
    size = sum(len(json.dumps(t.model_dump(exclude_none=True, by_alias=True))) for t in tools)
    assert size < 26_000, f"tools/list grew to {size} chars"


def test_output_schema_matches_model():
    model = models.ToolResult.model_json_schema()
    assert set(OUTPUT_SCHEMA["properties"]) == set(model["properties"])
    assert set(OUTPUT_SCHEMA["properties"]["citations"]["items"]["properties"]) == set(
        model["$defs"]["Citation"]["properties"])


@pytest.mark.parametrize(
    ("query", "kind", "expected"),
    [
        ("Scuol", "municipality", 3762), ("Schuls", "municipality", 3762), ("Lugano", "municipality", 5192),
        ("Genf", "municipality", 6621), ("Genève", "municipality", 6621), ("8003", "municipality", 261),
        ("Buchs SG", "municipality", 3271), ("Bienne", "municipality", 371), ("Coira", "municipality", 3901),
        ("Buchs", "ambiguous", None), ("Konstanz", "foreign", None), ("Kanton Zürich", "canton", None),
        ("Vaud", "canton", None), ("GR", "canton", None), ("", "empty", None),
        ("Zurich 8003", "municipality", 261), ("8003 Zürich", "municipality", 261),
        ("Lugano 1234", "municipality", 5192), ("3920 Zermatt", "municipality", 6300), ("9999", "not_found", None),
    ],
)
async def test_resolve(query, kind, expected):
    r = await resolve(query)
    assert r.kind == kind
    if expected:
        assert r.municipality.bfs == expected


async def test_sample_question_3_premium_lugano(client):
    """Qual è il premio mensile più basso … 30 anni … Lugano … franchigia 2500?"""
    r = await call(client, "health_insurance_premiums", place="Lugano", age=30, franchise=2500)
    assert r["status"] == "ok"
    best = r["data"]["cheapest"][0]
    assert best["monthly_premium_chf"] == 449.90 and best["insurer"] == "Agrisano"
    assert any("priminfo.admin.ch" in c["url"] for c in r["citations"])


async def test_premium_needs_municipality_in_multi_region_canton(client):
    r = await call(client, "health_insurance_premiums", place="Ticino", age=30, franchise=2500)
    assert r["status"] == "needs_context" and r["missing_context"][0]["field"] == "municipality"


async def test_premium_single_region_canton_needs_no_municipality(client):
    r = await call(client, "health_insurance_premiums", place="Genève", age=40)
    assert r["status"] == "ok"


@pytest.mark.parametrize("tool", ["waste_collection", "swiss_holidays", "health_insurance_premiums"])
async def test_foreign_place_is_not_covered(client, tool):
    """Sample question 5 (Konstanz is in Germany) must be refused, not answered."""
    args = {"place": "Konstanz"} | ({"age": 30} if tool == "health_insurance_premiums" else {})
    r = await call(client, tool, **args)
    assert r["status"] == "not_covered" and "Germany" in r["summary"]


async def test_sample_question_1_asks_for_municipality(client):
    """Wann wird bei uns das nächste Mal Karton abgeholt? -> ask which municipality."""
    r = await call(client, "waste_collection", waste_type="Karton")
    assert r["status"] == "needs_context" and r["missing_context"][0]["field"] == "municipality"


async def test_ambiguous_place_offers_options(client):
    r = await call(client, "swiss_place_info", place="Buchs")
    assert r["status"] == "needs_context"
    assert r["missing_context"][0]["options"] == ["Buchs (ZH)", "Buchs (SG)", "Buchs (AG)"]


async def test_place_info(client):
    r = await call(client, "swiss_place_info", place="Scuol")
    assert r["data"]["bfs_number"] == 3762 and r["data"]["population"] > 4000
    assert r["data"]["canton"] == "GR"


async def test_coverage_declares_scope(client):
    r = await call(client, "swiss_coverage")
    assert len(r["data"]["covered"]) >= 10 and r["data"]["not_covered"]


async def test_non_official_url_is_refused(client):
    r = await call(client, "read_official_page", url="https://www.example.com/")
    assert r["status"] == "not_covered"


async def test_invalid_arguments_are_tool_errors(client):
    r = await client.call_tool("health_insurance_premiums", {"place": "Lugano", "age": -1}, raise_on_error=False)
    assert r.is_error


def test_authority_classification():
    assert classify("https://www.vd.ch/x").jurisdiction == "CH-VD"
    assert classify("https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de").level == "federal"
    assert classify("https://www.lugano.ch/").level == "municipal"
    assert classify("https://www.ahv-iv.ch/de/").level == "semi-official"
    assert classify("https://www.bund.de/") is None
    assert classify("https://www.comparis.ch/") is None


def test_robots_setting_is_configurable(monkeypatch):
    import importlib

    from swiss_grounding_mcp import config

    monkeypatch.setenv("SGM_RESPECT_ROBOTS", "false")
    assert importlib.reload(config).settings.respect_robots is False
    monkeypatch.delenv("SGM_RESPECT_ROBOTS")
    assert importlib.reload(config).settings.respect_robots is True
