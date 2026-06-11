"""Deterministic renderer for the Section 6 Bull/Base/Bear block.

Sonar returns Section 6 as strict JSON (delimited by
``<BULL_BASE_BEAR_JSON>`` / ``</BULL_BASE_BEAR_JSON>``); this module turns
that JSON into the canonical FROG-golden three-column HTML so the layout is
identical across every ticker instead of improvised per-note class names.

The HTML contract matches ``golden/FROG_drilldown_golden.html`` exactly:
``three-col`` > ``col-card col-{bull,base,bear}`` > ``col-head
col-head-{bull,base,bear}`` / ``col-target`` / ``col-prob`` / ``col-body``,
followed by the "Probability weighting rationale" block.

It ALSO emits the recommendation artifacts the drilldown validator gates on
(``drilldown_validator`` checks h + i): the asymmetric-payoff table and the
"What makes us right" / "What makes us wrong" bullet headings. Those are not
part of the Sonar JSON schema, so they are derived deterministically from the
case targets, probabilities, and thesis bullets.
"""

from __future__ import annotations

import html as _html

CASE_META = {
    'bull_case': ('bull', 'Bull Case', 'var(--green)'),
    'base_case': ('base', 'Base Case', 'var(--teal)'),
    'bear_case': ('bear', 'Bear Case', 'var(--red)'),
}
CASE_ORDER = ('bull_case', 'base_case', 'bear_case')


class BBBValidationError(ValueError):
    """Raised when the Bull/Base/Bear payload fails schema validation."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise BBBValidationError(msg)


def validate_payload(payload: dict) -> None:
    """Validate the BBB payload against the strict schema. Raise on failure."""
    _require(isinstance(payload, dict), 'payload is not an object')
    for key in CASE_ORDER:
        _require(key in payload, f'missing case "{key}"')
        case = payload[key]
        _require(isinstance(case, dict), f'{key} is not an object')
        for fld, typ in (
            ('price_target_low', (int, float)),
            ('price_target_high', (int, float)),
            ('probability_pct', int),
            ('horizon_months', int),
        ):
            _require(fld in case, f'{key}.{fld} missing')
            _require(isinstance(case[fld], typ) and not isinstance(case[fld], bool),
                     f'{key}.{fld} wrong type')
        _require(case['price_target_low'] <= case['price_target_high'],
                 f'{key}: price_target_low > price_target_high')
        bullets = case.get('thesis_bullets')
        _require(isinstance(bullets, list) and len(bullets) == 3,
                 f'{key}.thesis_bullets must be a list of exactly 3 strings')
        for b in bullets:
            _require(isinstance(b, str), f'{key}.thesis_bullets has a non-string')
            wc = len(b.split())
            _require(20 <= wc <= 120,
                     f'{key} bullet word count {wc} outside [20, 120]')
        refs = case.get('claim_refs')
        _require(isinstance(refs, list) and all(isinstance(r, int) for r in refs),
                 f'{key}.claim_refs must be a list of ints')

    probs = [payload[k]['probability_pct'] for k in CASE_ORDER]
    _require(sum(probs) == 100,
             f'probabilities sum to {sum(probs)}, must equal 100')

    rationale = payload.get('probability_rationale')
    _require(isinstance(rationale, str), 'probability_rationale missing')
    rwc = len(rationale.split())
    _require(60 <= rwc <= 240,
             f'probability_rationale word count {rwc} outside [60, 240]')


def _esc(s: str) -> str:
    return _html.escape(str(s), quote=False)


def _fmt_price(v: float) -> str:
    return f'${v:,.0f}' if float(v).is_integer() else f'${v:,.2f}'


def _target_range(case: dict) -> str:
    lo, hi = case['price_target_low'], case['price_target_high']
    if lo == hi:
        return _fmt_price(lo)
    return f'{_fmt_price(lo)}–{_fmt_price(hi)}'


def _return_pct(case: dict, current_price: float) -> str:
    """Midpoint return vs current price, formatted with sign."""
    if not current_price or current_price <= 0:
        return '—'
    mid = (case['price_target_low'] + case['price_target_high']) / 2.0
    pct = (mid - current_price) / current_price * 100.0
    return f'{pct:+.0f}%'


def _card(key: str, case: dict) -> str:
    slug, label, color = CASE_META[key]
    bullets = '\n'.join(
        f'          <p>{_esc(b)}</p>' for b in case['thesis_bullets']
    )
    return (
        f'      <div class="col-card col-{slug}">\n'
        f'        <div class="col-head col-head-{slug}">{label}</div>\n'
        f'        <div class="col-target" style="color:{color};">'
        f'{_target_range(case)}</div>\n'
        f'        <div class="col-prob">{case["horizon_months"]}-Month Target '
        f'&nbsp;·&nbsp; {case["probability_pct"]}% Probability</div>\n'
        f'        <div class="col-body">\n{bullets}\n        </div>\n'
        f'      </div>'
    )


def _payoff_table(payload: dict, current_price: float) -> str:
    rows = []
    for key in CASE_ORDER:
        _, label, _ = CASE_META[key]
        case = payload[key]
        rows.append(
            f'| {label.split()[0]} | {case["probability_pct"]}% | '
            f'{_target_range(case)} | {_return_pct(case, current_price)} |'
        )
    body = '\n'.join(rows)
    return (
        '| Scenario | Probability | NTM target | Return |\n'
        '|----------|-------------|------------|--------|\n'
        f'{body}'
    )


def _right_wrong(payload: dict) -> str:
    """Derive confirm/break bullets from bull (right) and bear (base) theses."""
    right = payload['bull_case']['thesis_bullets'][:2]
    wrong = payload['bear_case']['thesis_bullets'][:2]
    right_li = '\n'.join(f'      <li>{_esc(b)}</li>' for b in right)
    wrong_li = '\n'.join(f'      <li>{_esc(b)}</li>' for b in wrong)
    return (
        '    <p><strong>What makes us right</strong></p>\n'
        f'    <ul>\n{right_li}\n    </ul>\n'
        '    <p><strong>What makes us wrong</strong></p>\n'
        f'    <ul>\n{wrong_li}\n    </ul>'
    )


def render_bull_base_bear(payload: dict, current_price: float) -> str:
    """Render the Section 6 body HTML from a validated BBB payload.

    Returns the inner HTML for the section-body (three-column cards, the
    probability-weighting rationale, the asymmetric-payoff table, and the
    What-makes-us-right/wrong bullets). Raises ``BBBValidationError`` if the
    payload does not satisfy the schema.
    """
    validate_payload(payload)

    cards = '\n'.join(_card(k, payload[k]) for k in CASE_ORDER)
    rationale = _esc(payload['probability_rationale'])
    payoff = _payoff_table(payload, current_price)
    right_wrong = _right_wrong(payload)

    return (
        '    <div class="three-col" style="margin-bottom:16px;">\n'
        f'{cards}\n'
        '    </div>\n\n'
        '    <div style="padding:14px 16px;background:var(--bg);'
        'border-radius:8px;border:1px solid var(--border);font-size:12px;'
        'line-height:1.7;margin-bottom:16px;">\n'
        f'      <strong>Probability weighting rationale:</strong> {rationale}\n'
        '    </div>\n\n'
        '    <div class="subsection-title">Recommendation — Asymmetric Payoff</div>\n'
        f'{payoff}\n\n'
        f'{right_wrong}'
    )
