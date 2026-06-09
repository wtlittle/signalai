---
name: financial-metric-prioritization
description: Canonical metric priority and source-of-truth hierarchy for buy-side equity research artifacts. Decides which financial figure to cite, in which flavor (GAAP vs non-GAAP, NTM vs LTM), and from which source when several disagree.
when_to_use: Drafting or reviewing any artifact that cites financial metrics for a public company — drilldowns, briefings, valuation memos, earnings notes, comp tables. Use it to pick the right metric, label it correctly, and trace it to an authoritative source.
---

# Financial Metric Prioritization

Buy-side notes live or die on citing the *right* number in the *right* flavor from
the *most authoritative* source. This skill encodes the house rules.

## 1. GAAP vs non-GAAP

- **Always label which one.** Never print a margin, EPS, or operating-income
  figure without GAAP / non-GAAP (NG) attached. A bare "EPS $0.82" is a defect.
- **GAAP is the anchor; non-GAAP is the supplement.** Lead with GAAP for the
  legal/headline figure, then show non-GAAP alongside with the bridge named
  (almost always stock-based compensation for SaaS). When GAAP and non-GAAP
  diverge materially, state the bridge size — e.g. "GAAP -$0.62 vs non-GAAP
  +$0.82, a $1.44 gap, ~entirely SBC at 29.5% of revenue."
- **Never let non-GAAP silently replace GAAP.** Consensus EPS usually tracks
  non-GAAP while reported GAAP EPS "misses" — call that out rather than implying
  a real miss.
- For a business with GAAP losses but strong cash generation, **FCF is the most
  analytically honest profitability metric** — but still show the GAAP line and
  the SBC dilution cost (shares × SBC/share), since dilution is a real cost to
  equity holders.

## 2. Metric priority for SaaS / software

When space is scarce, prioritize top-down in this order:

1. **Revenue** (and its growth rate) — the single most price-sensitive line.
2. **Gross profit / gross margin** — names the unit economics; flag GAAP vs NG.
3. **Operating income / operating margin** — GAAP first, NG bridge named.
4. **Free cash flow / FCF margin** — the honest profitability proxy under heavy SBC.
5. **Net income / EPS** — last; most distorted by SBC, tax, and one-offs.

Segment growth that drives the thesis (e.g. cloud revenue YoY, NRR, RPO,
$100K+/$1M+ ARR cohorts) outranks aggregate net income for a growth name.

## 3. NTM vs LTM

- **Label the window every time:** LTM (trailing twelve months) or NTM (next
  twelve months). An unlabeled "EV/Rev 15x" is ambiguous and a defect.
- Valuation multiples for growth names are usually quoted **NTM** (forward) —
  but always show the LTM anchor too so the reader sees the growth being paid for.
- NTM revenue should trace to guidance or a stated estimate, **marked E**. Never
  present an estimate as an actual.

## 4. Source-of-truth hierarchy

When two sources disagree on the same metric, prefer the higher tier:

```
10-K  >  10-Q  >  earnings release (8-K / press)  >  IR / investor day deck  >  sell-side note
```

- Audited annual (10-K) beats interim (10-Q) beats company press release beats
  investor-relations decks beats third-party sell-side synthesis.
- Use the highest tier that actually carries the figure; cite the lower tier only
  when the higher one does not disclose it (e.g. NRR often lives only in the
  earnings call / IR deck, not the 10-K).

See `references/finance-source-chain.md` for how the automated pipeline ranks
its *data providers* (a separate concern from filing tier), and
`references/market-context-chain.md` for TAM / competitive context.

## 5. Provenance is mandatory

Every metric traces to **source name + URL or filing reference + retrieval date**.
See `patterns/provenance.md`. Mark missing data explicitly as `MISSING` rather
than guessing or omitting silently.
