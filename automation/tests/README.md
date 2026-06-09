# automation/tests

Pytest suite for the automation pipeline. Run the whole suite with:

```
python -m pytest automation/tests -q
```

## Drilldown structural-quality diff (not in CI)

`test_drilldown_quality.py` checks a generated drilldown note against the
committed golden snapshot (`golden/FROG_drilldown_golden.html`). It counts
structural features — section count (must be 14), claim:N links, word count,
table count, and `pos` / `neg` / `num` span classes — and flags drift.

This is **run on demand, not in CI** — counting a full note is comparatively
expensive and the golden covers a single ticker, so it is invoked manually or by
the validator rather than on every push. The pytest cases in the module
(`test_golden_*`) only lock the golden baseline and are cheap; the candidate
diffing is exposed as a CLI:

```
python -m automation.tests.test_drilldown_quality <candidate.md>
```

Exit `0` if every metric is within tolerance of the golden, `1` if any metric
drifts out. Tolerance is ±5% on word count, table count, and claim count;
section count and span-class counts must match exactly. A per-metric diff table
is printed either way.
