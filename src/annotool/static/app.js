// Home page: file import + rescan. Plain JS, no dependencies.
(function () {
  // User menu: switching user never moves data; renaming is a separate, explicit action.
  var rn = document.getElementById("rename-annotator");
  function goAs(code) { var u = new URL(location.href); u.searchParams.set("annotator", code); location.href = u; }
  async function fail(res, msg) { var b = await res.json().catch(function () { return {}; }); alert(b.detail || msg); }
  if (rn) {
    var menu = document.createElement("div"); menu.className = "usermenu"; menu.hidden = true;
    document.body.appendChild(menu);
    function item(text, fn, cls) { var b = document.createElement("button"); b.type = "button"; b.textContent = text; if (cls) b.className = cls; b.addEventListener("click", fn); menu.appendChild(b); }
    rn.addEventListener("click", async function (ev) {
      ev.stopPropagation();
      if (!menu.hidden) { menu.hidden = true; return; }
      menu.textContent = "";
      var list = await (await fetch("/api/annotators")).json();
      var h = document.createElement("div"); h.className = "hd"; h.textContent = "Working as"; menu.appendChild(h);
      list.forEach(function (a) { item((a.code === rn.dataset.code ? "✓ " : "   ") + a.code, function () { goAs(a.code); }); });
      item("+ New annotator…", async function () {
        var code = prompt("Code for the new annotator (2-8 letters/digits). Their work is kept separate:");
        if (!code) return;
        var fd = new FormData(); fd.append("code", code);
        var res = await fetch("/api/annotators", { method: "POST", body: fd });
        if (!res.ok) return fail(res, "Could not add annotator"); goAs((await res.json()).code);
      });
      var sep = document.createElement("div"); sep.className = "sep"; menu.appendChild(sep);
      item("Rename " + rn.dataset.code + "… (moves its data to the new code)", async function () {
        var next = prompt("New code for " + rn.dataset.code + ". All of its annotations move to the new code and export file names change:", rn.dataset.code);
        if (!next || next.trim().toUpperCase() === rn.dataset.code) return;
        var fd = new FormData(); fd.append("new_code", next);
        var res = await fetch("/api/annotators/" + encodeURIComponent(rn.dataset.code), { method: "PATCH", body: fd });
        if (!res.ok) return fail(res, "Could not rename"); goAs((await res.json()).code);
      }, "danger");
      var r = rn.getBoundingClientRect(); menu.style.top = (r.bottom + window.scrollY + 6) + "px"; menu.style.right = (document.documentElement.clientWidth - r.right) + "px";
      menu.hidden = false;
    });
    document.addEventListener("click", function () { menu.hidden = true; });
  }
  // Settings page
  var addBtn = document.getElementById("add-annotator");
  if (addBtn) addBtn.addEventListener("click", async function () {
    var code = prompt("Code for the new annotator (2-8 letters/digits):");
    if (!code) return;
    var fd = new FormData(); fd.append("code", code);
    var res = await fetch("/api/annotators", { method: "POST", body: fd });
    if (!res.ok) return fail(res, "Could not add annotator"); location.reload();
  });
  document.querySelectorAll(".rename-btn").forEach(function (b) {
    b.addEventListener("click", async function () {
      var next = prompt("New code for " + b.dataset.code + ". All of its annotations move to the new code and export file names change:", b.dataset.code);
      if (!next || next.trim().toUpperCase() === b.dataset.code) return;
      var fd = new FormData(); fd.append("new_code", next);
      var res = await fetch("/api/annotators/" + encodeURIComponent(b.dataset.code), { method: "PATCH", body: fd });
      if (!res.ok) return fail(res, "Could not rename"); location.reload();
    });
  });

  var flash = document.getElementById("flash");
  if (!flash) return;

  // Remove a batch (and everything stored for it). Input files in data/ are never touched.
  document.querySelectorAll("[data-remove-batch]").forEach(function (b) {
    b.addEventListener("click", async function () {
      var name = b.dataset.removeBatch;
      if (!confirm("Remove " + name + " from the app?\n\nThis deletes " + b.dataset.count + " stored for it. Your .xlsx files in data/ are not touched, so Rescan can bring the batch back (without that work).")) return;
      var res = await fetch("/api/batches/" + encodeURIComponent(name) + "?confirm=" + encodeURIComponent(name), { method: "DELETE" });
      if (!res.ok) return alert((await res.json().catch(function () { return {}; })).detail || "Could not remove");
      location.reload();
    });
  });

  // Start a review from two annotators' work on one batch
  var rvBatch = document.getElementById("rv-batch");
  if (rvBatch) {
    var ra = document.getElementById("rv-a"), rb = document.getElementById("rv-b");
    var fill = function () {
      var people = rvBatch.selectedOptions[0].dataset.people.split(",");
      [ra, rb].forEach(function (sel, k) { sel.textContent = ""; people.forEach(function (p) { var o = document.createElement("option"); o.value = o.textContent = p; sel.appendChild(o); }); sel.selectedIndex = Math.min(k, people.length - 1); });
    };
    rvBatch.addEventListener("change", fill); fill();
    document.getElementById("rv-go").addEventListener("click", async function () {
      var res = await fetch("/api/reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ batch: rvBatch.value, a: ra.value, b: rb.value }) });
      var body = await res.json().catch(function () { return {}; });
      if (!res.ok) return alert(body.detail || "Could not start the review");
      location.href = body.url;
    });
  }

  function show(parts, isErr) {
    flash.className = "flash" + (isErr ? " err" : "");
    flash.textContent = "";
    parts.forEach(function (p) {
      if (typeof p === "string") { var d = document.createElement("div"); d.textContent = p; flash.appendChild(d); }
      else { var ul = document.createElement("ul"); p.forEach(function (t) { var li = document.createElement("li"); li.textContent = t; ul.appendChild(li); }); flash.appendChild(ul); }
    });
    flash.hidden = false;
  }

  function describe(r) {
    if (r.skipped) return r.source_file + ": skipped (" + r.skipped + ")";
    var s = r.source_file + ": " + r.rows_imported + " of " + r.rows_total + " rows imported";
    if (r.kind === "comparison") s += " (" + r.annotator + "), " + r.rows_flagged + " flagged, " + r.decisions_imported + " typed Final values kept";
    if (r.kind === "annotated") {
      s += ", " + r.rows_flagged + " flagged, " + r.auto_corrected_values + " values auto-corrected, " + r.unmatched + " unmatched";
      if (r.outside_section2_rows) s += ", " + r.outside_section2_rows + " rows with values outside Section 2 (kept, not exported)";
    }
    return s;
  }

  function kindOf(name) {
    if (/comparison/i.test(name)) return "comparison";
    return /^[A-Z]{2,5}_TRAIN/.test(name) ? "annotated" : "raw";
  }

  function finish(lines, errors) {
    try { sessionStorage.setItem("flash", JSON.stringify({ lines: lines, errors: errors })); } catch (e) {}
    location.reload();
  }

  try {
    var saved = JSON.parse(sessionStorage.getItem("flash") || "null");
    sessionStorage.removeItem("flash");
    if (saved) show(["Import finished", saved.lines.concat(saved.errors)], saved.errors.length > 0);
  } catch (e) {}

  var input = document.getElementById("file-input");
  document.getElementById("choose-files").addEventListener("click", function () { input.click(); });
  input.addEventListener("change", async function () {
    var lines = [], errors = [];
    for (var f of input.files) {
      var fd = new FormData(); fd.append("file", f);
      var res = await fetch("/api/import/" + kindOf(f.name), { method: "POST", body: fd });
      var body = await res.json().catch(function () { return {}; });
      if (res.ok) lines.push(describe(body)); else errors.push(f.name + ": " + (body.detail || res.status));
    }
    finish(lines, errors);
  });
  document.getElementById("rescan").addEventListener("click", async function () {
    var res = await fetch("/api/rescan", { method: "POST" });
    var body = await res.json();
    var lines = body.reports.map(describe).concat(body.notes);
    if (!lines.length && !body.errors.length) lines.push("No files found in data/raw or data/imports");
    finish(lines, body.errors);
  });
})();
