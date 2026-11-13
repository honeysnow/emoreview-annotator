// Minimal DOM shim so the app's plain-JS screens can run under Node without a browser.
class Text { constructor(t) { this._t = t; } get textContent() { return this._t; } }
class El {
  constructor(t) { this.tagName = (t || '').toUpperCase(); this.children = []; this.style = { cssText: '' }; this.attrs = {}; this.listeners = {};
    this.hidden = false; this.className = ''; this._t = ''; this.dataset = {}; this.value = ''; this.src = '';
    this.classList = { toggle: (c, on) => { const s = new Set(this.className.split(' ').filter(Boolean)); on ? s.add(c) : s.delete(c); this.className = [...s].join(' '); } }; }
  get firstChild() { return this.children[0] || null; } get lastChild() { return this.children[this.children.length - 1] || null; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; } removeChild(c) { this.children.splice(this.children.indexOf(c), 1); }
  setAttribute(k, v) { this.attrs[k] = v; } addEventListener(e, f) { (this.listeners[e] = this.listeners[e] || []).push(f); }
  set textContent(v) { this.children = []; this._t = String(v); } get textContent() { return this._t + this.children.map((c) => c.textContent).join(''); }
  focus() {}
  fire(e, ev = {}) { const o = Object.assign({ target: this, stopPropagation() {} }, ev); (this.listeners[e] || []).forEach((f) => f(o)); if (this['on' + e]) this['on' + e](o); }
}
function makeEnv(ids) {
  const byId = {}; ids.forEach((i) => { byId[i] = new El('div'); });
  const docL = {};
  const document = { getElementById: (i) => byId[i], createElement: (t) => new El(t), createTextNode: (t) => new Text(t), body: new El('body'),
    activeElement: null, addEventListener(e, f) { (docL[e] = docL[e] || []).push(f); }, querySelectorAll: () => [] };
  const all = (e, f, acc = []) => { if (f(e)) acc.push(e); (e.children || []).forEach((c) => all(c, f, acc)); return acc; };
  const byText = (root, t) => all(root, (e) => e.tagName === 'BUTTON' && e.textContent === t)[0];
  return { byId, document, all, byText, El };
}
module.exports = { makeEnv, El };
