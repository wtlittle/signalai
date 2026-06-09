# Applying prioritization in prompts

Concrete instructions to give the model when generating a drilldown/briefing so
the output follows the metric rules. These map to the HTML conventions the
drilldown renderer expects.

## Instruction patterns

- **claim:N links** — every cited number is wrapped in `<a href="claim:N">…</a>`
  where N indexes the citations audit trail. Tell the model: "Wrap every numeric
  value in a `claim:N` link tied to its citation; an uncited number is a defect."
- **Em-dash for unknown** — instruct: "If a value is not available, render an
  em-dash (—), never 0, 'N/A' text, or a guess. If a whole field is unavailable,
  mark it `MISSING`."
- **Color spans for deltas** — "Wrap positive deltas in `<span class="pos">` and
  negative in `<span class="neg">`; beats/misses in `beat`/`miss`."
- **num class for cells** — "Every numeric table cell gets `class="num"` for
  right-aligned tabular figures."
- **Flavor + window labels** — "Label every margin/EPS as GAAP or non-GAAP, and
  every multiple as LTM or NTM. Mark every forward estimate with a trailing E."

## Before / after

**Before (defective):**
```html
<td>EPS 0.82</td>
<td>EV/Rev 15x</td>
<td>Cloud growth was strong</td>
```

**After (correct):**
```html
<td class="num"><a href="claim:65">$0.82</a> NG</td>           <!-- flavor labeled, cited, num -->
<td class="num"><a href="claim:100">~15.1x</a> NTM</td>         <!-- window labeled -->
<td class="num"><span class="pos"><a href="claim:53">+44.9%</a></span></td>  <!-- delta colored -->
```

**Before (missing data faked):**
```html
<td>Gross Retention</td><td>95%</td>   <!-- invented -->
```

**After (missing data honest):**
```html
<td>Gross Retention</td><td class="num">MISSING</td>
<td>Annual historical not disclosed; only Q1'26 (97%) from transcript</td>
```

## Ordering instruction

For the financial-model table, instruct the model to lead with **revenue and
growth**, then **gross margin (NG)**, **operating income (GAAP)**, **FCF / FCF
margin**, then **EPS (GAAP and NG)** — matching the SaaS priority in `SKILL.md`
§2, not net-income-first.
