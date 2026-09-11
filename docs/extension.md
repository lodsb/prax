# The browser extension

Status: built (`extension/`, 2026-09-12), one Manifest V3 codebase for
Firefox, Waterfox and Chrome; tested by hand in Waterfox first. The door
it talks to is `prax.inbox` (howto 3l): `POST /ingest/html` takes a page
as the browser rendered it, `POST /ingest/url` a bare URL, both with a
domain set, tags and a capture-session id.

## Installing it

Development, in every browser, means loading the folder, so an edit in
the repo is live at the next popup open; only manifest or background
changes need the reload button.

- **Waterfox, Firefox:** `about:debugging#/runtime/this-firefox`, "Load
  Temporary Add-on…", pick `extension/manifest.json`. That install lasts
  until the browser restarts. For a permanent one, Waterfox (and Firefox
  Developer Edition or ESR) accepts unsigned add-ons once
  `xpinstall.signatures.required` is `false` in `about:config`: build
  the file with `python scripts/build_extension.py` and drop
  `dist/prax-capture-<version>.xpi` onto a browser window. Release
  Firefox wants it signed: Mozilla's self-distribution channel does that
  in a minute per build.
- **Chrome, Chromium, Edge:** `chrome://extensions`, developer mode on,
  "Load unpacked", pick the `extension/` folder (dragging the folder
  onto the page does the same).

Then open the popup once, follow "options": the server (the door's
address), the token when the door has one, default domains, and whether
"send all tabs" closes them. Saving asks the browser for permission to
talk to that server and to read every site (the snapshot fetches a
page's images through the extension, "send all tabs" reads background
tabs); Firefox grants host permissions on request, Chrome at install. In
Firefox a permission prompt closes the popup, so the popup itself only
offers the request as a link while the permission is missing. "Test connection" says whether the door answers and which
domains it offers.

## Files

| File | Role |
|---|---|
| `manifest.json` | MV3; `background.scripts` for Firefox's event page and `background.service_worker` for Chrome side by side; `activeTab`, `scripting`, `storage`, `tabs`; host permissions for http and https (optional in Firefox, requested when needed) |
| `lib.js` | the pure helpers: session id, what is capturable, PDF detection, the plan (DOM, URL or skip), server normalization; node tests in `tests/ui/extension.test.js` |
| `background.js`, `background-sw.js` | the sending: reads each tab through `scripting.executeScript`, posts to the door, fetches a PDF with the browser's session and uploads it, keeps progress in `storage.session`, closes tabs when asked; also the two services SingleFile's content scripts expect from a background (timers for the lazy-image loader, relaying frame answers to the top frame) and the fetch bridge for cross-origin resources; the service-worker file just imports the other two |
| `popup.html/js` | the two buttons, domain checkboxes (from `GET /inbox`), tags, close-after-send, progress and results with links into the UI |
| `options.html/js` | server, token, default domains, close-after-send; permission request and connection test |
| `style.css`, `icon48.png`, `icon128.png` | the look; the icons are generated squares |
| `vendor/single-file/` | SingleFile's built core and frame scripts, unchanged, with its licence and a `NOTICE.md` naming the commit; AGPL, which makes the extension AGPL (`LICENSE`, `README.md` in `extension/`) while the server stays MIT |

## What it does

The Zotero Connector was the model: one click on the toolbar saves what
the tab shows, with a small popup for the few choices that matter.

- **Send this tab.** The background injects SingleFile into the tab
  (`vendor/single-file/`, the built files of
  https://github.com/gildas-lormeau/SingleFile, the way Zotero's
  connector does it) and takes the page as one self-contained HTML
  document: images, fonts and stylesheets inlined, unused styles
  dropped, scripts removed, lazy-loaded images loaded first, frames
  included. Resources are fetched from the page, and through the
  background when the page's origin rules refuse. That file is the
  original in the archive, so "open original" in the UI shows the page
  offline, pictures included, served with a sandboxing header. When the
  snapshot fails (a page that blocks script injection) the bare DOM is
  sent instead, marked as such in the result. The rendered page, not a
  re-fetch, so paywalled, logged-in and script-rendered pages arrive as
  seen. A tab
  showing a PDF cannot be read as DOM: the background fetches the PDF
  again from inside the browser, with the session and cookies the tab
  has (a paywall you are logged into, an institutional proxy), and
  uploads the bytes to `/ingest/file`; only when that comes back as
  something else (a login page) or fails does it post the URL to
  `/ingest/url` for the door to fetch.
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
  re-capture, that the earlier capture is linked. The popup keeps the
  last forty sends as a list that survives its closing (session
  storage: gone when the browser quits), a failed one with a retry
  link that sends the same tab again, or the first tab showing that URL,
  or a new tab with it.
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
- Size guard: a snapshot above 32 MB is sent as URL only.

## Not in scope

No reading list, no annotation, no sync: prax's pages are where notes
go. No bundling of images or CSS: trafilatura wants the article text,
the archive keeps the DOM as the original.
