// Pages: the wiki's own documents, and what links to them.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ----------------------------------------------------------------- pages
// The wiki: notes on documents, project threads, topic pages. Each is a
// document, so a row opens the document view with its editor.

async function viewPages(p) {
  view.innerHTML = `
  <form id="new-page" class="search-form">
    <input name="title" type="text" placeholder="new page title…" required>
    <select name="kind">
      <option value="topic">topic</option>
      <option value="synthesis">synthesis</option>
      <option value="project">project</option>
    </select>
    <button>Create</button>
    <span id="page-create-msg" class="error"></span>
  </form>
  <div id="pages-list">${listPlaceholder("pages-list")}</div>`;
  document.getElementById("new-page").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    const slug = data.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
    try {
      const r = await put(`/page/${slug}`, {
        text: data.kind === "project"
          ? `# ${data.title}\n\n## Status\n\n## Open questions\n\n## Log\n\n`
          : `# ${data.title}\n\n`,
        title: data.title,
        kind: data.kind,
      });
      go("doc", String(r.doc_id), { edit: 1 });
    } catch (err) { document.getElementById("page-create-msg").textContent = err.message; }
  });
  const list = document.getElementById("pages-list");
  try {
    // a standing question says whether the library has learned something
    // since (a search per question, a second or two while the store is
    // being written to); the tree does not wait for it: it is drawn as soon
    // as the pages are in, and the questions' state follows
    const standingP = api("/questions").catch(() => []);
    const pages = await api("/pages", { kind: p.kind });
    if (!pages.length) { list.innerHTML = `<p class="muted">No pages yet. Create a topic or project page above, or add a note from any document.</p>`; return; }
    const filter = pageFilter();
    list.innerHTML = `<div id="pages-questions">${pages.some((pg) => pg.kind === "question") ? `<h2 class="page-tree-group">Standing questions</h2><p class="muted">checking what the library has learned since…</p>` : ""}</div>
      <input id="page-filter" class="page-filter" type="search" placeholder="filter the pages…" value="${esc(filter)}" aria-label="filter the pages">
      <div id="page-tree-box">${pageTree(pages, filter)}</div>`;
    document.getElementById("page-filter").addEventListener("input", (e) => {
      pageFilter(e.target.value);
      document.getElementById("page-tree-box").innerHTML = pageTree(pages, e.target.value);
    });
    // the ask blocks of other pages are standing questions too
    const standing = await standingP;
    const news = Object.fromEntries(standing.map((q) => [q.slug, q]));
    const blocks = standing.filter((q) => q.block);
    const state = (q, slug) => {
      if (!q) return "";
      if (q.asking) return `<span class="asking">asking…</span>`;
      if (q.held) return `<span class="held">edited by hand; left</span> · <a href="#" class="ask-again" data-slug="${esc(slug)}" data-force="1" data-release="1">answer anew</a>`;
      if (q.due) return `<span title="${esc((q.new || []).map((n) => n.title).join(", "))}">${esc(q.why)}</span> · <a href="#" class="ask-again" data-slug="${esc(slug)}">${q.filled === false ? "ask now" : "ask again"}</a>`;
      return `settled · <a href="#" class="ask-again" data-slug="${esc(slug)}" data-force="1">ask again anyway</a>`;
    };
    const blockRows = blocks.map((q) => `<tr>
          <td><a href="#doc/${q.doc_id}">${esc(q.question)}</a> <span class="muted">in ${esc(q.title || "")}</span></td>
          <td class="muted">${state(q, q.slug)}</td>
          <td class="muted">r${q.revision}${q.model ? ` · ${esc(q.model)}` : ""}</td>
          <td class="muted">${esc((q.asked_at || "").slice(0, 16).replace("T", " "))}</td>
        </tr>`).join("");
    const questions = pages.filter((pg) => pg.kind === "question");
    const questionTable = questions.length || blocks.length
      ? `<h2 class="page-tree-group">Standing questions</h2>
        <table class="doc-list page-list"><tbody>${questions.map((pg) => `<tr>
          <td><a href="#doc/${pg.doc_id}">${esc(pg.title || pg.slug)}</a></td>
          <td class="muted">${state(news[pg.slug], pg.slug)}</td>
          <td class="muted">r${pg.revision} · ${esc(pg.author || "")}</td>
          <td class="muted">${esc((pg.updated_at || "").slice(0, 16).replace("T", " "))}</td>
        </tr>`).join("")}${blockRows}</tbody></table>`
      : "";
    const box = document.getElementById("pages-questions");
    box.innerHTML = questionTable;
    box.querySelectorAll("a.ask-again").forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const r = await post("/questions/run", { slug: a.dataset.slug, force: !!a.dataset.force, release: !!a.dataset.release });
        a.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: `asking (job ${r.job})…` }));
      } catch (err) { setStatus(err.message); }
    }));
  } catch (err) {
    showError(list, err);
  }
}

// The filter of the Pages view, kept for this tab so that a live refresh
// or a visit to a page and back finds it as it was. With a value, sets it.
function pageFilter(value) {
  try {
    if (value !== undefined) sessionStorage.setItem("prax.pages.filter", value);
    return sessionStorage.getItem("prax.pages.filter") || "";
  } catch (_) { return value || ""; }
}

// The tree of pages: each page under its parent (meta.page.parent), and
// the pages at the top in four groups: projects, collections (a page with
// pages under it: an imported wiki, a set of books), loose topics, and the
// briefings by month. A branch is a fold that says how many pages it holds;
// with a filter, a page matches by title or slug and the way to it stays.
function pageTree(pages, filter) {
  const byId = new Map(pages.filter((pg) => pg.kind !== "question").map((pg) => [pg.doc_id, { ...pg, kids: [] }]));
  const roots = [];
  for (const n of byId.values()) {
    const up = n.parent != null ? byId.get(n.parent) : null;
    (up ? up.kids : roots).push(n);
  }
  const size = (n) => n.kids.reduce((s, k) => s + 1 + size(k), 0);
  const q = (filter || "").trim().toLowerCase();
  const hit = (n) => !q || `${n.title || ""} ${n.slug}`.toLowerCase().includes(q);
  const keep = (n) => hit(n) || n.kids.some(keep);
  const byTitle = (a, b) => (a.title || a.slug).localeCompare(b.title || b.slug);
  const node = (n) => {
    const kids = n.kids.filter(keep).sort(byTitle);
    const link = `<a href="#doc/${n.doc_id}">${esc(n.title || n.slug)}</a>`;
    if (!kids.length) return `<li>${link}<span class="slug">${esc(n.slug)}</span></li>`;
    return `<li><details${q ? " open" : ""}><summary>${link}<span class="n">${size(n)}</span></summary><ul class="page-tree">${kids.map(node).join("")}</ul></details></li>`;
  };
  const groups = [
    ["Projects", (n) => n.kind === "project"],
    ["Collections", (n) => n.kind !== "project" && n.kind !== "briefing" && n.kids.length > 0],
    ["Topics", (n) => n.kind !== "project" && n.kind !== "briefing" && n.kids.length === 0],
  ];
  let html = "";
  for (const [label, test] of groups) {
    const rs = roots.filter(test).filter(keep).sort(byTitle);
    if (rs.length) html += `<h2 class="page-tree-group">${label}</h2><ul class="page-tree">${rs.map(node).join("")}</ul>`;
  }
  const briefs = roots.filter((n) => n.kind === "briefing").filter(keep).sort((a, b) => b.slug.localeCompare(a.slug));
  if (briefs.length) {
    const months = new Map();
    for (const b of briefs) {
      const m = (b.slug.match(/\d{4}-\d{2}/) || ["other"])[0];
      if (!months.has(m)) months.set(m, []);
      months.get(m).push(b);
    }
    html += `<h2 class="page-tree-group">Briefings</h2><ul class="page-tree">${[...months].map(([m, bs]) => `<li><details${q ? " open" : ""}><summary>${esc(m)}<span class="n">${bs.length}</span></summary><ul class="page-tree">${bs.map(node).join("")}</ul></details></li>`).join("")}</ul>`;
  }
  return html || `<p class="muted">No page matches.</p>`;
}
