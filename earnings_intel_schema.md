# Earnings Intel — Canonical Schema

One persistent record per ticker. Updated in place (never duplicated). The same object is rendered whether the company is pre-earnings or post-earnings — only the `state` and conditional sub-modules change.

```jsonc
{
  "last_updated": "2026-04-21T15:30:00-04:00",
  "tickers": {
    "UNH": {
      "ticker": "UNH",
      "company_name": "UnitedHealth Group",
      "state": "pre_earnings",                // "pre_earnings" | "post_earnings" | "idle"
      "inflection_status": "PRE",             // "PRE" | "MID" | "POST" | "NONE"
      "last_earnings_date": "2026-01-27",
      "next_earnings_date": "2026-04-21",
      "intel_updated_at": "2026-04-21T08:00:00-04:00",
      "refresh_reason": "scheduled_pre_earnings",  // enum: scheduled_pre_earnings | post_earnings_update | manual_refresh

      "bottom_line": "UNH is a binary 1Q catalyst name. MCR is the only number that matters — an in-line or better 88.5-89.0% print with maintained FY guide unlocks a re-rate toward $320; a 89.5%+ miss or DOJ development takes the stock sub-$250. The sector-idiosyncratic overhang (MA rate shock + DOJ + Optum Health restructuring) means we prefer a small tracking position into the print, with conviction size reserved for a clean quarter that validates Hemsley's 'transition year' narrative.",

      "bull_case": {
        "thesis_headline": "MCR normalizes and Optum rightsizing proves on-track — stock re-rates to 18x FY26.",
        "pattern": "Defensive healthcare name with a multi-quarter setup where the first clean quarter resets the narrative.",
        "pushes_higher": [
          "MCR ≤88.3% in Q1, ahead of 88.8% FY guide",
          "FY26 EPS guide raised above $17.75",
          "Optum Health margins showing early recovery",
          "No new DOJ developments on or near the print"
        ],
        "pushes_lower": [
          "Elevated utilization trends in behavioral/specialty drugs persist",
          "MA 2027 final rate comes in below advance notice",
          "Optum Rx commercial momentum softens"
        ]
      },

      "base_case": {
        "setup_headline": "In-line print, maintained guide, narrative intact but unrewarded.",
        "pushes_higher": [
          "MCR 88.5-89.0% (middle of guided band)",
          "Optum Rx continues to offset Optum Health weakness",
          "Hemsley reiterates '2026 transition, 2027 acceleration'"
        ],
        "pushes_lower": [
          "Revenue sequentially lighter than $113.2B Q4",
          "MA enrollment attrition tracks to guided 1.3-2M decline",
          "Lack of DOJ resolution keeps overhang priced in"
        ]
      },

      "bear_case": {
        "thesis_headline": "MCR miss plus guidance cut — stock retests $235 floor.",
        "pattern": "Third consecutive disappointment; sector derate accelerates.",
        "pushes_higher": [
          "Bear thesis is primarily about bad news, not good — this field should capture offsets that would prevent downside"
        ],
        "pushes_lower": [
          "MCR ≥89.5% — FY guide mathematically at risk",
          "FY26 EPS guide narrowed or lowered",
          "Material DOJ development disclosed pre-call",
          "MA enrollment attrition worse than 2M"
        ]
      },

      "signal_scorecard": [
        {
          "signal_id": "mcr_normalization",
          "label": "MCR Normalization",
          "status": "WATCHING",                // "WATCHING" | "CONFIRMED" | "FAILED"
          "note": "88.8% ±50bps full-year target; Q1 is the first tangible read.",
          "watch_quarter": "Q1 FY2026",
          "confirmed_threshold": "MCR ≤89.0% with maintained guide",
          "failed_threshold": "MCR ≥89.5% triggers guide risk"
        },
        {
          "signal_id": "optum_health_rightsizing",
          "label": "Optum Health Rightsizing",
          "status": "WATCHING",
          "note": "VBC membership guided down ~10% in 2026; Q1 should show early cost-out.",
          "watch_quarter": "Q1 FY2026",
          "confirmed_threshold": "Optum Health operating margin flat-to-positive",
          "failed_threshold": "Operating loss deepens vs Q4 2025"
        },
        {
          "signal_id": "doj_investigation_quiet",
          "label": "DOJ Investigation Silent",
          "status": "WATCHING",
          "note": "No indictments since Aug 2025; any pre-call development = severe negative."
        }
      ],

      "guidance_profile": {
        "fy_guide_eps_low": 17.75,
        "fy_guide_eps_high": null,
        "fy_guide_revenue_low": 439000,       // in $M
        "fy_guide_revenue_high": null,
        "fy_mcr_target_midpoint": 88.8,
        "fy_mcr_target_range_bps": 50,
        "last_changed": "2026-01-27",
        "guide_style": "floor"                // "range" | "floor" | "ceiling" | "point"
      },

      "tone_drift": {
        "current_tone": "cautious_constructive",
        "prior_tone": "defensive",
        "tone_notes": "Hemsley's language shifted from 'stabilize' (Aug) to 'transition year' (Jan) — a small but real upgrade."
      },

      "theme_lifecycle": [
        {"theme": "Medicare Advantage rate shock", "stage": "chronic", "since": "2025-Q4"},
        {"theme": "DOJ risk adjustment probe", "stage": "chronic", "since": "2023"},
        {"theme": "Optum Health restructuring", "stage": "active", "since": "2025-Q3"},
        {"theme": "MCR normalization cycle", "stage": "emerging", "since": "2026-Q1"}
      ],

      "inflection_library": [
        {
          "date": "2026-01-27",
          "type": "guidance_reset",
          "headline": "FY26 guide issued at >$17.75 EPS with MCR 88.8%; MA rate shock signaled.",
          "stock_reaction_pct": -12.0
        },
        {
          "date": "2025-10-28",
          "type": "strategy_shift",
          "headline": "Optum Health admitted to straying from intent; VBC rightsizing announced.",
          "stock_reaction_pct": -8.4
        }
      ],

      "source_metadata": {
        "primary_sources": [
          {"label": "UNH 2025 Annual Results / 2026 Outlook", "url": "https://www.unitedhealthgroup.com/content/dam/UHG/PDF/investors/2025/unh-reports-2025-results-and-issues-2026-outlook.pdf"},
          {"label": "Reuters UNH Q4 2025", "url": "https://www.reuters.com/business/healthcare-pharmaceuticals/unitedhealth-forecasts-2026-profit-slightly-above-estimates-2026-01-27/"}
        ],
        "legacy_note_path": "notes/pre_earnings/UNH_2026-04-21.md"
      },

      /* === POST-EARNINGS ONLY (present when state = "post_earnings") === */
      "post_earnings_review": {
        "active": false,
        "earnings_date": null,
        "visible_until": null,
        "takeaways_headline": null,
        "takeaways_bullets": [],
        "what_happened_headline": null,
        "what_happened_bullets": [],
        "stock_reaction_pct": null
      },
      "previous_bottom_line": null,
      "signal_changes": []  // [{"signal_id": "...", "old_status": "WATCHING", "new_status": "CONFIRMED", "note": "..."}]
    }
  }
}
```

## Rules

1. **One record per ticker.** Refreshes overwrite fields; they do not create new records.
2. **State transitions:**
   - `idle` → `pre_earnings` when within 14 days of `next_earnings_date`
   - `pre_earnings` → `post_earnings` after earnings date (sets `post_earnings_review.active = true`, `visible_until = earnings_date + 7d`)
   - `post_earnings` → `idle` after `visible_until` passes (review collapses, `active = false`)
3. **On post-earnings refresh:** save current `bottom_line` to `previous_bottom_line` before overwriting. Populate `signal_changes[]` with any status deltas.
4. **Inflection badge values:** `PRE` (upcoming), `MID` (earnings day / day after), `POST` (recently reported, review active), `NONE` (idle).
5. **Headline first, bullets second. Insight first, evidence second.** Avoid "solid quarter" / "mixed results".
