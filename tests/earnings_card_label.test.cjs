/**
 * Node test for the timing + wall-clock aware earnings card label
 * (utils.js: earningsCardLabel / earningsNoteStatusLabel).
 *
 * utils.js is a browser script (references `window` at module scope and has no
 * native ESM/CJS export under the repo's "type":"module" package), so we load
 * it into a vm sandbox with a `window` stub and a CommonJS `module.exports`
 * shim, then exercise the exported functions.
 *
 * Run: node tests/earnings_card_label.test.cjs
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

function loadUtils() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'utils.js'), 'utf8');
  const sandbox = {
    window: {}, module: { exports: {} },
    console, Intl, Date, Number, Math, parseInt, parseFloat, Set, Object, RegExp,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return sandbox.module.exports;
}

const u = loadUtils();

// UTC instants chosen so the America/New_York (EDT, UTC-4 in June) wall clock
// lands on the target local time.
const AMC_354PM = new Date('2026-06-04T19:54:00Z'); // 3:54 PM ET
const AMC_430PM = new Date('2026-06-04T20:30:00Z'); // 4:30 PM ET
const BMO_900AM = new Date('2026-06-04T13:00:00Z'); // 9:00 AM ET
const BMO_1000AM = new Date('2026-06-04T14:00:00Z'); // 10:00 AM ET
const ANYTIME = new Date('2026-06-04T13:00:00Z');

const cases = [
  ['AMC, today, 3:54 PM ET -> future tense',
    () => u.earningsCardLabel({ report_date: '2026-06-04', timing: 'AMC' }, AMC_354PM),
    'Reports today · AMC'],
  ['AMC, today, 4:30 PM ET -> past tense',
    () => u.earningsCardLabel({ report_date: '2026-06-04', timing: 'AMC' }, AMC_430PM),
    'Reported today · AMC'],
  ['BMO, today, 9:00 AM ET -> future tense',
    () => u.earningsCardLabel({ report_date: '2026-06-04', timing: 'BMO' }, BMO_900AM),
    'Reports today · BMO'],
  ['BMO, today, 10:00 AM ET -> past tense',
    () => u.earningsCardLabel({ report_date: '2026-06-04', timing: 'BMO' }, BMO_1000AM),
    'Reported today · BMO'],
  ['AMC, yesterday -> always past tense regardless of time',
    () => u.earningsCardLabel({ report_date: '2026-06-03', timing: 'AMC' }, ANYTIME),
    'Reported yesterday · AMC'],
];

let failed = 0;
for (const [name, fn, expected] of cases) {
  const got = fn();
  try {
    assert.strictEqual(got, expected);
    console.log(`  ok   ${name}  =>  "${got}"`);
  } catch (e) {
    failed += 1;
    console.error(`  FAIL ${name}\n       expected "${expected}"\n       got      "${got}"`);
  }
}

// note_status secondary line
const noteCases = [
  ['note_status print_reaction',
    () => u.earningsNoteStatusLabel({ note_status: 'print_reaction' }, ANYTIME),
    'Print reaction · awaiting call commentary'],
  ['note_status call_reaction',
    () => u.earningsNoteStatusLabel({ note_status: 'call_reaction' }, ANYTIME),
    'Reaction · full'],
];
for (const [name, fn, expected] of noteCases) {
  const got = fn();
  try {
    assert.strictEqual(got, expected);
    console.log(`  ok   ${name}  =>  "${got}"`);
  } catch (e) {
    failed += 1;
    console.error(`  FAIL ${name}\n       expected "${expected}"\n       got      "${got}"`);
  }
}

if (failed) {
  console.error(`\n${failed} test(s) failed`);
  process.exit(1);
}
console.log(`\nAll ${cases.length + noteCases.length} card-label tests passed`);
