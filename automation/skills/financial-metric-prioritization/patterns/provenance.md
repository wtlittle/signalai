# Provenance pattern

Every metric in a finished artifact must trace to three things:

1. **Source name** — the provider or document (e.g. "Finnhub", "FROG 10-K FY2025",
   "Mordor Intelligence DevSecOps Report 2024–2029").
2. **URL or filing reference** — a link, or an unambiguous filing locator.
3. **Retrieval date** — when the value was fetched (data goes stale).

## Inline provenance marker

For a metric whose origin matters at the point of citation, embed a superscript
source tag next to the value:

```html
Cloud revenue grew +50% YoY<sup class="src">[finnhub 2026-06-08]</sup>
```

```html
NRR 120%<sup class="src">[FROG Q1'26 call 2026-05-07]</sup>
```

The tag carries `[source retrieval-date]`; keep it terse. Use it where a reader
might otherwise wonder which provider a contested number came from (in-quarter
actuals, third-party TAM, anything flagged medium-confidence).

## Sources section

Every artifact ends with a sources section that, per category, lists source
name + link + retrieval date, and an explicit **MISSING fields** block stating:
what was unavailable, why, and how it was addressed (proxy, external estimate,
or omitted). Example obligations:

- `finance_estimates` returned no data → forward figures from guidance, marked E.
- TAM not in Supabase `market_intel` → sourced externally, confidence noted.
- Segment-level gross margins not disclosed → analysis limitation stated.

## Pipeline markers ↔ citations

Internal `<field>_source` markers (see `references/finance-source-chain.md`) and
the `claim:N` audit trail are the machine-side of provenance: the marker says
which connector produced the field, the `claim:N` ties the rendered value to its
citation record. Surface both — the reader sees `claim:N` links and the sources
section; the validator checks the `<field>_source` markers and the `tam_source`.
