// The inbox: what came in, the drop zone, and what is still waiting.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ----------------------------------------------------------------- inbox
// Captures: upload files (drag and drop or pick), send a URL for the door
// to fetch, and the latest captures with their state. Each capture names
// its domains; the rules in prax.yaml apply when none is chosen.

async function viewInbox(p) {
  loading();
  let d;
  try { d = await api("/inbox", { limit: p.limit || 50 }); } catch (err) { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const domainOpts = d.modules.map((m) => `<label class="chip"><input type="checkbox" name="domain" value="${esc(m)}"> ${esc(m)}</label>`).join(" ");
  const row = (x) => `<tr>
      <td><a href="#doc/${x.doc_id}">${esc(x.title || "(untitled)")}</a>${x.source_url ? ` <a class="muted" href="${esc(x.source_url)}" target="_blank" rel="noopener" title="${esc(x.source_url)}">↗</a>` : ""}</td>
      <td class="muted">${esc(x.source)}${x.capture.by && x.capture.by !== x.source ? ` (${esc(x.capture.by)})` : ""}${x.capture.mode ? ` · ${esc(x.capture.mode)}` : ""}${x.capture.note && x.capture.mode !== "snapshot" ? `<br><small title="${esc(x.capture.note)}">${esc(x.capture.note.slice(0, 60))}</small>` : ""}</td>
      <td class="muted">${esc((x.domains || []).join(", ") || "all")}${x.recaptured ? ` <span title="sent again with the same text">·${x.recaptured + 1}×</span>` : ""}</td>
      <td class="muted">${esc((x.capture.at || "").slice(0, 16).replace("T", " "))}</td>
      <td>${x.indexed ? (x.extracted ? "extracted" : "indexed") : x.tried ? `<span class="muted" title="every extractor tried it and found no text — a scan? OCR or the vision model can be asked for on its page (read again…)">no text found</span>` : `<span class="muted" title="registered; the worker (prax work --watch) extracts its text">pending</span>`}</td>
    </tr>`;
  view.innerHTML = `
    <form id="upload" class="search-form" autocomplete="off">
      <div id="drop" class="drop">Drop files here, or <label><input id="files" type="file" multiple hidden><u>choose files</u></label> or <label><input id="folder" type="file" webkitdirectory multiple hidden><u>a folder</u></label>. Text and HTML are searchable at once; PDFs and images wait for the parse queue.</div>
      <input id="up-title" type="text" placeholder="title (single file only)" style="flex:1 1 16rem">
      <span class="chips" id="up-domains">${domainOpts}</span>
      <input id="up-tags" type="text" placeholder="tags, comma-separated" style="flex:0 1 14rem">
      <button>Upload</button>
    </form>
    <form id="fetch" class="search-form" autocomplete="off">
      <input id="fetch-url" type="url" placeholder="https://… (the door fetches it: a page, a PDF)" style="flex:1 1 22rem" required>
      <input id="fetch-title" type="text" placeholder="title (optional)">
      <button>Fetch</button>
    </form>
    <p class="muted">Drop folder on the server: <code>${esc(d.inbox_dir)}</code> (a file in <code>inbox/&lt;domain&gt;/</code> lands in that domain; <code>scripts/inbox.py --watch --parse</code> consumes it).</p>
    <p id="inbox-msg">${inboxReport()}</p>
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Recent captures (${d.recent.length})</h2>
    ${d.recent.length ? `<table class="doc-list"><thead><tr><th>document</th><th>source</th><th>domains</th><th>when</th><th>state</th></tr></thead><tbody>${d.recent.map(row).join("")}</tbody></table>` : `<p class="muted">Nothing captured yet.</p>`}`;
  const clearKept = document.getElementById("inbox-msg-clear");
  if (clearKept) clearKept.addEventListener("click", () => { keepInboxReport(""); document.getElementById("inbox-msg").innerHTML = ""; });
  const chosenDomains = () => [...view.querySelectorAll("#up-domains input:checked")].map((i) => i.value);
  // The message box is looked up at report time: the view may have been
  // re-rendered meanwhile (the change poll), and a captured element would
  // then be a detached one that nobody sees.
  const msgBox = () => document.getElementById("inbox-msg");
  const line = (r) => r.error
    ? `<span class="error">${esc(r.name)}: ${esc(r.error)}</span>`
    : `${esc(r.name)} → <a href="#doc/${r.doc_id}">doc ${r.doc_id}</a>${r.duplicate_of ? " (the same page again)" : r.created ? "" : " (already in the store)"}${r.replaced ? `, replaces doc ${r.replaced}` : ""}${r.indexed ? ", searchable" : ", waiting for the parse queue"}`;
  const report = (results, done, total) => {
    const box = msgBox();
    if (!box) return;
    const n = results.length;
    const failed = results.filter((r) => r.error);
    const fresh = results.filter((r) => !r.error && r.created).length;
    const known = results.filter((r) => !r.error && !r.created).length;
    const summary = n > 1
      ? `<b>${done ? "Done:" : "Uploading:"}</b> ${n} of ${total} file${total === 1 ? "" : "s"} — ${fresh} new, ${known} already in the store${failed.length ? `, <span class="error">${failed.length} failed</span>` : ""}. ${done ? "The parse queue reads the new ones; the list below follows." : ""}`
      : "";
    // every failure in full; successes in full up to a screen, then folded
    const shown = n <= 40 ? results : [...failed, ...results.filter((r) => !r.error).slice(0, 12)];
    const rest = n - shown.length;
    const html = summary + (summary && shown.length ? "<br>" : "") + shown.map(line).join("<br>")
      + (rest > 0 ? `<br><span class="muted">… and ${rest} more (all in the list below once parsed)</span>` : "")
      + (done && n > 1 ? ` <button type="button" class="linkish" id="inbox-msg-clear">dismiss</button>` : "");
    keepInboxReport(html);  // shown again after a re-render or a reload of the page
    box.innerHTML = html;
    const clear = document.getElementById("inbox-msg-clear");
    if (clear) clear.addEventListener("click", () => { keepInboxReport(""); box.innerHTML = ""; });
  };
  async function upload(files) {
    if (!files.length) return;
    // what the form says, read once: the view may re-render during a long batch
    const title = files.length === 1 ? (document.getElementById("up-title").value || "").trim() : "";
    const doms = chosenDomains();
    const tags = (document.getElementById("up-tags").value || "").trim();
    uploading = true;
    const results = [];
    const total = files.length;
    try {
      for (const [i, f] of files.entries()) {
        setStatus(`uploading ${i + 1} of ${total}…`);
        const fd = new FormData();
        fd.append("file", f, f.name);
        if (title) fd.append("title", title);
        if (doms.length) fd.append("domains", doms.join(","));
        if (tags) fd.append("tags", tags);
        try {
          const res = await fetch("/ingest/file", { method: "POST", body: fd });
          if (res.status === 401) { askForToken(); throw new Error("access token required"); }
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || res.statusText);
          results.push({ name: f.name, ...data });
        } catch (err) { results.push({ name: f.name, error: err.message }); }
        if (i % 10 === 9) report(results, false, total);
      }
    } finally {
      uploading = false;
    }
    setStatus(`uploaded ${total} file${total === 1 ? "" : "s"}`);
    report(results, true, total);
    refreshList();
  }
  let pendingTimer = null;
  async function refreshList() {
    try {
      const fresh = await api("/inbox", { limit: p.limit || 50 });
      const tbody = view.querySelector("table.doc-list tbody");
      if (tbody) tbody.innerHTML = fresh.recent.map(row).join("");
      watchPending(fresh.recent);
    } catch (_) { /* the list stays as it was */ }
  }
  // while a capture is pending, the list follows the watcher's progress
  function watchPending(recent) {
    clearTimeout(pendingTimer);
    if (recent.some((x) => !x.indexed) && route().name === "inbox") {
      pendingTimer = setTimeout(refreshList, 10000);
    }
  }
  watchPending(d.recent);
  const drop = document.getElementById("drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });
  document.getElementById("files").addEventListener("change", (e) => upload([...e.target.files]));
  document.getElementById("folder").addEventListener("change", (e) => upload([...e.target.files].filter((f) => !f.name.startsWith("."))));
  document.getElementById("upload").addEventListener("submit", (e) => { e.preventDefault(); upload([...document.getElementById("files").files]); });
  document.getElementById("fetch").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = document.getElementById("fetch-url").value.trim();
    const body = { url, title: document.getElementById("fetch-title").value.trim() || null, domains: chosenDomains().length ? chosenDomains() : null };
    try {
      const data = await post("/ingest/url", body);
      report([{ name: url, ...data }], true, 1);
      refreshList();
    } catch (err) { report([{ name: url, error: err.message }], true, 1); }
  });
}
