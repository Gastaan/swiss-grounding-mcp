# Evaluation

[← Back to the README](../README.md)

## Challenge self-check

The organisers' [practice cases](https://github.com/Swiss-ai-Weeks/swisscom-2026/tree/main/swiss-grounding-mcp#submission-self-check-pack)
(read from the published file; their launcher was not run), checked against this server:

| Practice case | Behaviour |
|---|---|
| Cardboard collection, no place given | asks only for the municipality (`needs_context`), never a date |
| Geneva school holidays 2026 | cites `ge.ch/vacances-scolaires-2026-2027` first, no ask-back; periods the official page confirms are marked `on_official_page` |
| Licence fee in Konstanz | "Konstanz is in Germany, Swiss sources do not apply" (`not_covered`) |
| Romansh: autumn holidays in Scuol | 10–25 Oct 2026, citing the Scuol school's own 2026/27 plan |
| Current reference interest rate | BWO page, 1.25 % with its effective date, cached at most 6 h |
| Registering on arrival in Lausanne, then Bern | Bern: the French question gets a hint that Bern publishes in German; searching again in German finds the city's own page (`bern.ch/themen/zuzug-umzug-wegzug`). Lausanne: the city's residents' office page is not indexed, so answers rest on the federal ch.ch page and the canton's pages |
| Which source supports a deadline | every citation carries the verbatim passage (`excerpt`) |
| Source unavailable | a cached copy is labelled with its date (`data.stale_sources`), otherwise `source_error` with the link |

**End to end** (`scripts/e2e_eval.py --questions eval/practice_questions.json`, Claude Code, answers
also reviewed by hand against the pack's criteria, `eval/results/2026-09-25T0704.md`): Sonnet 11/11,
Haiku 10/11. The practice cases are in `eval/practice_questions.json`, together with the pack's three
extra sample questions and a source-failure case (the server offline with an empty cache: both models
said the housing office was unreachable, gave its link and did not guess a rate). Haiku's miss: the
notice-period answer is right but cites ch.ch instead of the article on Fedlex. Reviewing the answers
found one error the automated checks had passed: the City of Bern's autumn holidays were given with the
French-speaking schools' dates. Holiday periods are now checked against the official page (the one on
bern.ch confirms 19 September to 11 October 2026) and school types are named by language, and both
models answer correctly.

Robots.txt and terms of use are respected by default and both are configurable (`SGM_RESPECT_ROBOTS`,
`SGM_RESPECT_TERMS`, see [Configuration](configuration.md)). No credentials are needed.

## End-to-end runs

End-to-end with real MCP clients and LLMs (mirrors the evaluation: 2 clients × 2 LLMs):

```sh
OPENAI_API_KEY=... uv run python scripts/e2e_eval.py    # Claude Code (sonnet, haiku) + OpenCode (gpt-5.4-mini, gpt-4.1-mini)
```

It runs `eval/questions.json` — the 5 published sample questions plus 12 more (de/fr/it/rm/en,
including ask-back, out-of-scope and not-covered cases) — and writes `eval/results/<date>.md`.

### Compared with Claude without this server

The same 28 questions (the 17 above and the organisers' 11 practice cases), Claude Code with Sonnet,
three ways that differ only in their tools (same neutral assistant prompt, `--assistant-prompt`):
this server; web search and fetch, no MCP server (`claude-web`); no tools at all (`claude-base`).
Results: `eval/results/2026-09-25T111906.md` and `…T112059.md` (practice runs first blocked by the
account's spend limit were rerun, so every mode has all 28 answers).

| Mode | 17 questions | Practice cases | Avg calls (17 q.) | Avg seconds (17 q.) | Links to official Swiss authorities (28 answers) |
|---|---|---|---|---|---|
| **With this server** | **16/17** | **11/11** | 1.2 | 14 | **94%** of 34 links |
| Web search, no server | 11/17 | 7/11 | 2.4 searches | 28 | 72% of 67 links (others: Wikipedia, bonus.ch, bern.com, jurawelt.com…) |
| Model knowledge only | 6/17 | 3/11 | 0 | 15 | 1 link in 28 answers |

Reading the answers: web search often reaches the right figure but mixes in comparison sites, news and
tourism pages, rounds official figures ("circa 45.000" inhabitants of Bellinzona instead of the FSO's
45'828), dates the reference rate by its publication rather than its effective date, answers "where I
live" questions by saying it has no access instead of asking for the municipality, and gives canton-wide
advice where the city's own page applies (registering in Bern). It is also slower: twice the calls and
twice the time. Without any tools the model mostly declines or answers from dated knowledge.
Question Q15 ("capital of Australia") is answered from general knowledge in all three modes.

### Latest results (2026-09-25, `eval/results/2026-09-25T0606.md`, hybrid search)

All four runs of the evaluation setup (2 clients x 2 LLMs), scored with the current checks:

| Client + LLM | Pass | Avg tool calls | Avg seconds | First run (`…T0159`) |
|---|---|---|---|---|
| Claude Code + Sonnet | 15/16 | 1.0 | 13 | 14/16 |
| Claude Code + Haiku | 13/16 | 0.9 | 12 | 14/16 |
| OpenCode + gpt-5.4-mini | 15/16 | 1.4 | 9 | 13/16 |
| OpenCode + gpt-4.1-mini | 14/16 | 1.0 | 8 | 13/16 |

Checks: asking back may be a polite request as well as a question; Q12 accepts ch.ch's customs pages,
which state the CHF 5 rule word for word; Q15 ("capital of Australia") follows the challenge text for a
question not about Switzerland: the right response is to say so, without calling the Swiss server.

Remaining misses, honestly reported:

- **Q15 (all four):** the models answer "Canberra" from general knowledge. This run was recorded when
  the server instructions only said not to call its tools for such questions; they now also say to state
  that the question is outside this Swiss service. Rerun with Claude after that change: both still answer
  from general knowledge without calling any tool, so the server never sees the question. The
  challenge's own non-Swiss sample (S5, a fee after moving to Konstanz) passes in all four, because
  there the model does ask the server and gets `not_covered`.
- **Q12 (Haiku, gpt-4.1-mini):** the parcel-VAT question. The CHF 5 parcel rule is in the tool's
  results, but these models apply a neighbouring rule (the travellers' CHF 150 allowance, or "import tax
  on every parcel"). The larger models answer it correctly.
- **Q7 (Haiku):** correct answer, cited from a search result instead of the law text on Fedlex.

Runs are not deterministic: the same model can pass or miss a question between runs (Haiku passed Q7
and Q12 in `…T0552`).

Q17 (French question about registering in Bern, testing the language hint) was added after this run:
Sonnet passed it by searching again in German; Haiku passed 1 of 4 runs.

### Earlier results (2026-09-25, `eval/results/2026-09-25T0159.md`)

| Client + LLM | Pass (content + citation) | Content correct | Published samples | Avg tool calls | Avg seconds |
|---|---|---|---|---|---|
| Claude Code + Sonnet | 14/16 | 14/16 | 5/5 | 1.2 | 12 |
| Claude Code + Haiku | 14/16 | 15/16 | 5/5 | 0.9 | 11 |
| OpenCode + gpt-5.4-mini | 13/16 | 15/16 | 4/5 | 1.4 | 9 |
| OpenCode + gpt-4.1-mini | 13/16 | 14/16 | 5/5 | 1.0 | 7 |

Remaining misses, honestly reported: some answers are correct but name the source without the URL
(Q8, one S3 run); two answers to the parcel-VAT question (Q12) reach the right conclusion with the
travellers' allowance instead of the CHF 5 parcel rule; and a general non-Swiss question (Q15,
"capital of Australia") is answered from model knowledge without calling any tool, which the check at
the time counted as a miss (it now counts as correct, see above; the published non-Swiss sample S5,
Konstanz, passes in all four).
