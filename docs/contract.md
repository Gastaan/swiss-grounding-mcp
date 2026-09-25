# Response contract and tools

[← Back to the README](../README.md)

## Tools

| Tool | Use it for |
|---|---|
| `search_official_info` | "How do I…", rules, deadlines, fees — verbatim excerpts from official pages, filtered to federal + the given canton/municipality |
| `read_official_page` | Live text of an official page (allowlisted Swiss government domains only), focused on given words |
| `swiss_federal_law` | Federal law articles (SR number or abbreviation such as OR/CO, ZGB, SVG, AIG) |
| `health_insurance_premiums` | Cheapest KVG/LAMal premiums |
| `swiss_holidays` | School or public holidays |
| `waste_collection` | Next collection dates by waste type |
| `public_transport` | Connections and departures |
| `federal_votes` | Next vote subjects or results |
| `swiss_rates` | Reference interest rate, SNB exchange rates |
| `company_register` | Company lookup by name or UID |
| `swiss_place_info` | Municipality facts, population, website |
| `current_weather` | Latest MeteoSwiss measurements |
| `swiss_coverage` | The declared scope ([README](../README.md#coverage)), for the assistant |

## Response contract

Every tool returns the same JSON object (as `structuredContent` with an `outputSchema`, and as text):

```json
{
  "status": "ok | needs_context | not_covered | not_found | source_error",
  "summary": "One factual sentence, ending with 'Source: <publisher> – <url>'",
  "data": {"...": "tool-specific facts"},
  "citations": [{"title": "", "url": "", "publisher": "", "level": "federal|cantonal|municipal|semi-official|community",
                 "jurisdiction": "CH | CH-VD | CH-VD-5586", "retrieved_at": "", "valid_for": "", "excerpt": "verbatim"}],
  "missing_context": [{"field": "municipality", "question": "…", "options": ["Buchs (ZH)", "Buchs (SG)"]}],
  "guidance": "what the assistant should do next"
}
```

- `needs_context` — ask the user exactly `summary` (e.g. which municipality; which of three *Buchs*).
- `not_covered` — outside Switzerland or outside the declared scope; the assistant should say so.
- `not_found` / `source_error` — nothing found / source unreachable; never answer from memory.
- Places can be given as the user wrote them: *Genf, Ginevra, Genève*, *Schuls → Scuol*, *8003*,
  *Zurich 8003*, *Bahnhofstrasse 1, Zürich*. Ambiguous names return options; foreign places are flagged.
- Invalid arguments are returned with `isError: true` and a readable message.
- `data.stale_sources` appears when a live source was down and an earlier cached copy was used (at most
  `SGM_MAX_STALE_HOURS` old); `guidance` then tells the assistant to give the user that date.
- `search_official_info` sets `data.local_match: false` when a place was given but no cantonal or
  municipal page matched as well as the federal ones; the summary says so and the municipality's
  website is cited, so a federal page is not passed off as the local rule.
- `search_official_info` sets `data.place_languages` (e.g. `["de"]`) when the question is in another
  language than the place publishes in and none of the place's own pages matched. The summary and
  guidance then ask the assistant to search again with its key words translated into that language: a
  French question about registering in Bern leads to a German search that finds Bern's own page. The
  place's languages come from the language of its pages in the index.
- The output schema lists the top-level fields only; the fields inside `citations` and
  `missing_context` are described once in the server instructions, which keeps `tools/list` small.
