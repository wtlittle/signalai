---
name: financial-metric-prioritization
description: "Canonical metric priority and source-of-truth hierarchy for buy-side equity research artifacts. Use when drafting or reviewing drilldowns, briefings, valuation memos, earnings notes, or comp tables that cite financial metrics for a public company. Decides which financial figure to cite, in which flavor (GAAP vs non-GAAP, NTM vs LTM), and from which source when several disagree."
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

## 2. Metric priority by company archetype

There is no single "revenue first" default. Anchor every growth and guidance
discussion on the metric the company itself leads with in its earnings
release. Classify the name, then prioritize top-down for that archetype.

### 2a. ARR-led SaaS — lead with ARR, not GAAP revenue

A large cohort of subscription names reports **ARR (Annual Recurring
Revenue) as the headline growth metric** and treats GAAP revenue as a
lagging, secondary line. For these, ARR (current ARR + net new ARR + NRR)
is the primary growth metric, revenue is secondary, and **RPO / cRPO is the
leading indicator** of forward growth.

The ARR-led cohort (anchor commentary on ARR, not revenue):

> RBRK, NET, CRWD, ZS, OKTA, DDOG, MDB, SNOW, ESTC, S, NTNX, BILL, GTLB,
> FROG, CFLT, DT, PD, BOX, ASAN, MNDY, SMAR, ZUO, AI, PATH, U, RNG, FIVN,
> TWLO, FSLY, NCNO, BSY, AVPT, DOMO

Priority order for an ARR-led name:

1. **ARR** (current ARR + net new ARR) — the headline growth line.
2. **NRR / net dollar retention** — names expansion quality.
3. **RPO / cRPO** — the forward-booking leading indicator.
4. **Gross retention** — churn floor.
5. **Revenue / gross margin** — the GAAP anchor, now secondary.
6. **FCF margin / Rule of 40** — profitability + balance of growth and margin.

### 2b. Other archetypes

| Archetype | Lead metrics |
|---|---|
| **ARR-led SaaS** (cohort above) | ARR, net new ARR, NRR, RPO, cRPO, gross retention → then revenue / gross margin / FCF / Rule of 40 |
| **Usage-based** (DDOG, MDB, NET Workers, SNOW) | consumption revenue growth + DBNR, NRR |
| **Hardware / semis** (NVDA, AMD, AVGO) | revenue by segment, GM%, inventory days, book-to-bill |
| **Legacy enterprise** (ORCL, CRM, NOW) | cloud revenue mix, billings, RPO, cRPO |
| **Hyperscalers** (MSFT, GOOGL, AMZN) | segment revenue, segment op margin, capex |
| **Payments** (V, MA, FI) | TPV, take rate, transactions |

### 2c. Fallback order (no clear headline metric / space is scarce)

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
See `patterns/provenance.md`. Mark genuinely undisclosed data explicitly with an
em-dash `—` (never the literal word "MISSING" in a rendered note, and never a
guess or a silent omission); explain the gap in prose or the data-quality notes.
