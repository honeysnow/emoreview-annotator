// Review screen. Final values are never typed: Use A, Use B, or Edit… (the structured
// editor in a dialog). All decisions are saved immediately via PUT /api/review/{sentence}/{field}.
(function () {
  "use strict";
  var boot = JSON.parse(document.getElementById("boot").textContent);
  var rows = boot.rows, FIELDS = boot.fields, L = boot.labels, A = boot.a, B = boot.b;
  var cur = Math.max(0, rows.findIndex(function (r) { return r.id === boot.current; }));
  var filter = "all", cueMode = "a", noteTimer = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (k === "style") e.style.cssText = v;
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v); else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function saving(on, err) { var n = $("savestate"); n.className = "savestate" + (err ? " err" : on ? " dirty" : ""); n.lastChild.textContent = err ? "Not saved: " + err : on ? "Saving…" : "Saved"; }
  function api(method, url, body) {
    saving(true);
    return fetch(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || r.status); return d; }); })
      .then(function (d) { saving(false); return d; })
      .catch(function (e) { saving(false, e.message); throw e; });
  }
  function replace(row) { var i = rows.findIndex(function (r) { return r.id === row.id; }); rows[i] = row; render(); }
  function R() { return rows[cur]; }

  // ---------------------------------------------------------------- actions
  function decide(field, value) { var r = R(); return api("PUT", "/api/review/" + r.id + "/" + field, { final_value: value, resolved: true }).then(replace).catch(function (e) { alert(e.message); }); }
  function useAll(side) { return api("POST", "/api/review/" + R().id + "/use/" + side).then(replace); }
  function select(i) { flushNote(); cur = i; history.replaceState(null, "", "/review/" + encodeURIComponent(boot.batch) + "/" + R().sent_id); render(); window.scrollTo(0, 0); }
  function nextOpen() {
    for (var s = 1; s <= rows.length; s++) { var i = (cur + s) % rows.length; if (rows[i].status === "open") { select(i); return true; } }
    return false;
  }
  function flushNote() { if (noteTimer) { clearTimeout(noteTimer); noteTimer = null; var r = R(), v = $("note").value; api("PUT", "/api/review/" + r.id + "/_note", { note: v, resolved: false }).then(function (d) { r.note = d.note; }); } }

  // ----------------------------------------------------------------- render
  function render() { renderQueue(); renderSentence(); renderFields(); renderRight(); }

  function visible(r) { return filter === "all" || (filter === "dis" && r.diff > 0) || (filter === "open" && r.status === "open"); }
  function renderQueue() {
    var f = clear($("filters"));
    [["all", "All"], ["dis", "Disagreements"], ["open", "Unresolved"]].forEach(function (x) {
      var n = rows.filter(function (r) { return x[0] === "all" || (x[0] === "dis" && r.diff > 0) || (x[0] === "open" && r.status === "open"); }).length;
      f.appendChild(el("button", { class: "chip" + (filter === x[0] ? " on" : ""), type: "button", style: "padding:5px 10px;font-size:12px", text: x[1] + " · " + n, onclick: function () { filter = x[0]; renderQueue(); } }));
    });
    var q = clear($("queue")), shown = 0;
    rows.forEach(function (r, i) {
      if (!visible(r) && i !== cur) return;
      shown++;
      var pill = r.status === "open" ? r.open + " open" : r.status;
      q.appendChild(el("button", { class: "qrow" + (i === cur ? " on" : ""), onclick: function () { select(i); } }, [
        el("span", { class: "meta" }, [el("span", { text: (i + 1) + " · " + r.sent_id }), el("span", { class: "pill " + r.status, text: pill })]),
        el("span", { class: "txt", text: r.text })]));
    });
    if (!shown) q.appendChild(el("div", { class: "muted", style: "padding:16px 20px", text: "No sentences match this filter." }));
    $("q-count").textContent = shown + " shown";
  }

  function renderSentence() {
    var r = R(), toks = r.text.split(" ");
    $("sent-label").textContent = "SENTENCE " + (cur + 1) + " OF " + rows.length + " · " + r.sent_id + " · " + r.doc;
    var hl = {};
    var spans = cueMode === "a" ? r.cues_a : cueMode === "b" ? r.cues_b : [];
    spans.forEach(function (s) { for (var i = s[0]; i <= s[1]; i++) hl[i] = true; });
    var t = clear($("tokens"));
    toks.forEach(function (w, i) { t.appendChild(el("span", { style: "padding:3px 4px;border-radius:6px;" + (hl[i] ? "background:#FFE9A8;box-shadow:inset 0 -2px 0 #C99700" : ""), text: w })); });
    var m = clear($("cue-modes")); m.appendChild(el("span", { class: "l", text: "Highlight cues from" }));
    [["a", A], ["b", B], ["off", "Off"]].forEach(function (x) { m.appendChild(el("button", { class: cueMode === x[0] ? "on" : "", type: "button", text: x[1], onclick: function () { cueMode = x[0]; renderSentence(); } })); });
  }

  function diffParts(s, other) {  // word-level diff: words missing from the other side are marked
    if (!s) return [el("span", { text: "—", style: "color:#9A968D" })];
    var set = {}; (other || "").split(" ").forEach(function (w) { set[w] = true; });
    var out = [];
    s.split(" ").forEach(function (w, i) { if (i) out.push(" "); out.push(other && !set[w] ? el("mark", { text: w }) : w); });
    return out;
  }
  function renderFields() {
    var r = R(); $("h-a").textContent = A; $("h-b").textContent = B;
    var box = clear($("fields"));
    FIELDS.forEach(function (f) {
      var a = r.a[f], b = r.b[f], dis = a !== b, fin = r.final[f], open = dis && !r.resolved[f];
      var pickA = dis && r.resolved[f] && fin === a, pickB = dis && r.resolved[f] && fin === b;
      var finCell = el("div", { class: "cell fin" + (fin ? "" : " empty"), text: fin ? fin : (open ? "Pick a value" : "—") });
      var kids = [el("span", { class: "lab", text: L[f] }),
        el("div", {}, [el("div", { class: "cell pick" + (pickA ? " on" : "") }, dis ? diffParts(a, b) : [a || "—"])]),
        el("div", {}, [el("div", { class: "cell pick" + (pickB ? " on" : "") }, dis ? diffParts(b, a) : [b || "—"])]),
        el("div", {}, [finCell].concat(dis ? [el("div", { class: "btns" }, [
          el("button", { class: "btn" + (pickA ? " on" : ""), type: "button", text: "Use A", onclick: function () { decide(f, a); } }),
          el("button", { class: "btn" + (pickB ? " on" : ""), type: "button", text: "Use B", onclick: function () { decide(f, b); } }),
          el("button", { class: "btn" + (r.resolved[f] && !pickA && !pickB ? " on" : ""), type: "button", text: "Edit…", onclick: function () { openEditor(f); } })])] : [])),
        el("span", {}, [el("span", { class: "mk " + (dis ? "x" : "ok"), title: dis ? "Differs (exported as X)" : "Match (exported as -)", text: dis ? "X" : "✓" })])];
      box.appendChild(el("div", { class: "rgrid " + (dis ? (open ? "open" : "dis") : "agree") }, kids));
    });
  }

  function renderRight() {
    var r = R(), fl = clear($("flags"));
    var flags = r.flags.slice();
    if (!r.diff) flags.push({ level: "ok", msg: "Both annotators agree on every field." });
    else if (!flags.length) flags.push({ level: "ok", msg: "No automatic issues. " + r.diff + " field" + (r.diff === 1 ? "" : "s") + " need a decision." });
    flags.forEach(function (x) { fl.appendChild(el("div", { class: x.level === "ok" ? "" : "warn" }, [el("b", { text: x.level === "ok" ? "✓" : "!" }), el("span", { text: x.msg })])); });
    var note = $("note"); if (document.activeElement !== note) note.value = r.note || "";
    var ag = clear($("agreement")), s = boot.summary;
    $("ag-mean").textContent = "mean " + s.mean.toFixed(0) + "% · " + (r.diff ? (r.diff - r.open) + " of " + r.diff + " resolved" : "nothing to resolve");
    FIELDS.forEach(function (f) {
      var p = s.agreement[f], low = p < 60;
      ag.appendChild(el("div", { class: "agrow" }, [el("div", { class: "top" }, [el("span", { text: L[f] }), el("span", { text: p.toFixed(0) + "%", style: "color:" + (low ? "#B45309" : "inherit") })]),
        el("div", { class: "b" }, [el("i", { style: "width:" + p + "%;background:" + (low ? "#B45309" : "#2F4BD8") })])]));
    });
  }

  // ------------------------------------------------------- structured Edit…
  var editField = null;
  function openEditor(field, source, overwrite) {
    editField = field; var r = R();
    $("modal-title").textContent = "Edit Final · " + L[field] + " · " + r.sent_id;
    $("seed-a").textContent = A; $("seed-b").textContent = B;
    api("POST", "/api/review/" + r.id + "/final/seed", { source: source || (r.a[field] || !r.b[field] ? "a" : "b"), overwrite: !!overwrite })
      .then(function (d) { $("modal-frame").src = d.url; $("modal").hidden = false; });
  }
  function closeEditor() { $("modal").hidden = true; $("modal-frame").src = "about:blank"; editField = null; }
  $("seed-a").onclick = function () { if (confirm("Replace the structured Final with " + A + "'s annotation?")) openEditor(editField, "a", true); };
  $("seed-b").onclick = function () { if (confirm("Replace the structured Final with " + B + "'s annotation?")) openEditor(editField, "b", true); };
  $("modal-cancel").onclick = closeEditor;
  $("modal-use").onclick = function () {
    var f = editField, w = $("modal-frame").contentWindow;
    Promise.resolve(w && w.__annotoolFlush ? w.__annotoolFlush() : null)
      .then(function () { return api("GET", "/api/review/" + R().id + "/final"); })
      .then(function (strings) { closeEditor(); return decide(f, strings[f] == null ? "" : strings[f]); });
  };

  // ----------------------------------------------------------------- footer
  $("prev").onclick = function () { if (cur > 0) select(cur - 1); };
  $("next-open").onclick = function () { if (!nextOpen()) alert("No unresolved disagreements."); };
  $("use-a").onclick = function () { if (R().open) useAll("a"); };
  $("resolve").onclick = function () {
    var r = R();
    if (r.open && !confirm(r.open + " field(s) are still unresolved. Move on anyway?")) return;
    if (!nextOpen() && cur < rows.length - 1) select(cur + 1);
    else if (!rows.some(function (x) { return x.status === "open"; })) alert("All disagreements are resolved.");
  };
  $("note").addEventListener("input", function () { clearTimeout(noteTimer); noteTimer = setTimeout(flushNote, 600); });
  window.addEventListener("pagehide", function () { if (noteTimer) { var r = R(); fetch("/api/review/" + r.id + "/_note", { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: $("note").value, resolved: false }) }); } });

  $("help-open").onclick = function () { $("help").hidden = false; };
  $("help-close").onclick = function () { $("help").hidden = true; };
  document.addEventListener("keydown", function (ev) {
    var t = ev.target;
    if ((t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === "?") { $("help").hidden = !$("help").hidden; return; }
    if (ev.key === "Escape") { $("help").hidden = true; return; }
    if (!$("modal").hidden) return;  // the Edit… dialog has its own controls
    if (ev.key === "ArrowLeft" && cur > 0) select(cur - 1);
    else if (ev.key === "ArrowRight" && cur < rows.length - 1) select(cur + 1);
    else if (ev.key === "n") { if (!nextOpen()) alert("No unresolved disagreements."); }
  });

  render();
})();
