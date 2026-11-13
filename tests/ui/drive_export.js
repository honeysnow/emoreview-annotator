// Headless smoke test of export.js in both modes (DOM shim, stubbed fetch).
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const { makeEnv } = require('./dom.js');
const src = fs.readFileSync(path.resolve(__dirname, '../../src/annotool/static/export.js'), 'utf8');
const boot = { batch: 'B1', annotator: 'HZY', documents: [{ id: 'D1', n: 2 }], document: 'D1', total: 2, type: process.argv[2] || 'annotations',
  reviews: [{ name: 'R1', a: 'OYC', b: 'TJH', total: 5, diff: 3, open: 2 }], review: 'R1' };
const ids = ['boot', 'type-ann', 'type-cmp', 'panel-ann', 'panel-cmp', 'foot', 'document', 'review', 'opt-unannotated', 'opt-warn', 'opt-prefix', 'opt-formulas', 'opt-agree', 'opt-note', 'opt-cprefix',
  'c-all', 'c-dis', 'c-open', 'filename', 'summary', 'pv', 'more', 'warnings', 'status', 'dl-csv', 'dl-xlsx'];
const env = makeEnv(ids); const { byId, document } = env;
byId.boot.textContent = JSON.stringify(boot);
['opt-unannotated', 'opt-warn', 'opt-prefix', 'opt-formulas', 'opt-agree', 'opt-cprefix'].forEach((i) => { byId[i].checked = true; });
byId.review.value = 'R1'; byId.document.value = 'D1';
byId['type-ann'].classList = { toggle() {} }; byId['type-cmp'].classList = { toggle() {} };
document.querySelector = (sel) => ({ value: /cscope/.test(sel) ? 'disagreements' : 'all' });
document.querySelectorAll = () => [];
const calls = [];
const groups = ['Polarity', 'Arousal', 'Emotion Tag', 'Emotion Cues', 'Emotion Source', 'Emotion Cause Object', 'Emotion Cause Action', 'Emotion Experiencer'];
const fetch = (url, o) => {
  const body = JSON.parse(o.body); calls.push({ url, body });
  const cmp = /comparison/.test(url);
  const headers = cmp ? ['DocumentID', 'SentID', 'Sentence'].concat(...groups.map((g) => [g, g, g, 'OYCxTJH'])) : ['DocumentID', 'SentID', 'Sentence', 'Polarity'];
  const labels = cmp ? ['', '', ''].concat(...groups.map(() => ['OYC', 'TJH', 'Final', ''])) : [];
  const row = headers.map((h, i) => (cmp && i >= 3 && (i - 3) % 4 === 3 ? 'X' : 'v'));
  const d = { filename: cmp ? 'TRAIN_2_EmoReview40K_Comparison_Sheet_20260101.xlsx' : 'HZY_B1_annotation.xlsx', headers, labels, rows: [row, row], total: 9, all: 20, warnings: ['CS1: not annotated yet'],
    agreement: Object.fromEntries(groups.map((g, i) => [g, 40 + i])) };
  return Promise.resolve({ ok: true, json: () => Promise.resolve(d) });
};
const ctx = { document, history: { replaceState() {} }, location: { href: 'http://x/export' }, URL, fetch, confirm: () => true, console, JSON, Object, Math, Promise, Array };
vm.createContext(ctx); vm.runInContext(src, ctx);
const tick = () => new Promise((r) => setTimeout(r, 5));
(async () => {
  await tick();
  const t = byId.pv;
  if (boot.type === 'comparison') {
    assert.equal(t.children.length, 2 + 2, 'group row + header row + 2 data rows');
    assert.equal(t.children[0].children.length, 1 + 8, 'group header spans per field');
    assert(/Polarity · 40% agree/.test(t.children[0].textContent));
    assert(/OYC · Polarity/.test(t.children[1].textContent), 'annotator labels shown above field names');
    assert.equal(calls[0].url, '/api/export/comparison/preview');
    assert.equal(calls[0].body.batch, 'R1'); assert.equal(calls[0].body.scope, 'disagreements'); assert.equal(calls[0].body.formulas, true);
    assert(/9 of 20 rows/.test(byId.summary.textContent)); assert.equal(byId['panel-cmp'].hidden, false);
    assert(/Disagreements only · 3/.test(byId['c-dis'].textContent));
  } else {
    assert.equal(t.children.length, 3);
    assert.equal(calls[0].url, '/api/export/annotations/preview'); assert.equal(calls[0].body.batch, 'B1'); assert.equal(calls[0].body.annotator, 'HZY');
    assert.equal(byId.warnings.hidden, false); assert.equal(byId['panel-ann'].hidden, false);
  }
  console.log('ALL OK', boot.type);
})().catch((e) => { console.error(e); process.exit(1); });
