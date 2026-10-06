# What a document's `meta` holds

`documents.meta` is a JSON object, open on purpose: a new source adds
keys there before it earns a column (CLAUDE.md, Conventions). This is
the catalogue of its top-level keys. `store.DocumentMeta` declares the
same keys with the shape of each value, and `tests/test_meta_keys.py`
holds the two lists equal.

A key the code writes and the declaration does not name fails the test
that wrote it: the root `conftest.py` sets `store.checks.strict_meta`,
and `store.check_meta` refuses it at the three places a meta is written
(`register`, `set_meta`, `_put_meta`), and in the `json_set` calls a
test scans. On a host the key is kept and logged once
(`meta key 'x' is undeclared`). A nested object's own keys are its
writer's; the comment names who that is.

`store.get_meta` returns a `DocumentMeta`, so mypy checks a reader's
literal keys and the type of what it reads. The writers (`set_meta`,
`_put_meta`, `register`) take `store.MetaLike`, the declared shape or a
plain dict. A key read through a variable is a `Final` or a `Literal`.

To add a key: declare it in `store.DocumentMeta` with its shape and
writer, and add its row here.


## Where it came from: the importer or capture, at register

| key | shape | what, and who writes it |
|---|---|---|
| `source` | `str` | zotero, capture, drop, feed, github, project, claude, citations… |
| `origin` | `dict[str, Any]` | a drop folder's host and path |
| `capture` | `dict[str, Any]` | the extension's at, session, by, note, mode |
| `requested_url` | `str` | the URL asked for, where the capture landed elsewhere |
| `previous_capture` | `int` | the document this capture of the same URL follows |
| `recaptured` | `list[dict[str, Any]]` | later captures folded into this one |
| `parser` | `str` | the parser a document names for itself (video) |
| `video` | `dict[str, Any]` | the player: provider, id, url, chapters, captions… |
| `polished` | `bool` | an automatic transcript punctuated (the polish reading) |
| `zotero` | `dict[str, Any]` | the Zotero item and attachment, importers.zotero |
| `fields` | `dict[str, Any]` | Zotero's item fields, as Zotero names them |
| `creators` | `list[dict[str, Any]]` | Zotero's creators |
| `collections` | `list[str]` | Zotero's collection paths |
| `date` | `str \| None` | the record's date as written (Zotero) |
| `abstract` | `str \| None` | the record's abstract |
| `doi` | `str \| None` | a printed or recorded DOI (Zotero, a capture's paper) |
| `arxiv` | `str` | an arXiv id |
| `paper` | `dict[str, Any]` | the extension's paper: journal, date, pdf_url |
| `project` | `dict[str, Any]` | a project's sync: key, name, path, version |
| `tags` | `list[str]` | a capture's, an importer's, a person's |
| `citations` | `dict[str, Any]` | importers.citations: OpenAlex's record |

## An importer's own block, under its source's name (importers.feed)

| key | shape | what, and who writes it |
|---|---|---|
| `github` | `dict[str, Any]` | importers.github: the repository |
| `claude` | `dict[str, Any]` | importers.claude: a Claude project's document |
| `chat` | `dict[str, Any]` | importers.chats: a conversation |
| `links` | `dict[str, Any]` | importers.links: a link list's entry |

## The text: parsers.queue, store.index_text

| key | shape | what, and who writes it |
|---|---|---|
| `text_source` | `str \| None` | the parser and its version that wrote the text |
| `parse_history` | `list[dict[str, Any]]` | earlier parses (bounded) |
| `pages` | `int` | the original's page count, at parse |
| `ocr` | `dict[str, Any]` | an OCR reading's pages and progress |
| `reading` | `dict[str, Any]` | the last reading that finished (readings queue) |
| `lang` | `str` | ISO 639-1, prax.text.language |
| `markup` | `dict[str, Any]` | the schema.org pass: hash, facts, type, genre |
| `references` | `dict[str, Any]` | the references pass: entries, links |
| `private` | `dict[str, Any]` | the rules' cues for "personal?" (stage V) |
| `sensitivity` | `dict[str, Any]` | a person's or a rule's decision: state, by |

## What it is: the domains, the genres (stage Z)

| key | shape | what, and who writes it |
|---|---|---|
| `domains` | `list[str]` | the modules it is read against (invariant 9) |
| `domains_by` | `str` | a person, a rule, the labeller |
| `genres` | `list[dict[str, Any]]` | labels with p (ontology/genres.yaml) |
| `subjects` | `list[dict[str, Any]]` | labels with p (ontology/subjects.yaml) |
| `genres_at` | `str` | when they were given |
| `genres_by` | `str` | human, claude, the labeller |
| `genres_run` | `str` | the labeller's or the step's batch |
| `genres_model` | `dict[str, Any]` | a model's labels a person's save replaced |
| `genres_note` | `str` | a person's note on the genre tab |
| `genres_skip` | `str` | a person skipped it on the genre tab |
| `genres_tried` | `dict[str, Any]` | the genres step could not |

## What the model steps wrote

| key | shape | what, and who writes it |
|---|---|---|
| `title_source` | `str` | what gave the title: the record, the titles step, a person |
| `title_run` | `str \| None` | the titles step's batch |
| `title_confidence` | `str \| None` | the titles step's own |
| `title_history` | `list[dict[str, Any]]` | the titles it replaced |
| `titles_tried` | `dict[str, Any]` | the titles step found none better |
| `summary` | `str` | the summary the document field indexes |
| `summaries` | `dict[str, str]` | every summary there is, by language |
| `summary_lang` | `str` | the language ``summary`` is in |
| `summary_source` | `str` | what wrote it: an extraction, the summaries step |
| `summary_run` | `str \| None` | that step's batch |
| `summary_tried` | `dict[str, Any]` | the summaries step could not |
| `sections` | `dict[str, Any]` | a long document's chapter summaries |
| `published` | `dict[str, Any]` | date, precision, by, words, confidence |
| `published_tried` | `dict[str, Any]` | the dates step found no date |
| `extraction` | `dict[str, Any]` | the last reading of the graph, its stamp |
| `extraction_history` | `list[dict[str, Any]]` | earlier readings' stamps |
| `extraction_stale` | `dict[str, Any]` | the text read is replaced, or asked again |
| `extraction_error` | `dict[str, Any]` | the last reading failed: who, under what |
| `promote` | `dict[str, Any]` | flagged for the expensive pass |
| `world_dates` | `dict[str, Any]` | the worlddates step's stamp |

## Its life: retired, superseded, stale

| key | shape | what, and who writes it |
|---|---|---|
| `retired` | `dict[str, Any]` | at, reason, of, by, run |
| `status` | `dict[str, Any]` | a project note's own status line |

## A page (pages are documents too)

| key | shape | what, and who writes it |
|---|---|---|
| `page` | `dict[str, Any]` | slug, kind, revision, author |
| `asks` | `dict[str, Any]` | a page's ask blocks by id |
| `briefing` | `dict[str, Any]` | a briefing page's window and documents |
| `question` | `dict[str, Any]` | a question page's question |

## What a writer of meta takes: the declared shape, or a plain dict a

| key | shape | what, and who writes it |
|---|---|---|

## Caller built (an importer's, a test's)

| key | shape | what, and who writes it |
|---|---|---|
