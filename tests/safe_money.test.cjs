/**
 * Node test for safeMoney's magnitude formatting (earnings.js).
 *
 * earnings.js touches `document` at module scope, so we can't require() it.
 * Instead we lift the two pure functions (_scaleMoney + safeMoney) out of the
 * source into a vm sandbox and exercise them directly. This guards the Bug 2
 * regression where raw revenue integers (e.g. 19311000000) rendered as
 * "$19311000000" instead of "$19.31B".
 *
 * Run: node tests/safe_money.test.cjs
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

function loadSafeMoney() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'earnings.js'), 'utf8');
  const scale = src.match(/function _scaleMoney[\s\S]*?\n}\n/);
  const money = src.match(/function safeMoney[\s\S]*?\n}\n/);
  if (!scale || !money) throw new Error('could not locate safeMoney/_scaleMoney in earnings.js');
  const sandbox = { Math, Number, String, RegExp, module: { exports: {} } };
  vm.createContext(sandbox);
  vm.runInContext(scale[0] + '\n' + money[0] + '\nthis.safeMoney = safeMoney;', sandbox);
  return sandbox.safeMoney;
}

const safeMoney = loadSafeMoney();

const cases = [
  // The 8 raw-integer revenue values from the regression report.
  ['AVGO 19311000000 -> $19.31B', () => safeMoney(19311000000), '$19.31B'],
  ['CRWD 1305375000  -> $1.31B',  () => safeMoney(1305375000),  '$1.31B'],
  ['CXM  220592000   -> $221M',   () => safeMoney(220592000),   '$221M'],
  ['VEEV 835951000   -> $836M',   () => safeMoney(835951000),   '$836M'],
  ['FIVE 1728480000  -> $1.73B',  () => safeMoney(1728480000),  '$1.73B'],
  ['CRDO 407012000   -> $407M',   () => safeMoney(407012000),   '$407M'],
  ['MDT  9017000000  -> $9.02B',  () => safeMoney(9017000000),  '$9.02B'],
  ['PVH  2505100000  -> $2.51B',  () => safeMoney(2505100000),  '$2.51B'],
  // Numeric string without a suffix still scales.
  ['string "19311000000" -> $19.31B', () => safeMoney('19311000000'), '$19.31B'],
  // Already-formatted magnitude strings pass through untouched.
  ['"$3.00B" pass-through', () => safeMoney('$3.00B'), '$3.00B'],
  ['"264M" pass-through',   () => safeMoney('264M'),   '$264M'],
  // EPS-sized numbers are NOT scaled (only one "$", no B/M).
  ['EPS 1.23 -> $1.23', () => safeMoney(1.23), '$1.23'],
  // An explicit unit means the caller pre-scaled; just append.
  ['276.7 unit M -> $276.7M', () => safeMoney(276.7, { unit: 'M' }), '$276.7M'],
  // Missing / blank values degrade to n/a, never a fabricated number.
  ['null -> n/a',  () => safeMoney(null), 'n/a'],
  ['"—" -> n/a', () => safeMoney('—'), 'n/a'],
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

if (failed) {
  console.error(`\n${failed} test(s) failed`);
  process.exit(1);
}
console.log(`\nAll ${cases.length} safeMoney tests passed`);
