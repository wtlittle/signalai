---
name: industry-cross-reference
description: "Grounds every industry claim in a buy-side research artifact in verifiable data instead of hand-waving. Use whenever a post-earnings note, drilldown, or weekly briefing trend cites TAM, market share, competitive position, or category growth. Encodes the source-of-truth hierarchy (Supabase market_intel_ticker first), the citation rules for TAM/CAGR/competitors, and the anti-fabrication guardrails that keep a note from inventing a round-number market size."
---

# Industry Cross-Reference

A thesis that says a company is "winning in a large, rapidly growing market" is
worthless. A thesis that says it "grew subscription revenue 22% this quarter
against a 14% category CAGR for ITSM (Gartner, 2026-2030 forecast), implying
~800bps of share gain" is a position. This skill is the house rule for getting
from the first to the second -- and for refusing to write either when the data
isn't there.

## 1. When to use this skill

Apply it any time an artifact makes an industry-level claim:

- **Post-earnings notes** -- the Thesis Impact section should frame in-quarter
  growth against the category CAGR and name the competitor benchmark.
- **Drilldowns** -- TAM, share, and competitive-position paragraphs.
- **Weekly briefing trends** -- subsector growth narratives, "who is taking
  share" claims.
- **Any prose** that says TAM, market share, competitive position, category
  growth, or names a competitive dynamic.

If you are about to write the words *TAM*, *market share*, *category growth*,
*competitive position*, or name a rival, this skill applies.

## 2. Source-of-truth hierarchy

Use the highest tier that carries the figure. Stop at the first one that does.

1. **Supabase `market_intel_ticker` row updated within 14 days.** The weekly
   `market_intel_refresh` harvester populates per-ticker TAM, category CAGR,
   demand drivers, and named competitors with source URLs. This is the primary
   ground truth for the pipeline. A row older than 14 days is **stale** -- treat
   it as if it were missing (drop to tier 4), do not cite its numbers as current.
2. **The ticker's last 4 earnings-transcript snippets** covering TAM, share, or
   competition -- management's own framing, quoted and attributed to the call
   date.
3. **An explicit sonar query** constrained with `allowed_domains` to
   gartner.com, forrester.com, idc.com, statista.com, g2.com, sec.gov (and the
   company's own investor-relations domain). Cite the returned source URL.
4. **If all of the above fail -> render an em-dash `--` or "n/a" and DO NOT
   invent numbers.** Write "industry data refresh pending" in a note rather than
   a guessed figure. A missing number is a data gap; a fabricated one is a
   defect that destroys the artifact's credibility.

This mirrors the broader provenance rule in
[[financial-metric-prioritization]] section 4 -- highest-authority source wins,
and a gap is marked, never filled with a guess.

## 3. Output rules

Every industry claim must be auditable:

- **Cite the source of every industry claim** -- the Supabase row (its `ticker`
  + `updated_at`), the transcript date, or the source URL. No claim ships
  uncited.
- **CAGR must specify its time window** -- "14% ITSM CAGR (2026-2030)", never a
  bare "14% CAGR".
- **TAM must specify currency and year of estimate** -- "$460B TAM (USD, 2026
  estimate)", never "$460B TAM".
- **Competitors must be named** -- "Databricks, AWS Athena, and Microsoft
  Fabric", never "various competitors" or "several players".

## 4. Cross-reference patterns

Weave the industry data into the thesis; do not bolt it on as a separate fact
dump. The move is to put the company's own number next to the market's number
and let the gap carry the argument:

- **Share-gain framing:** "ServiceNow's 22% subscription growth this quarter
  comes against a 14% category CAGR for ITSM per Gartner (2026-2030 forecast)
  [link] -- implying roughly 800bps of share gain."
- **Deceleration-vs-market framing:** "Revenue growth of 18% now only modestly
  leads the 15% category CAGR (IDC, 2025-2029) [link]; the share-gain story that
  justified the multiple is narrowing."
- **TAM-headroom framing:** "At ~$3.2B LTM revenue against a $460B TAM (USD, 2026
  estimate, [link]), penetration is under 1% -- the runway is not the debate;
  execution against Databricks and BigQuery is."
- **Competitive-pressure framing:** "Management's pricing commentary tracks the
  threat ranking in the pack: Databricks (Leader, High) and Microsoft Fabric
  (Leader, High) are the named pressure points, not the legacy niche vendors."

The pattern is always: *company metric* + *market benchmark with source* ->
*implication*. The implication is the analysis; the two cited numbers are the
evidence.

## 5. Anti-patterns

These are defects. Do not ship them:

- **Round, unsourced TAM numbers.** "$50B TAM" with no source and a suspiciously
  round value is a tell that the number was invented. Every TAM carries a source
  and a year.
- **"Rapidly growing" / "large and growing" / "massive market".** Adjectives are
  not data. Replace with a CAGR and a window, or say nothing.
- **TAM without a year of estimate.** A market size is a point-in-time forecast;
  an undated TAM is unfalsifiable and therefore useless.
- **"Various competitors" / "several players".** Name them, with their
  quadrant/threat level and source, or omit the competitive claim.
- **Citing a stale (>14 day) pack as if current.** If the pack is stale or
  missing, write "industry data refresh pending" -- never launder old or absent
  numbers into a fresh-sounding claim.

## 6. How this is wired into the pipeline

The post-earnings note generator fetches the per-ticker pack via
`automation/data/industry_pack_fetcher.py` (`fetch_industry_pack(ticker)`),
which reads `market_intel_ticker` from Supabase and returns the normalized dict
plus an `is_stale` flag (True when `updated_at` is older than 14 days). The
context builder stores it on `ctx["industry_pack"]`; the prompt builder renders
it via `_render_industry_pack` as an INDUSTRY PACK block -- ahead of the thesis
instructions -- carrying the FRESH/STALE/MISSING status and the
do-not-fabricate instruction. When the pack is stale or missing, the block tells
the model to write "industry data refresh pending" instead of inventing figures.
