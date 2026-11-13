// Export page: annotations and comparison sheet. Preview and files both come from the server.
(function () {
  "use strict";
  var boot = JSON.parse(document.getElementById("boot").textContent);
  var type = boot.type;
  function $(id) { return document.getElementById(id); }
  function radio(name) { return document.querySelector("input[name=" + name + "]:checked").value; }
  function body(fmt) {
    if (type === "comparison") return { batch: $("review").value, scope: radio("cscope"), formulas: $("opt-formulas").checked,
      agreement_row: $("opt-agree").checked, reviewer_note: $("opt-note").checked, cue_prefix: $("opt-cprefix").checked, format: fmt || "xlsx" };
    var scope = radio("scope");
    return { batch: boot.batch, annotator: boot.annotator, scope: scope, document: scope === "document" ? $("document").value : null,
      include_unannotated: $("opt-unannotated").checked, cue_prefix: $("opt-prefix").checked, format: fmt || "xlsx" };
  }
  function url(suffix) { return (type === "comparison" ? "/api/export/comparison" : "/api/export/annotations") + (suffix || ""); }
  function post(u, b) { return fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }); }
  function td(tag, text, cls, span) { var e = document.createElement(tag); e.textContent = text; e.title = text; if (cls) e.className = cls; if (span) e.colSpan = span; return e; }
  var last = null;

  function setType(t) {
    type = t;
    $("type-ann").classList.toggle("on", t === "annotations"); $("type-cmp").classList.toggle("on", t === "comparison");
    $("panel-ann").hidden = t !== "annotations"; $("panel-cmp").hidden = t !== "comparison";
    $("foot").textContent = t === "comparison"
      ? "“-” = exact match, “X” = differs. Final is blank until the reviewer resolves the disagreement. Every export is also saved under data/exports/."
      : "Every export is also saved under data/exports/ with a timestamp.";
    var u = new URL(location.href); u.searchParams.set("type", t); history.replaceState(null, "", u);
    refresh();
  }
  function render(d) {
    $("filename").textContent = d.filename;
    var t = $("pv"); t.textContent = "";
    if (type === "comparison") {
      var g = document.createElement("tr"); g.appendChild(td("th", "", "", 3));
      var ps = ["Polarity", "Arousal", "Emotion Tag", "Emotion Cues", "Emotion Source", "Emotion Cause Object", "Emotion Cause Action", "Emotion Experiencer"];
      ps.forEach(function (p) { var pct = d.agreement[p]; g.appendChild(td("th", p + (pct != null ? " · " + pct.toFixed(0) + "% agree" : ""), "grp", 4)); });
      t.appendChild(g);
      $("summary").textContent = d.total + " of " + d.all + " rows · " + d.headers.length + " columns";
    } else {
      $("summary").textContent = d.total + " rows · " + d.headers.length + " columns";
    }
    var tr = document.createElement("tr");
    d.headers.forEach(function (h, i) { tr.appendChild(td("th", (type === "comparison" && d.labels[i] ? d.labels[i] + " · " : "") + h)); });
    t.appendChild(tr);
    d.rows.forEach(function (r) {
      var row = document.createElement("tr");
      r.forEach(function (v, i) {
        var cls = type === "comparison" && i >= 3 && (i - 3) % 4 === 3 ? (v === "X" ? "mx" : "mo") : "";
        row.appendChild(td("td", v, cls));
      });
      t.appendChild(row);
    });
    $("more").textContent = d.total > d.rows.length ? "… and " + (d.total - d.rows.length) + " more rows" : (d.total ? "" : "No rows match.");
    var w = $("warnings");
    if (type === "comparison") { w.hidden = true; return; }
    w.hidden = !d.warnings.length || !$("opt-warn").checked; w.textContent = "";
    if (!w.hidden) w.textContent = d.warnings.length + " incomplete row(s): " + d.warnings.slice(0, 8).join(" | ") + (d.warnings.length > 8 ? " …" : "");
  }
  function refresh() {
    post(url("/preview"), body()).then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); }).then(function (x) {
      if (!x[0]) { $("status").textContent = x[1].detail || "Error"; return; }
      last = x[1]; $("status").textContent = ""; render(last);
      if (type === "comparison") { $("c-all").textContent = "All sentences · " + last.all; }
    });
  }
  function download(fmt) {
    if (type === "annotations" && $("opt-warn").checked && last && last.warnings.length && !confirm(last.warnings.length + " row(s) are incomplete (e.g. " + last.warnings[0] + ").\n\nDownload anyway?")) return;
    $("status").textContent = "Preparing…";
    post(url(), body(fmt)).then(function (r) {
      if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || r.status); });
      var cd = r.headers.get("Content-Disposition") || "", m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(cd);
      return r.blob().then(function (b) { return [b, m ? decodeURIComponent(m[1]) : "export." + fmt]; });
    }).then(function (x) {
      var a = document.createElement("a"); a.href = URL.createObjectURL(x[0]); a.download = x[1]; document.body.appendChild(a); a.click(); a.remove();
      $("status").textContent = "Downloaded " + x[1] + " (also saved in data/exports/)";
    }).catch(function (e) { $("status").textContent = "Export failed: " + e.message; });
  }
  if (boot.reviews.length) {
    var info = function () { var r = boot.reviews.filter(function (x) { return x.name === $("review").value; })[0]; if (r) { $("c-dis").textContent = "Disagreements only · " + r.diff; $("c-open").textContent = "Unresolved only · " + r.open; } };
    $("review").addEventListener("change", function () { info(); refresh(); }); info();
  }
  document.querySelectorAll("input,select").forEach(function (n) { n.addEventListener("change", refresh); });
  $("type-ann").onclick = function () { setType("annotations"); };
  $("type-cmp").onclick = function () { setType("comparison"); };
  $("dl-xlsx").onclick = function () { download("xlsx"); };
  $("dl-csv").onclick = function () { download("csv"); };
  setType(type);
})();
