# Ontology v4: syntheses across sources (2026-09-11)

A page that draws on several documents and makes claims of its own had no
vocabulary: v3 could say a page *annotates* a document (a note on one
thing) and a project has members, but not "this write-up rests on these
five papers and argues these three points". v4 adds:

- page kind `synthesis` (`store.PAGE_KINDS`), next to topic, project and
  addendum; the Pages tab groups them, `doctype=page` finds them, and the
  Ask view can start one from an answer ("or start a synthesis").
- relation `synthesizes`: domain page or project, range paper or page.
  `write_page` uses it instead of `annotates` for a synthesis page's
  sources; `annotates` keeps its meaning (a page about one document).
- `supports` and `contradicts` accept a page as source, and `proposes`
  accepts a page: extracting a synthesis page yields the page proposing
  claims and the papers supporting or contradicting them, which makes the
  claims graph nodes between the synthesis and its sources.
- the extractor's header carries the page kind (`Kind: page (synthesis)`)
  so the model knows what it reads; the `page` type description mentions
  syntheses.

No entity type: a synthesis is a document, already a `page` entity by
title. No chunk-level edges: the page text carries chunk links (Ask writes
them), and edges keep evidence in their column (rationale R15, R16).

The bump re-selects every extracted document for extraction (their stamp
says v3); `replay_review.py` links what the queue holds that v4 accepts;
the pages themselves are extracted with `extract_graph.py --ids`.
