// Headless smoke test of review.js (DOM shim, stubbed fetch). argv[2] = boot JSON from /review/...
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const { makeEnv } = require('./dom.js');
const boot = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const src = fs.readFileSync(path.resolve(__dirname, '../../src/annotool/static/review.js'), 'utf8');
const ids = ['boot', 'savestate', 'q-count', 'filters', 'queue', 'sent-label', 'cue-modes', 'tokens', 'h-a', 'h-b', 'fields', 'prev', 'next-open', 'use-a', 'resolve', 'flags', 'note', 'ag-mean', 'agreement', 'modal', 'modal-title', 'modal-frame', 'seed-a', 'seed-b', 'modal-cancel', 'modal-use', 'help', 'help-open', 'help-close'];
const env = makeEnv(ids); const { byId, document, all, byText } = env;
byId.boot.textContent = JSON.stringify(boot); byId.savestate.appendChild(new env.El('i')); byId.savestate.appendChild(new env.El('span'));
byId.modal.hidden = true;
const calls = [];
const fetch = (url, o) => {
  const body = o && o.body ? JSON.parse(o.body) : null; calls.push({ url, method: o && o.method, body });
  let out = {};
  const m = /\/api\/review\/(\d+)\/(.+)$/.exec(url);
  if (m && o.method === 'PUT') { // echo: the row with that field resolved to the chosen value
    const row = JSON.parse(JSON.stringify(boot.rows.find((r) => r.id === +m[1])));
    row.final[m[2]] = body.final_value; row.resolved[m[2]] = true; row.open = Math.max(0, row.open - 1); row.status = row.open ? 'open' : 'resolved'; out = row;
  } else if (/final\/seed/.test(url)) out = { url: '/annotate/x/y?annotator=FINAL&embed=1' };
  else if (/\/final$/.test(url)) out = Object.fromEntries(boot.fields.map((k) => [k, 'EDITED:' + k]));  // what the structured editor produced
  else if (/\/use\//.test(url)) { const row = JSON.parse(JSON.stringify(boot.rows.find((r) => r.id === +/review\/(\d+)/.exec(url)[1]))); row.open = 0; row.status = 'resolved'; out = row; }
  return Promise.resolve({ ok: true, json: () => Promise.resolve(out) });
};
byId.help.hidden = true;
const ctx = { document, window: { addEventListener() {}, scrollTo() {} }, history: { replaceState() {} }, location: {}, fetch, setTimeout, clearTimeout, confirm: () => true, alert() {}, console, JSON, Object, Math, Promise };
vm.createContext(ctx); vm.runInContext(src, ctx);
const tick = () => new Promise((r) => setTimeout(r, 5));
(async () => {
  assert.equal(byId['h-a'].textContent, boot.a); assert.equal(byId['h-b'].textContent, boot.b);
  assert.equal(byId.fields.children.length, 8, 'one grid row per field');
  assert(/^SENTENCE 1 OF 599/.test(byId['sent-label'].textContent));
  const cls = (i) => byId.fields.children[i].className;
  const filterBtns = byId.filters.children.map((b) => b.textContent);
  console.log('filters:', filterBtns.join(' | '));
  assert(/^All · 599/.test(filterBtns[0]));
  // filter to Unresolved: queue only lists open rows (+ current)
  byText(byId.filters, filterBtns[2]).fire('click');
  const nOpen = boot.rows.filter((r) => r.status === 'open').length;
  assert(byId.queue.children.length >= nOpen && byId.queue.children.length <= nOpen + 1);
  // jump to the next disagreement, then resolve one field with Use B
  byId['next-open'].fire('click');
  let idx = boot.rows.findIndex((r) => r.status === 'open');
  assert(new RegExp('^SENTENCE ' + (idx + 1) + ' OF').test(byId['sent-label'].textContent), 'next disagreement lands on first open row');
  const row = boot.rows[idx], f = boot.fields.find((k) => row.a[k] !== row.b[k] && row.final[k] === null);
  const gi = boot.fields.indexOf(f);
  assert(/open/.test(cls(gi)), 'open field is highlighted');
  const finCell = byId.fields.children[gi].children[3];
  const useB = all(finCell, (e) => e.tagName === 'BUTTON' && e.textContent === 'Use B')[0];
  useB.fire('click'); await tick();
  const put = calls.find((c) => c.method === 'PUT' && c.url.endsWith('/' + f));
  assert(put && put.body.final_value === row.b[f] && put.body.resolved === true, 'Use B sends B string');
  assert(!/open/.test(cls(gi)), 'field no longer open after resolving');
  // Final cell is read-only text (no inputs anywhere in the grid)
  assert.equal(all(byId.fields, (e) => e.tagName === 'INPUT' || e.tagName === 'TEXTAREA').length, 0, 'no free-text boxes in Final');
  // Edit… seeds the structured Final and opens the dialog
  const g2 = boot.fields.findIndex((k) => k !== f && row.a[k] !== row.b[k]);
  if (g2 >= 0) {
    const edit = all(byId.fields.children[g2].children[3], (e) => e.tagName === 'BUTTON' && e.textContent === 'Edit…')[0];
    edit.fire('click'); await tick();
    assert(calls.some((c) => /final\/seed/.test(c.url)), 'seed requested');
    assert.equal(byId.modal.hidden, false, 'dialog opens');
    assert(/embed=1/.test(byId['modal-frame'].src));
    byId['modal-use'].fire('click'); await tick(); await tick();
    const used = calls.filter((c) => c.method === 'PUT' && c.url.endsWith('/' + boot.fields[g2]));
    assert.equal(used.length, 1, 'one decision saved');
    assert.equal(used[0].body.final_value, 'EDITED:' + boot.fields[g2], 'Final = the structured editor output');
    assert.equal(byId.modal.hidden, true, 'dialog closes');
  }
  console.log('ALL OK, calls:', calls.length);
})().catch((e) => { console.error(e); process.exit(1); });
