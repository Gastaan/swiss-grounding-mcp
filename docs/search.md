# Search

[← Back to the README](../README.md)

`search_official_info` ranks passages of the index with SQLite FTS5/BM25 plus rules (the user's
language, the most specific jurisdiction, how many query words a passage covers). With the optional
`semantic` extra it becomes **hybrid**: a local multilingual embedding model
(`paraphrase-multilingual-MiniLM-L12-v2`, ONNX, no API key) ranks the same passages by meaning, and the
two rankings are merged (reciprocal-rank fusion, with the same preference for cantonal and municipal
pages when a place is given). The two best keyword hits keep their places, so exact matches are never
pushed out: with only one, passages about a neighbouring rule (the travellers' CHF 150 allowance) took
the place of the CHF 5 parcel rule.

```sh
uv sync --extra semantic      # then start the server as usual; /health shows "search": "hybrid (…)"
```

## Measured quality

Measured with `scripts/search_eval.py`: 38 hand-labelled questions whose answer is in the index (including
the challenge's practice cases), plus 5 without one. A question counts as answered when an official page on its topic is among the 5 results
the tool returns; for the "rule" questions the excerpt itself must state the rule (the CHF 5 parcel-VAT
rule behind end-to-end question Q12).

| Question kind | Keyword only | Hybrid |
|---|---|---|
| Uses the page's own words (9) | 9 | 9 |
| Same language, other words (9) | 5 | 6 |
| Another language than the only official page (15) | 2 | 8 |
| The rule itself in an excerpt (5 phrasings of Q12) | 3 | 2 |
| **Answered (38)** | **19** | **25** |
| No correct page exists: says so instead of passing off another page (5) | 5 | 5 |

For example, "exchange my foreign driving licence" in Lausanne now finds Vaud's French-only page, and
"register my dog" in Basel finds the German one. Still missed: the Romansh school calendar of Scuol,
German or English questions about Lausanne's French waste calendar, and parcel-VAT questions worded
with terms the official pages do not use ("Freigrenze", French "colis").

**Honesty is kept:** a result only counts as evidence if it covers at least half of the query's words
or is close in meaning (cosine ≥ 0.7). With no such result the tool answers `not_found`, as before.
The threshold was set so that none of the questions without a correct page gets through by similarity
alone.

## Costs

~240 MB for the model (downloaded once into `SGM_MODEL_DIR`, or at image build time in
Docker) and ~130 MB of Python packages (`fastembed`, `onnxruntime`, `numpy` and their dependencies); `data/embeddings.npz` adds 12 MB
(int8 vectors); about 20 ms per search; the weekly refresh re-embeds all passages (~15 min on a laptop,
longer on CI runners). Without the extra, or with `SGM_SEMANTIC=off`, nothing of this is loaded.
The server only uses embeddings built from the exact index it serves, and falls back to keyword search
if the vector part fails.
