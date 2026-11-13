// Annotate screen. Vanilla JS, no dependencies. State is held per sentence and
// autosaved (debounced 300 ms) via PUT /api/annotations/{sentence}/{annotator}.
// The export preview, checks and import flags always come from the server so
// they can never disagree with the exported file.
(function () {
  "use strict";
  var boot = JSON.parse(document.getElementById("boot").textContent);
  var CB = boot.codebook;
  var TAGS = {}; CB.tags.forEach(function (t) { TAGS[t.name] = t; });

  var SLOTS = [
    { k: "cue", prop: "cues", label: "Emotion cues", hint: "Words that signal the emotion", bg: "#FFE9A8", dot: "#C99700" },
    { k: "obj", prop: "objects", label: "Cause object", hint: "What triggers the emotion", bg: "#CFE3FF", dot: "#3B7DE8" },
    { k: "act", prop: "actions", label: "Cause action", hint: "What happens to / with it", bg: "#E3D5FB", dot: "#7C4DDB" },
    { k: "exp", prop: "experiencers", label: "Experiencer", hint: "Who feels the emotion", bg: "#CDEFD9", dot: "#2E9E5B" }
  ];
  var PREVIEW = [["polarity", "Polarity"], ["arousal", "Arousal"], ["tag", "Emotion Tag"], ["cues", "Emotion Cues"],
    ["source", "Emotion Source"], ["object", "Emotion Cause Object"], ["action", "Emotion Cause Action"], ["experiencer", "Emotion Experiencer"]];
  var POLCLS = { positive: "pos", negative: "neg", neutral: "neu", none: "non" };
  var POLFG = { positive: "#0F766E", negative: "#B45309", neutral: "#4B5563", none: "#6B675F" };
  var AROSHORT = ["very low", "low", "moderate", "high", "very high"];

  var S = boot.sentences;
  S.forEach(function (s) { s.annotation.emotions.forEach(anchorsInit); s.tokens = s.text.split(" "); s.ack = []; s.ver = 0; s.sentVer = 0; s.timer = null; s.inflight = null; });
  var cur = Math.max(0, S.findIndex(function (s) { return s.id === boot.current; }));
  var emo = 0, slot = "cue", tagOpen = false, hover = null, suggesting = false;

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v;
      else if (k === "style") e.style.cssText = v; else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function A() { return S[cur].annotation; }
  function E() { return A().emotions[emo] || null; }
  function isS2(tag) { return !!(TAGS[tag] && TAGS[tag].section2); }
  function eligible(k) { var e = E(); return k === "cue" || !!(e && isS2(e.tag)); }
  function status() { var p = A().polarity; return !p.length ? "todo" : (p.indexOf("none") >= 0 ? "none" : "done"); }
  function textOf(sp) { return S[cur].tokens.slice(sp.start, sp.end + 1).join(" "); }

  // ------------------------------------------------------------------ spans
  // Spans are always contiguous token ranges (decision 6).
  function spanAt(arr, i) { for (var j = 0; j < arr.length; j++) if (i >= arr[j].start && i <= arr[j].end) return j; return -1; }
  function addSpan(arr, start, end) {
    var lo = start, hi = end;
    for (var j = arr.length - 1; j >= 0; j--) {
      if (arr[j].start <= hi && arr[j].end >= lo) { lo = Math.min(lo, arr[j].start); hi = Math.max(hi, arr[j].end); arr.splice(j, 1); }
    }
    arr.push({ start: lo, end: hi, group: 0 });
    arr.sort(function (a, b) { return a.start - b.start; });
  }
  function clickToken(i, shift) {
    var e = E(); if (!e || !eligible(slot)) return;
    var arr = e[SLOTS.filter(function (s) { return s.k === slot; })[0].prop];
    if (shift && arr.length) {
      var near = arr[0], nd = 1e9;
      arr.forEach(function (s) { var d = i < s.start ? s.start - i : (i > s.end ? i - s.end : 0); if (d < nd) { nd = d; near = s; } });
      var lo = Math.min(near.start, i), hi = Math.max(near.end, i);
      arr.splice(arr.indexOf(near), 1); addSpan(arr, lo, hi);
    } else {
      var at = spanAt(arr, i);
      if (at >= 0) arr.splice(at, 1); else addSpan(arr, i, i);
    }
    afterSpanEdit(e);
  }
  function dragSpan(a, b) {
    var e = E(); if (!e || !eligible(slot)) return;
    var arr = e[SLOTS.filter(function (s) { return s.k === slot; })[0].prop];
    addSpan(arr, Math.min(a, b), Math.max(a, b));
    afterSpanEdit(e);
  }
  // Action -> object grouping. An action belongs to the object it was last assigned to (its anchor);
  // a new action defaults to the nearest object on its left. Objects are numbered left to right.
  function anchorsInit(e) {
    e.objects.sort(function (x, y) { return x.start - y.start; });
    e.actions.forEach(function (a) { var o = e.objects[a.group]; if (o) a.anchor = o.start; });
  }
  function regroup(e) {
    e.objects.sort(function (x, y) { return x.start - y.start; });
    e.objects.forEach(function (o, i) { o.group = i; });
    e.actions.sort(function (x, y) { return x.start - y.start; });
    e.actions.forEach(function (a, idx) {
      if (!e.objects.length) { a.group = idx; delete a.anchor; return; }
      var gi = -1; e.objects.forEach(function (o, i) { if (o.start === a.anchor) gi = i; });
      if (gi < 0) { gi = 0; e.objects.forEach(function (o, i) { if (o.start <= a.start) gi = i; }); }
      a.group = gi; a.anchor = e.objects[gi].start;
    });
  }
  function afterSpanEdit(e) { if (slot === "obj" || slot === "act") regroup(e); changed(); }

  // --------------------------------------------------------------- saving
  function setSaveState(cls, text) { var n = $("savestate"); n.className = "savestate " + cls; n.lastChild.textContent = text; }
  function changed() { var s = S[cur]; s.ver++; setSaveState("dirty", "Saving…"); clearTimeout(s.timer); s.timer = setTimeout(function () { save(s); }, 300); render(); }
  function payload(s) {
    var a = s.annotation;
    return { polarity: a.polarity, arousal: a.arousal, source: a.source, emotions: a.emotions, acknowledged: s.ack };
  }
  function save(s, keepalive) {
    clearTimeout(s.timer); s.timer = null;
    var v = s.ver;
    s.sentVer = v;
    var p = fetch("/api/annotations/" + s.id + "/" + encodeURIComponent(boot.annotator), {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(s)), keepalive: !!keepalive
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || r.status); });
      return r.json();
    }).then(function (d) {
      s.failed = false; s.retries = 0;
      if (s.ver === v) {  // ignore stale responses; keep local edits authoritative
        ["status", "preview", "checks", "has_errors", "attention"].forEach(function (k) { s[k] = d[k]; });
        if (!anyPending()) setSaveState("", "Saved"); render();
      }
    }).catch(function (err) {
      s.failed = true; s.retries = (s.retries || 0) + 1;
      if (s.retries <= 5) { setSaveState("err", "Not saved (" + err.message + ") · retrying…"); if (s.ver === v) setTimeout(function () { if (s.ver === v && s.failed) save(s); }, 3000 * s.retries); }
      else setSaveState("err", "Not saved: " + err.message + ". Your changes are still on this page; check the server, then make any edit to retry.");
    }).then(function () { /* settled */ });
    s.inflight = p;
    return p;
  }
  function anyPending() { return S.some(function (s) { return s.timer; }); }
  function flush() {
    var ps = S.map(function (s) { return s.timer ? save(s) : s.inflight; }).filter(Boolean);
    return Promise.all(ps);
  }
  window.addEventListener("beforeunload", function (ev) {  // never lose edits that have not reached the server
    if (S.some(function (s) { return s.timer || s.failed; })) { ev.preventDefault(); ev.returnValue = ""; }
  });
  window.__annotoolFlush = flush;  // used by the Review screen's Edit… dialog
  window.addEventListener("pagehide", function () { S.forEach(function (s) { if (s.timer) save(s, true); }); });

  // ------------------------------------------------------------ navigation
  function url(s) { return "/annotate/" + encodeURIComponent(boot.batch) + "/" + s.sent_id + "?annotator=" + encodeURIComponent(boot.annotator); }
  function go(i) {
    flush().then(function () {
      if (i < 0) { if (boot.prev_doc) location.href = boot.prev_doc; return; }
      if (i >= S.length) { if (boot.next_doc) location.href = boot.next_doc; else alert("End of the batch."); return; }
      cur = i; emo = 0; slot = "cue"; hover = null; suggesting = false;
      tagOpen = !!(E() && !E().tag);
      history.replaceState(null, "", url(S[cur]));
      render();
      window.scrollTo(0, 0);
    });
  }
  function saveAndNext() {
    flush().then(function () {
      var s = S[cur];
      if (s.has_errors) {
        var bad = s.checks.filter(function (c) { return c.level === "error"; }).map(function (c) { return "• " + c.msg; }).join("\n");
        if (!confirm("This sentence has issues:\n" + bad + "\n\nContinue anyway?")) return;
      }
      go(cur + 1);
    });
  }

  // ---------------------------------------------------------------- render
  function render() { renderList(); renderSentence(); renderControls(); renderEmotions(); renderRight(); }

  function polText(a) { return a.polarity.map(function (x) { return x.slice(0, 3); }).join(" · "); }
  function renderList() {
    var box = clear($("sentence-list")), done = 0;
    S.forEach(function (s, i) {
      var st = s.annotation.polarity.length ? (s.annotation.polarity.indexOf("none") >= 0 ? "none" : "done") : "todo";
      if (st !== "todo") done++;
      var p0 = s.annotation.polarity[0];
      box.appendChild(el("button", { class: "srow" + (i === cur ? " on" : ""), onclick: function () { go(i); } }, [
        el("span", { class: "dot " + st + (s.attention && s.attention.length ? " flag" : ""), title: (s.attention && s.attention.length ? "Imported data needs checking · " : "") + (st === "done" ? "Annotated" : st === "none" ? "No emotion" : "To do") }),
        el("span", { style: "flex:1;min-width:0" }, [
          el("span", { class: "meta" }, [el("span", { text: (i + 1) + " · " + s.sent_id }), el("span", { text: polText(s.annotation), style: "font-weight:500;color:" + (p0 ? POLFG[p0] : "#6B675F") })]),
          el("span", { class: "txt", text: s.text })])
      ]));
    });
    $("done-count").textContent = done + " of " + S.length + " done";
    $("done-bar").style.width = Math.round(100 * done / S.length) + "%";
    $("done-bar").style.cssText += ";display:block;height:100%;background:var(--accent)";
  }

  var dragFrom = null, suppress = false;
  function renderSentence() {
    var s = S[cur], e = E(), box = clear($("tokens"));
    $("sent-label").textContent = "SENTENCE " + (cur + 1) + " OF " + S.length + " · " + s.sent_id;
    var def = SLOTS.filter(function (x) { return x.k === slot; })[0];
    s.tokens.forEach(function (w, i) {
      var style = "";
      if (e && eligible(slot) && spanAt(e[def.prop], i) >= 0) style = "background:" + def.bg + ";box-shadow:inset 0 -2px 0 " + def.dot + ";font-weight:500";
      else if (e) {
        SLOTS.forEach(function (o) { if (!style && o.k !== slot && isS2Slot(e, o) && spanAt(e[o.prop], i) >= 0) style = "background:" + o.bg + ";opacity:.7;box-shadow:inset 0 -2px 0 " + o.dot; });
      }
      box.appendChild(el("button", {
        class: "tok", style: style, text: w, type: "button",
        onmousedown: function () { dragFrom = i; },
        onmouseup: function () { if (dragFrom !== null && dragFrom !== i) { suppress = true; var f = dragFrom; dragFrom = null; dragSpan(f, i); } dragFrom = null; },
        onclick: function (ev) { if (suppress) { suppress = false; return; } clickToken(i, ev.shiftKey); }
      }));
    });
    var lg = clear($("legend"));
    SLOTS.forEach(function (x) { lg.appendChild(el("span", {}, [el("i", { style: "background:" + x.bg + ";box-shadow:inset 0 -2px 0 " + x.dot }), x.label])); });
  }
  function isS2Slot(e, o) { return o.k === "cue" || isS2(e.tag); }
  document.addEventListener("mouseup", function () { dragFrom = null; });

  function chip(label, on, cls, fn, extra) {
    return el("button", Object.assign({ class: "chip " + (cls || "") + (on ? " on" : ""), type: "button", "aria-pressed": on ? "true" : "false", onclick: fn, text: label }, extra || {}));
  }
  function renderControls() {
    var a = A(), st = status();
    var pol = clear($("pol"));
    CB.polarities.forEach(function (k) {
      pol.appendChild(chip(k, a.polarity.indexOf(k) >= 0, POLCLS[k], function () {
        var on = a.polarity.indexOf(k) >= 0;
        if (k === "none") {
          if (on) a.polarity = []; else { a.polarity = ["none"]; a.emotions = []; a.arousal = null; a.source = null; emo = 0; tagOpen = false; }
        } else {
          a.polarity = a.polarity.filter(function (x) { return x !== "none"; });
          var at = a.polarity.indexOf(k);
          if (at >= 0) a.polarity.splice(at, 1); else a.polarity.push(k);
          if (a.polarity.length && !a.emotions.length) { a.emotions.push(newEmotion()); emo = 0; tagOpen = true; }
        }
        changed();
      }));
    });
    var aro = clear($("aro"));
    for (var n = 1; n <= 5; n++) (function (n) {
      aro.appendChild(el("button", { class: "chip ar" + (a.arousal === n ? " on" : ""), type: "button", title: CB.arousal[n], onclick: function () { a.arousal = n; changed(); }, onmouseenter: function () { $("aro-hint").textContent = CB.arousal[n]; }, onmouseleave: aroHint }, [el("b", { text: String(n) }), el("span", { text: AROSHORT[n - 1] })]));
    })(n);
    aroHint();
    var src = clear($("src"));
    CB.sources.forEach(function (k) { src.appendChild(chip(k, a.source === k, "", function () { a.source = k; changed(); })); });
    document.querySelectorAll(".rest").forEach(function (n) { n.classList.toggle("off", st === "none"); });
    $("none-note").hidden = st !== "none";
  }
  function aroHint() { var a = A(); $("aro-hint").textContent = a.arousal ? CB.arousal[a.arousal] : "Hover a level for examples from the codebook."; }
  function newEmotion() { return { tag: "", cues: [], objects: [], actions: [], experiencers: [] }; }

  function legacyFor(tag) {
    return (S[cur].attention || []).filter(function (x) { return x.reason === "outside_section2" && x.detail.indexOf(tag + ":") === 0; });
  }
  function renderEmotions() {
    var a = A(), st = status(), e = E();
    var tabs = clear($("emo-tabs"));
    a.emotions.forEach(function (m, i) {
      var warn = m.tag && !isS2(m.tag) && legacyFor(m.tag).length > 0;
      tabs.appendChild(el("button", { class: "etab" + (i === emo ? " on" : "") + (warn ? " warn" : ""), type: "button", title: warn ? "Has legacy values outside Section 2: please check" : "", onclick: function () { emo = i; tagOpen = !m.tag; suggesting = false; render(); } }, [
        (warn ? "⚠ " : "") + (i + 1) + " · " + (m.tag || "new emotion"),
        el("span", { class: "x", role: "button", "aria-label": "Remove emotion", text: "×", onclick: function (ev) { ev.stopPropagation(); a.emotions.splice(i, 1); emo = Math.max(0, Math.min(emo, a.emotions.length - 1)); changed(); } })]));
    });
    if (a.polarity.length && st !== "none") tabs.appendChild(el("button", { class: "etab add", type: "button", text: "+ Add emotion", onclick: function () { a.emotions.push(newEmotion()); emo = a.emotions.length - 1; tagOpen = true; render(); } }));
    $("emo-empty").hidden = !(st === "todo" || (st === "done" && !a.emotions.length));
    $("emo-empty").textContent = st === "todo" ? "Choose a polarity above to start adding emotions." : "Add an emotion to continue.";
    $("emo-body").hidden = !e || st === "none";
    if (!e || st === "none") return;
    $("tag-name").textContent = e.tag || "Choose a tag";
    $("tag-toggle").textContent = tagOpen ? "Close" : "Change";
    $("tag-toggle").onclick = function () { tagOpen = !tagOpen; hover = null; renderEmotions(); renderRight(); };

    var pk = $("tag-picker"); pk.hidden = !tagOpen; clear(pk);
    if (tagOpen) {
      CB.tags.forEach(function (t) {
        pk.appendChild(el("button", { class: "chip" + (e.tag === t.name ? " on" : "") + (t.section2 ? " s2" : ""), type: "button", text: t.name,
          title: t.section2 ? "Section 2 emotion: cause and experiencer apply" : "",
          onmouseenter: function () { hover = t.name; renderRight(); }, onmouseleave: function () { hover = null; renderRight(); },
          onclick: function () {
            e.tag = t.name; if (!t.section2) { e.objects = []; e.actions = []; e.experiencers = []; slot = "cue"; }
            tagOpen = false; hover = null; changed();
          } }));
      });
      if (suggesting) {
        var inp = el("input", { type: "text", placeholder: "new tag", maxlength: "60" });
        var ok = function () { var v = inp.value.trim().toLowerCase(); if (!v) return; e.tag = v; e.objects = []; e.actions = []; e.experiencers = []; slot = "cue"; tagOpen = false; suggesting = false; changed(); };
        inp.addEventListener("keydown", function (ev) { ev.stopPropagation(); if (ev.key === "Enter") ok(); });
        pk.appendChild(inp); pk.appendChild(el("button", { class: "chip", type: "button", text: "Add (flagged for review)", onclick: ok }));
        setTimeout(function () { inp.focus(); }, 0);
      } else pk.appendChild(el("button", { class: "chip suggest", type: "button", text: "Suggest new tag…", onclick: function () { suggesting = true; renderEmotions(); } }));
    }

    var ln = $("legacy-note"), lg = e.tag && !isS2(e.tag) ? legacyFor(e.tag) : [];
    ln.hidden = !lg.length; ln.textContent = "";
    if (lg.length) ln.textContent = "⚠ “" + e.tag + "” is outside Section 2. Legacy values were kept but are not exported (cause/experiencer export as “none”): " + lg.map(function (x) { return x.field + " → " + x.detail.slice(e.tag.length + 1).trim(); }).join("; ") + ". Please check with the annotator.";

    var sl = clear($("slots"));
    SLOTS.forEach(function (d) {
      var ok = eligible(d.k), active = slot === d.k;
      var runs = el("div", { class: "runs" });
      if (ok) e[d.prop].forEach(function (sp, idx) {
        var chipKids = [textOf(sp)];
        if (d.k === "act" && e.objects.length >= 2) {  // which object does this action belong to?
          var sel = el("select", { title: "This action belongs to object…", "aria-label": "Belongs to object", onclick: function (ev) { ev.stopPropagation(); },
            onchange: function (ev) { var g = +ev.target.value; sp.group = g; sp.anchor = e.objects[g].start; changed(); } });
          e.objects.forEach(function (o, i) { var op = el("option", { value: String(i), text: "→ " + (i + 1) + " · " + textOf(o).slice(0, 18) }); if (i === sp.group) op.setAttribute("selected", ""); sel.appendChild(op); });
          sel.value = String(sp.group);
          chipKids.push(sel);
        }
        chipKids.push(el("button", { type: "button", "aria-label": "Remove", text: "×", onclick: function (ev) { ev.stopPropagation(); e[d.prop].splice(idx, 1); afterSpanEdit(e); } }));
        runs.appendChild(el("span", { class: "run", style: "background:" + d.bg }, chipKids));
      });
      if (!ok || !e[d.prop].length) runs.appendChild(el("span", { class: "empty", text: !ok ? (e.tag ? "Not applicable for " + e.tag + " (Section 2 emotions only)" : "Choose a tag first") : d.k === "cue" ? (active ? "Click words in the sentence…" : "No cues yet") : (active ? "Click words in the sentence · leave empty for “none”" : "none") }));
      sl.appendChild(el("div", { class: "slot" + (active ? " on" : "") + (ok ? "" : " locked"), style: active ? "box-shadow:inset 3px 0 0 " + d.dot : "", onclick: function () { if (ok) { slot = d.k; render(); } } }, [
        el("div", { class: "lab" }, [el("b", {}, [el("i", { style: "background:" + d.bg }), d.label]), el("small", { text: d.hint })]), runs]));
    });
  }

  function renderRight() {
    var s = S[cur];
    $("prev-label").textContent = boot.annotator + "_" + boot.batch + " row";
    var pv = clear($("preview")), prev = s.preview || {};
    PREVIEW.forEach(function (f) { var v = prev[f[0]]; pv.appendChild(el("div", {}, [el("div", { class: "k", text: f[1] }), el("div", { class: "v" + (v ? "" : " e"), text: v || "—" })])); });
    var ck = clear($("checks"));
    (s.checks || []).forEach(function (c) { ck.appendChild(el("div", { class: c.level }, [el("b", { text: c.level === "ok" ? "✓" : c.level === "na" ? "–" : "!" }), el("span", { text: c.msg })])); });
    var at = s.attention || [], ap = $("attn-panel"), ab = clear($("attn"));
    ap.hidden = !at.length;
    at.forEach(function (x) {
      ab.appendChild(el("div", { class: "aitem" }, [el("b", { text: x.field }), " — " + x.text + (x.detail ? ": " + x.detail : ""),
        x.raw ? el("code", { text: "Legacy cell: " + x.raw }) : null,
        el("button", { class: "btn sm", type: "button", text: "Mark as checked", onclick: function () {
          if (s.ack.indexOf(x.field) < 0) s.ack.push(x.field);
          s.attention = s.attention.filter(function (y) { return y.field !== x.field; });
          changed();
        } })]));
    });
    var name = hover || (E() && E().tag) || null;
    $("cb-tag").textContent = name || "—";
    $("cb-def").textContent = name ? (TAGS[name] ? TAGS[name].definition : "Suggested tag — not in the codebook; flagged for review.") : "Select an emotion tag to see its codebook definition.";
  }

  // ------------------------------------------------------------- footer/keys
  $("help-open").onclick = function () { $("help").hidden = false; };
  $("help-close").onclick = function () { $("help").hidden = true; };
  $("prev").onclick = function () { go(cur - 1); };
  $("next").onclick = saveAndNext;
  $("mark-none").onclick = function () { var a = A(); a.polarity = ["none"]; a.emotions = []; a.arousal = null; a.source = null; emo = 0; tagOpen = false; changed(); };
  document.addEventListener("keydown", function (ev) {
    var t = ev.target, typing = t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
    if (typing || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === "?" || (ev.key === "Escape" && !$("help").hidden)) { $("help").hidden = ev.key === "Escape" ? true : !$("help").hidden; return; }
    if (boot.embed && /^(ArrowLeft|ArrowRight|Enter)$/.test(ev.key)) return;  // no navigation inside the dialog
    if (/^[1-5]$/.test(ev.key) && status() !== "none") { A().arousal = +ev.key; changed(); }
    else if (ev.key === "ArrowLeft") { go(cur - 1); }
    else if (ev.key === "ArrowRight") { go(cur + 1); }
    else if (ev.key === "Enter" && t === document.body) { saveAndNext(); }
    else if (ev.key === "Tab" && t === document.body && E()) {
      var order = SLOTS.map(function (x) { return x.k; }).filter(eligible), i = order.indexOf(slot);
      slot = order[(i + (ev.shiftKey ? order.length - 1 : 1)) % order.length]; ev.preventDefault(); render();
    }
  });

  tagOpen = !!(E() && !E().tag);
  render();
})();
