"""Build src/swiss_grounding_mcp/data/embeddings.npz — vectors of every index passage for hybrid search.

Uses the local model in swiss_grounding_mcp.semantic (no API key; downloaded once, ~220 MB). Vectors are
stored as int8 (18 MB instead of 72 MB; rankings stay within ~97% of float32) together with the passage
rowids and the build date of the index they belong to, so the server never mixes them with another index.
Run after build_index.py (about 15 minutes on a laptop CPU):

  uv run --extra semantic --group build python scripts/build_embeddings.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swiss_grounding_mcp import semantic  # noqa: E402
from swiss_grounding_mcp.config import settings  # noqa: E402
from swiss_grounding_mcp.sources import search  # noqa: E402


def save(vectors: np.ndarray, rowids: np.ndarray, index_built: str, path: Path = semantic.EMBEDDINGS) -> None:
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    meta = {"model": semantic.MODEL, "index_built": index_built, "passages": int(len(rowids)),
            "dim": int(vectors.shape[1]), "built": date.today().isoformat(), "dtype": "int8 (value * 127)"}
    np.savez_compressed(path, vectors=np.round(vectors * 127).astype(np.int8), rowids=rowids.astype(np.int64),
                        meta=np.array(json.dumps(meta)))
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB): {meta}")


def main() -> None:
    with sqlite3.connect(f"file:{search._db_path()}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT rowid, title, heading, body FROM passages ORDER BY rowid").fetchall()
    index_built = search.index_meta()["built"]
    print(f"{len(rows)} passages of index {index_built}; model {semantic.MODEL}", flush=True)
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(semantic.MODEL, cache_dir=str(settings.model_dir))
    started, vectors = time.time(), []
    for i, v in enumerate(model.passage_embed([semantic.passage_text(t, h, b) for _, t, h, b in rows],
                                              batch_size=64)):
        vectors.append(v)
        if i and i % 5000 == 0:
            print(f"  {i} passages, {time.time() - started:.0f} s", flush=True)
    save(np.asarray(vectors, dtype=np.float32), np.array([r[0] for r in rows]), index_built)


if __name__ == "__main__":
    main()
