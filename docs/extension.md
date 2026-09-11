# The browser extension

Status: designed, not built. The door it talks to exists (`prax.inbox`,
howto 3l): `POST /ingest/html` takes a page as the browser rendered it,
`POST /ingest/url` a bare URL, both with a domain set, tags and a
capture-session id; `PRAX_CORS_ORIGINS` admits the extension's origin.

## What it does

The Zotero Connector is the model: one click on the toolbar saves what
the tab shows, with a small popup for the few choices that matter.

- **Send this tab.** The content script returns
  `document.documentElement.outerHTML`, the URL and the title; the popup
  posts them to `/ingest/html`. The rendered DOM, not a re-fetch, so
  paywalled, logged-in and script-rendered pages arrive as seen. A tab
  showing a PDF cannot be read as DOM: the popup posts its URL to
  `/ingest/url` and the door fetches the file (cookies are not
  forwarded, so a paywalled PDF is saved through the file upload
  instead: the browser's download, then the Inbox view or the drop
  folder).
- **Send all tabs in this window.** The same, tab by tab, under one
  capture-session id (`<timestamp>-<4 random chars>`), so "the tabs I
  saved on Tuesday" is a query over `meta.capture.session`. Optional:
  close each tab after a successful send.
- **Domain and tags.** The popup shows the domains the door offers
  (`GET /inbox` lists the modules) as checkboxes, preselected from the
  options page; a tags field. A page that is both family and research
  gets both.
- **Feedback.** The response says whether the document was new, whether
  it is searchable already, and which document it became; the popup
  shows a link into the UI (`<server>/ui/#doc/<id>`) and, for a
  re-capture, that the earlier capture is linked.
- **Bookmarklet and share target** as fallbacks post a URL only; the
  door fetches. The mobile share target is a tiny page under `/ui/`
  that takes `?url=` and calls `/ingest/url`.

## Settings (the options page)

| Setting | What | Default |
|---|---|---|
| Server | the door's URL: `http://<tailscale-name>:8000` on the serving host, `http://127.0.0.1:8000` on the desktop | empty; the popup refuses to send until set |
| Token | `PRAX_TOKEN`, stored in `chrome.storage.local` (never `sync`) | empty |
| Default domains | preselected in the popup | none |
| Close tabs after sending | for "send all tabs" | off |

The popup checks `GET /health` on open and shows the server's state
(reachable, token accepted or refused).

## Authentication

- The extension sends `Authorization: Bearer <token>` on every request,
  the same shared secret scripts use (howto 4). Without a token the door
  admits loopback only, which is enough for the desktop.
- The door must list the extension's origin in `PRAX_CORS_ORIGINS`
  (`chrome-extension://<id>`, `moz-extension://<uuid>`); Firefox gives
  each install a different UUID, so the manifest pins one through
  `browser_specific_settings.gecko.id`, and the door can also take a
  wildcard for development. Preflight (OPTIONS) is answered by the CORS
  middleware without the token.
- Transport: the door is reached over Tailscale (WireGuard), so plain
  HTTP on the tailnet is private; the extension asks for
  `host_permissions` on the server URL only, granted at install for the
  configured host. HTTPS with Tailscale's certificates is a later step
  if the door ever leaves the tailnet.
- The token is a bearer secret in the browser's extension storage; it is
  as safe as the profile. Rotating it is setting a new `PRAX_TOKEN` and
  pasting it into the options page.

## Manifest V3, one codebase

- `manifest.json` with `action` (the popup), `options_ui`, `permissions:
  ["activeTab", "scripting", "storage", "tabs"]`, optional
  `host_permissions` for the configured server (requested at runtime with
  `permissions.request` when the user saves the options), and
  `browser_specific_settings.gecko` for Firefox.
- `popup.html/js`: the two buttons, the domain checkboxes, tags, the
  result list.
- `content.js` injected with `chrome.scripting.executeScript` returning
  `{url, title, html}`; no persistent content script.
- `background.js` (service worker) only for "send all tabs" so the popup
  closing does not abort the loop.
- Size guard: a DOM above 8 MB is sent as URL only.

## Not in scope

No reading list, no annotation, no sync: prax's pages are where notes
go. No bundling of images or CSS: trafilatura wants the article text,
the archive keeps the DOM as the original.
