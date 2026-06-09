# Finance data source chain

How the automated pipeline ranks its *data providers* for a ticker's financials.
This is distinct from the filing-tier hierarchy in `SKILL.md` §4 — that ranks
*documents* (10-K > 10-Q > release > IR > sell-side); this ranks the *connectors*
the pipeline queries to fill those fields.

## The chain (`_ordered_sources()`)

```
PerplexityFinance  →  Finnhub  →  yfinance  →  Perplexity (sonar)  →  CachedFallback
```

Each source fills only the fields the prior one left null; the first
authoritative value for a field wins.

### When each is authoritative

- **PerplexityFinance** — leads. Its `finance_earnings_history` tool supplies the
  full trailing-8Q array and the structured financials/ratios/segments. Primary
  source for quotes, financials, ratios, segments, adjusted metrics, insider /
  institutional holders, earnings history, analyst research.
- **Finnhub** — gap-fills in-quarter EPS actual, consensus, and surprise when
  PerplexityFinance leaves them null. Authoritative for in-quarter
  actual/consensus/surprise.
- **yfinance** — gap-fills remaining nulls (quotes, basic financials). Lowest-cost
  quantitative backstop; no LLM.
- **Perplexity (sonar)** — qualitative source: FY guidance + narrative. **Never**
  the source for in-quarter actuals.
- **CachedFallback** — runs last. Re-emits still-fresh on-disk fields so a
  transient miss across the live sources does not re-quarantine a mature ticker.

## Provenance markers

Every accepted field carries a `<field>_source` marker recording which connector
supplied it (e.g. `history_8q_source`, `eps_consensus_source`,
`transcript_source`, `_source` on the cached blob). Downstream consumers and
cost-tracking distinguish field origin by these markers. When citing a metric in
an artifact, the `<field>_source` tells you which provider — but you must still
trace the metric to its underlying *filing* per the §4 hierarchy.
