# Market context source chain

For non-financial market context — TAM, competition, category CAGR, market share
— the source ranking differs from the financials chain. These figures are not in
the company's filings as primary data, so the hierarchy favors specialist
research houses, then primary disclosures, then company framing.

## Priority for TAM / category CAGR / share

```
Gartner / Forrester / IDC / Statista / G2   →   SEC filings (10-K risk + market sections)   →   company IR
```

1. **Specialist research houses first.** Gartner, Forrester, IDC, Statista, and
   G2 (for category positioning / reviews) are the most credible third-party
   sizing. Cite the **primary report**, not a synthesized blog or a number
   relayed second-hand.
2. **SEC filings next.** A company's 10-K market-opportunity and risk-factor
   sections give a primary, legally-reviewed view of the market it competes in.
   Use for share claims the company will stand behind.
3. **Company IR last.** Investor-day TAM slides are useful but self-serving —
   companies define TAM expansively. Treat as the company's framing, not
   neutral truth.

## Cite primary, not synthesized

- Always attribute TAM/CAGR to the **named report and year** (e.g. "Mordor
  Intelligence DevSecOps Market Report 2024–2029, ~$14.4B 2024 → ~$36.4B 2029,
  ~20% CAGR"), not "industry estimates."
- **Rank source credibility.** Gartner/IDC-tier > mid-tier (Mordor, Grand View)
  > vendor-sponsored. When using a mid-tier source, say so and note the
  confidence level and any corroboration.
- If the metric is **MISSING** from the internal data block (e.g. the Supabase
  `market_intel` table has no row), mark it `MISSING` and state it was sourced
  externally with a confidence rating — never silently fill the gap.

## Provenance

Same rule as financials: source name + URL/report reference + retrieval date.
TAM figures carry a `tam_source` marker in the pipeline; surface it in the note's
sources section and flag medium/low confidence explicitly.
