"""Hybrid search. The merge and honesty rules are tested with a fake vector index (always runs); the real
model runs only when the `semantic` extra and data/embeddings.npz are available."""

import sqlite3

import pytest

from swiss_grounding_mcp import semantic
from swiss_grounding_mcp.sources import search

from .conftest import call


def _rowid(url_part: str) -> int:
    with sqlite3.connect(f"file:{search._db_path()}?mode=ro", uri=True) as db:
        return db.execute("SELECT passages.rowid FROM passages JOIN pages p ON p.id = passages.page_id "
                          "WHERE p.url LIKE ? LIMIT 1", (f"%{url_part}%",)).fetchone()[0]


class FakeVectors:
    def __init__(self, results=None, error=None):
        self.results, self.error = results or [], error

    def search(self, query, jurisdictions, n):
        if self.error:
            raise self.error
        return self.results


@pytest.fixture
def vectors(monkeypatch):
    def use(fake):
        monkeypatch.setattr(semantic, "backend", lambda wait=True: fake)
    return use


async def test_page_found_only_by_meaning_joins_the_results(client, vectors):
    """An English question about a French-only Vaud page: keywords cannot match, similarity can."""
    vd = _rowid("vd.ch/prestation/echanger-un-permis-de-conduire-etranger")
    vectors(FakeVectors([(vd, 0.78)]))
    r = await call(client, "search_official_info", query="exchange my foreign driving licence", place="Lausanne")
    assert r["status"] == "ok" and r["data"]["search"] == "hybrid"
    assert any("vd.ch/prestation/echanger-un-permis" in c["url"] for c in r["citations"])
    assert r["data"]["local_match"] is True


async def test_weak_similarity_is_not_evidence(client, vectors):
    """A loosely similar passage must not turn 'nothing found' into an answer."""
    vectors(FakeVectors([(_rowid("bakom.admin.ch/de/abstimmungen"), semantic.SUPPORT - 0.05)]))
    r = await call(client, "search_official_info", query="Quokkafütterung Wombatrennen Kakaduschule")
    assert r["status"] == "not_found"


async def test_vector_failure_falls_back_to_keywords(client, vectors):
    vectors(FakeVectors(error=RuntimeError("onnx session died")))
    r = await call(client, "search_official_info", query="permis de conduire étranger échanger", place="Lausanne")
    assert r["status"] == "ok" and r["data"]["search"] == "keyword"


async def test_keyword_only_when_switched_off(client, monkeypatch):
    monkeypatch.setattr(semantic.settings, "semantic", "off")
    semantic.reset()
    try:
        assert semantic.backend() is None and "SGM_SEMANTIC=off" in semantic.status()
        r = await call(client, "search_official_info", query="Hund anmelden", place="Basel")
        assert r["data"]["search"] == "keyword"
    finally:
        monkeypatch.undo()
        semantic.reset()


def test_embeddings_belong_to_the_shipped_index():
    np = pytest.importorskip("numpy")
    if not semantic.EMBEDDINGS.exists():
        pytest.skip("no embeddings file")
    import json

    data = np.load(semantic.EMBEDDINGS)
    meta = json.loads(str(data["meta"]))
    assert meta["model"] == semantic.MODEL
    assert str(meta["index_built"]) == str(search.index_meta()["built"])
    assert meta["passages"] == len(data["rowids"]) == data["vectors"].shape[0]


# ---------------------------------------------------------------- the real model (optional extra)

real = pytest.mark.skipif(semantic.backend() is None, reason="semantic extra or embeddings not available")


@real
@pytest.mark.parametrize(("query", "place", "expected"), [
    ("exchange my foreign driving licence", "Lausanne", "vd.ch/prestation/echanger-un-permis"),
    ("register my dog", "Basel", "bs.ch/themen/umwelt-und-bauen/tiere-und-pflanzen/hunde/hund-anmelden"),
])
async def test_real_model_finds_pages_in_another_language(client, query, place, expected):
    r = await call(client, "search_official_info", query=query, place=place)
    assert r["data"]["search"] == "hybrid"
    assert any(expected in c["url"] for c in r["citations"]), [c["url"] for c in r["citations"]]


@real
async def test_real_model_keeps_no_local_page_honest(client):
    r = await call(client, "search_official_info", query="Hund anmelden", place="Winterthur")
    assert r["data"]["local_match"] is False


def test_search_does_not_wait_for_a_model_still_loading(monkeypatch):
    """While the model loads in the background (first start downloads it), searches use keywords."""
    semantic.reset()
    assert semantic._lock.acquire()  # another thread is loading
    try:
        assert semantic.backend(wait=False) is None
    finally:
        semantic._lock.release()
        semantic.reset()
