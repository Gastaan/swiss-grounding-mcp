"""Live tests against the real sources. Run with: uv run pytest -m live"""

import pytest

from .conftest import call

pytestmark = pytest.mark.live


async def test_law_article(client):
    r = await call(client, "swiss_federal_law", sr_number="OR", article="335c")
    assert r["status"] == "ok" and "Kündigungsfrist" in r["data"]["text"]
    assert r["citations"][0]["url"].endswith("#art_335_c")


async def test_law_in_french(client):
    r = await call(client, "swiss_federal_law", sr_number="VZV", article="42", language="fr")
    assert r["status"] == "ok" and "douze mois" in r["data"]["text"]


async def test_sample_question_4_scuol_autumn_holidays(client):
    r = await call(client, "swiss_holidays", place="Scuol", year=2026)
    autumn = [h for h in r["data"]["holidays"] if h["start"] == "2026-10-10"]
    assert autumn and autumn[0]["end"] == "2026-10-25"


async def test_waste_zurich_by_postcode(client):
    r = await call(client, "waste_collection", place="8003", waste_type="Karton")
    assert r["status"] == "ok" and r["data"]["collections"][0]["type"] == "cardboard"


async def test_waste_uncovered_municipality_is_honest(client):
    r = await call(client, "waste_collection", place="Lausanne", waste_type="carton")
    assert r["status"] == "not_covered" and r["citations"][0]["url"]


async def test_transport(client):
    r = await call(client, "public_transport", origin="Zürich HB", destination="Bellinzona")
    assert r["status"] == "ok" and r["data"]["connections"]


async def test_votes(client):
    r = await call(client, "federal_votes")
    assert r["status"] == "ok" and r["data"]["proposals"]


async def test_reference_rate(client):
    r = await call(client, "swiss_rates", kind="reference_interest_rate")
    assert r["status"] == "ok" and 0 < r["data"]["rate_percent"] < 10


async def test_exchange_rate(client):
    r = await call(client, "swiss_rates", kind="exchange_rate", currency="EUR")
    assert r["status"] == "ok" and 0.5 < r["data"]["monthly_average_chf"] < 2


async def test_company(client):
    r = await call(client, "company_register", name_or_uid="CHE-102.753.938")
    assert r["data"]["companies"][0]["name"] == "Swisscom AG"


async def test_weather(client):
    r = await call(client, "current_weather", place="Zermatt")
    assert r["status"] == "ok" and r["data"]["station"] == "Zermatt"


async def test_search_vaud_licence_exchange(client):
    """Sample question 2: foreign driving licence in Vaud (12 months)."""
    r = await call(client, "search_official_info", query="échanger permis de conduire étranger", place="Vaud",
                   language="fr")
    assert r["status"] == "ok"
    assert all(c["jurisdiction"] == "CH" or c["jurisdiction"].startswith("CH-VD") for c in r["citations"])
    assert any("12" in (c.get("excerpt") or "") for c in r["citations"])


async def test_read_official_page(client):
    r = await call(client, "read_official_page",
                   url="https://www.vd.ch/mobilite/automobile-et-navigation/permis/echanger-un-permis-etranger",
                   focus="délai 12 mois")
    assert r["status"] == "ok" and "12" in r["data"]["text"]


@pytest.mark.parametrize(("tool", "args"), [
    ("search_official_info", {"query": "Karton Abfuhr Stadt Zürich"}),
    ("health_insurance_premiums", {"place": "Zürich", "age": 35}),
    ("swiss_federal_law", {"sr_number": "220", "query": "Kündigungsfrist"}),
])
async def test_responses_stay_small(client, tool, args):
    r = await client.call_tool(tool, args)
    assert len(r.content[0].text) < 8000
