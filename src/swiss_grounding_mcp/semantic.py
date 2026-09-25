"""Optional vector search over the index passages, merged with keyword search in sources/search.py.

Install with the `semantic` extra (`uv sync --extra semantic`). It needs data/embeddings.npz (built by
scripts/build_embeddings.py from the same index) and a small multilingual model (~220 MB, downloaded
once into SGM_MODEL_DIR). When any of that is missing, or SGM_SEMANTIC=off, search stays keyword-only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

from .config import DATA_DIR, settings

log = logging.getLogger("swiss_grounding_mcp")

# Local ONNX model (no API key): ~50 languages incl. de/fr/it/en; Romansh is not among them.
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDINGS = DATA_DIR / "embeddings.npz"
# Vector candidates below INCLUDE are not merged at all. A result counts as evidence for the answer if it
# matches at least half of the query's keywords or reaches SUPPORT. Both were calibrated on
# scripts/search_eval.py: at 0.7 no question without a correct page in the index got through.
INCLUDE = 0.6
SUPPORT = 0.7
# Merging: weight of the vector ranking against the keyword ranking (1.0); the bonus for pages of the
# canton or municipality asked about (in reciprocal-rank units: 1/61 is a first place); and how many of
# the best keyword hits keep their place whatever the fusion says (two keep the exact rule passage of
# a question, e.g. the CHF 5 parcel-VAT rule, ahead of passages about a neighbouring rule). Chosen on
# scripts/search_eval.py (weight 0.5/0.7/1.0, bonus 0-0.015, pinned 0-2, coverage weighting, a
# minimum similarity for meaning-only passages): 23/34 answered vs 15/34 keyword-only, no worse in any
# question kind, same result on questions without a correct page. Re-check there after changes.
VECTOR_WEIGHT = 1.0
LOCAL_BONUS = 0.01
KEYWORD_PINNED = 2


def passage_text(title: str, heading: str | None, body: str) -> str:
    """The text that is embedded for a passage (the build script and any rebuild must use this)."""
    return (f"{title} — {heading}\n{body}" if heading else f"{title}\n{body}")[:1200]


class Semantic:
    def __init__(self, vectors, rowids, jurisdictions, model) -> None:
        import numpy as np

        self.np = np
        self.vectors = vectors  # float32, rows normalised
        self.rowids = rowids
        self.jurisdictions = jurisdictions
        self.model = model

    def search(self, query: str, jurisdictions: list[str] | None, n: int) -> list[tuple[int, float]]:
        """Passage rowids most similar to the query (at least INCLUDE), restricted to the jurisdictions."""
        np = self.np
        q = next(iter(self.model.query_embed([query])))
        q = np.asarray(q, dtype=np.float32)
        q /= np.linalg.norm(q)
        sims = self.vectors @ q
        if jurisdictions:
            sims = np.where(np.isin(self.jurisdictions, jurisdictions), sims, -1.0)
        n = min(n, len(sims) - 1)
        top = np.argpartition(-sims, n)[:n]
        top = top[np.argsort(-sims[top])]
        return [(int(self.rowids[i]), float(sims[i])) for i in top if sims[i] >= INCLUDE]


_backend: Semantic | None = None
_state = "not loaded"
_done = False
_lock = threading.Lock()


def status() -> str:
    return _state


def backend(wait: bool = True) -> Semantic | None:
    """The loaded vector index, or None (keyword-only search). Loads once; never raises.
    With wait=False it returns None instead of waiting while another thread is still loading, so a
    search during startup uses keywords rather than stalling (the first start may download the model)."""
    global _backend, _state, _done
    if _done:
        return _backend
    if not _lock.acquire(blocking=wait):
        return None
    try:
        if not _done:
            _state = "loading"
            _backend, _state = _load()
            _done = True
            log.info("search: %s", _state)
        return _backend
    finally:
        _lock.release()


def load_in_background() -> None:
    """Start loading at server start without delaying the MCP handshake."""
    threading.Thread(target=backend, name="semantic-load", daemon=True).start()


def _load() -> tuple[Semantic | None, str]:
    if settings.semantic == "off":
        return None, "keyword only (SGM_SEMANTIC=off)"
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError:
        return None, "keyword only (install the 'semantic' extra for hybrid search)"
    if not EMBEDDINGS.exists():
        return None, "keyword only (data/embeddings.npz missing; run scripts/build_embeddings.py)"
    from .sources import search  # late import: search imports this module

    try:
        data = np.load(EMBEDDINGS)
        meta = json.loads(str(data["meta"]))
        index = search.index_meta()
        if meta.get("model") != MODEL or str(meta.get("index_built")) != str(index.get("built")):
            return None, (f"keyword only (embeddings are for index {meta.get('index_built')}, "
                          f"the index is {index.get('built')}; rebuild them)")
        rowids = data["rowids"]
        with sqlite3.connect(f"file:{search._db_path()}?mode=ro", uri=True) as db:
            jur = dict(db.execute("SELECT passages.rowid, p.jurisdiction FROM passages "
                                  "JOIN pages p ON p.id = passages.page_id"))
        if len(jur) != len(rowids) or any(int(r) not in jur for r in rowids[:100]):
            return None, "keyword only (embeddings do not match the index passages; rebuild them)"
        vectors = data["vectors"].astype(np.float32) / 127.0  # stored as int8
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        jurisdictions = np.array([jur[int(r)] for r in rowids])
        settings.model_dir.mkdir(parents=True, exist_ok=True)
        model = TextEmbedding(MODEL, cache_dir=str(settings.model_dir),
                              **({"local_files_only": True} if settings.offline else {}))
    except Exception as e:  # a broken optional feature must not stop the server
        log.warning("semantic search disabled: %s: %s", type(e).__name__, e)
        return None, f"keyword only (semantic search failed to load: {type(e).__name__})"
    return Semantic(vectors, rowids, jurisdictions, model), f"hybrid (keyword + {MODEL.split('/')[-1]})"


def reset() -> None:
    """Forget the loaded backend (tests, or after rebuilding the embeddings)."""
    global _backend, _state, _done
    with _lock:
        _backend, _state, _done = None, "not loaded", False
