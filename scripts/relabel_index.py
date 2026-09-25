"""Re-apply authorities.classify() to the pages of the shipped index and rewrite index.sqlite.gz.

Use after changing authorities.py (a full build_index.py run does this anyway). Only the publisher,
level and jurisdiction of pages whose level or jurisdiction changes are rewritten; page texts and the
index build date stay as they are, so data/embeddings.npz remains valid.

Run: uv run python scripts/relabel_index.py
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swiss_grounding_mcp.authorities import classify  # noqa: E402
from swiss_grounding_mcp.config import DATA_DIR  # noqa: E402


def main() -> None:
    packed = DATA_DIR / "index.sqlite.gz"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "index.sqlite"
        with gzip.open(packed, "rb") as src, open(path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        db = sqlite3.connect(path)
        changes: dict[tuple[str, str, str], int] = {}
        for page_id, url, level, jurisdiction in db.execute("SELECT id, url, level, jurisdiction FROM pages").fetchall():
            authority = classify(url)
            if authority and (authority.level, authority.jurisdiction) != (level, jurisdiction):
                db.execute("UPDATE pages SET publisher = ?, level = ?, jurisdiction = ? WHERE id = ?",
                           (authority.publisher, authority.level, authority.jurisdiction, page_id))
                key = (url.split("/")[2], f"{level} {jurisdiction}", f"{authority.level} {authority.jurisdiction}")
                changes[key] = changes.get(key, 0) + 1
        db.commit()
        db.execute("VACUUM")
        db.close()
        for (host, before, after), n in sorted(changes.items()):
            print(f"{n:>5} pages of {host}: {before} -> {after}")
        if not changes:
            print("nothing to relabel")
            return
        with open(path, "rb") as src, gzip.open(packed, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
    unpacked = DATA_DIR / "index.sqlite"
    unpacked.unlink(missing_ok=True)  # unpacked again from the new archive on the next start
    print(f"wrote {packed} ({packed.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
