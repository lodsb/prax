# The browser extension

Built (`clients/browser-extension/`, 2026-09-12). One Manifest V3
codebase for Firefox, Waterfox and Chrome, tested by hand in Waterfox
first. The door it talks to is `prax.inbox` (howto 3l). `POST
/ingest/html` takes a page as the browser rendered it, `POST
/ingest/url` a bare URL. Both take a domain set, tags and a
capture-session id.

## Installing it

Development means loading the folder, in every browser. An edit in the
repo is live at the next popup open; only a manifest or background
change needs the reload button.

- **Waterfox, Firefox:** `about:debugging#/runtime/this-firefox`, "Load
  Temporary Add-on…", pick `clients/browser-extension/manifest.json`.
  That install lasts until the browser restarts. For a permanent one,
  Waterfox (and Firefox Developer Edition or ESR) accepts unsigned
  add-ons once `xpinstall.signatures.required` is `false` in
  `about:config`. Build the file with `python
  scripts/build_extension.py` and drop `dist/prax-capture-<version>.xpi`
  onto a browser window. Release Firefox wants it signed; Mozilla's
  self-distribution channel does that in a minute per build.
- **Chrome, Chromium, Edge:** `chrome://extensions`, developer mode on,
  "Load unpacked", pick the `clients/browser-extension/` folder.
  Dragging the folder onto the page does the same.

Then open the popup once and follow "options": the server (the door's
address), the token when the door has one, default domains, and whether
"send all tabs" closes them. Saving asks the browser for permission to
talk to that server and to read every site. Both are needed: the
snapshot fetches a page's images through the extension, and "send all
tabs" reads background tabs. Firefox grants host permissions on
request, Chrome at install. A Firefox permission prompt closes the
popup, so the popup offers the request as a link while the permission
is missing. "Test connection" says whether the door answers and which
domains it offers.

## Files

| File | Role |
|---|---|
| `manifest.json` | MV3. `background.scripts` for Firefox's event page and `background.service_worker` for Chrome, side by side. `activeTab`, `scripting`, `storage`, `tabs`, and host permissions for http and https (optional in Firefox, requested when needed). It sets `content_security_policy.extension_pages` itself, without `upgrade-insecure-requests`. Firefox's MV3 default includes that directive, which rewrites the extension's `http://` fetches to `https://`. Loopback is exempt, a LAN address is not, so a door on the private network would only ever see TLS handshakes |
| `lib.js` | the pure helpers: session id, what is capturable, PDF detection, the plan (DOM, URL or skip), server normalization. Node tests in `tests/ui/extension.test.js` |
| `background.js`, `background-sw.js` | the sending. Reads each tab through `scripting.executeScript`, posts to the door, fetches a PDF with the browser's session and uploads it, keeps progress in `storage.session`, closes tabs when asked. It is also the two services SingleFile's content scripts expect from a background: timers for the lazy-image loader, and relaying frame answers to the top frame. The fetch bridge for cross-origin resources lives here too. The service-worker file only imports the other two |
| `popup.html/js` | the two buttons, domain checkboxes (from `GET /inbox`), tags, close-after-send, progress, and results with links into the UI |
| `options.html/js` | server, token, default domains, close-after-send; the permission request and the connection test |
| `style.css`, `icon.svg`, `icon{16,32,48,128}.png` | the look. The icons are the mark (`docs/design/assets/logo/favicon.svg`) on a tile of the Bindery ground, so a toolbar of either colour shows it. `icon.svg` is the source and the PNGs its renders (MuPDF: `pymupdf.open(stream=svg, filetype="svg")`) |
| `vendor/single-file/` | SingleFile's built core and frame scripts, unchanged, with its licence and a `NOTICE.md` naming the commit. It is AGPL, which makes the extension AGPL (`LICENSE`, `README.md` in `extension/`) while the server stays MIT |

## What it does

The Zotero Connector was the model: one click on the toolbar saves what
the tab shows, with a small popup for the few choices that matter.

**Send this tab.** The background injects SingleFile into the tab
(`vendor/single-file/`, the built files of
https://github.com/gildas-lormeau/SingleFile, the way Zotero's
connector does it). It takes the page as one self-contained HTML
document: images, fonts and stylesheets inlined, unused styles dropped,
scripts removed, lazy-loaded images loaded first, frames included.
Resources are fetched from the page, and through the background when
the page's origin rules refuse. That file is the original in the
archive, so "open original" in the UI shows the page offline, pictures
included, served with a sandboxing header. A page that blocks script
injection sends its bare DOM instead, marked as such in the result. It
is the rendered page and not a re-fetch, so paywalled, logged-in and
script-rendered pages arrive as seen.

A tab showing a PDF cannot be read as DOM. The background fetches the
PDF again from inside the browser, with the session and cookies the tab
has (a paywall you are logged into, an institutional proxy), and
uploads the bytes to `/ingest/file`. Only when that comes back as
something else, such as a login page, or fails, does it post the URL to
`/ingest/url` for the door to fetch.

**Send all tabs in this window.** The same, tab by tab, under one
capture-session id (`<timestamp>-<4 random chars>`). So "the tabs I
saved on Tuesday" is a query over `meta.capture.session`. Closing each
tab after a successful send is optional.

**Domain and tags.** The popup shows the domains the door offers (`GET
/inbox` lists the modules) as checkboxes, preselected from the options
page, and a tags field. A page that is both family and research gets
both.

**Feedback.** The response says whether the document was new, whether
it is searchable already, and which document it became. The popup shows
a link into the UI (`<server>/ui/#doc/<id>`) and, for a re-capture, that
the earlier capture is linked. It keeps the last forty sends as a list
that survives its closing (session storage: gone when the browser
quits). A failed send carries a retry link, which sends the same tab
again, or the first tab showing that URL, or a new tab with it. "Retry
failed" does that for every failed entry in turn, and a retry updates
the entry in place.

**A host that refuses the extension's own request.** A Cloudflare
challenge answers 403 to it, cookies or not, and to any server. The
extension then fetches from inside one of the site's own pages: an open
tab of the site, or its front page opened in a background tab and
closed again. That passes where the page's own scripts do.

When even that is refused, the extension uses the browser's downloader,
which is a navigation. The file lands in `Downloads/prax-inbox/` with a sidecar
(`<file>.json`: URL, title, domains, tags, session), and the inbox
watcher on the batch host consumes that folder like the drop folder
(howto 3l; `prax work --also <folder>` names other folders). The popup
says "downloaded for the watcher", and the document appears in the
Inbox view once the watcher has been over it.

Some sites hand the file to a page load only and refuse the downloader
too. Then the extension writes the sidecar and asks you to press Ctrl+S
in the viewer tab, saving the file under the name it shows into
`Downloads/prax-inbox/`. The watcher pairs file and sidecar. Such an
entry shows as "waiting for you" in the popup's history.

**The context menu.** "Send this page to prax" works on any page, the
browser's PDF viewer included (the popup works there too). "Send
selection to prax as an excerpt" makes a small document of its own: the
words as selected, paragraphs kept, headed by the page they are from
(title, URL, date; `meta.kind = "excerpt"`, `meta.excerpt` naming the
page). A passage worth keeping is kept without the page around it, and
an ask cites it like anything else. "Send link to prax" fetches the
linked file with the browser's session and uploads it without opening a
tab, or lets the door fetch it. All three use the options page's
default domains, and the result lands in the popup's history.

**A paper.** A scholarly page names its paper in the Highwire tags
every publisher, arXiv and the preprint servers write:
`citation_pdf_url`, `citation_doi`, `citation_arxiv_id`,
`citation_title`, `citation_author`, the venue and the date. "Send this
tab" on an abstract page fetches the PDF those tags point at, with this
browser's session, and uploads it under the paper's title with its DOI,
arXiv id and authors (`meta.doi`, `meta.arxiv`, `meta.creators`,
`meta.paper`). Those are the ids the citations importer joins on, as
Zotero's import writes them. A PDF the library already holds is not
held twice: the ids and authors fill in what that document lacks. When
the PDF cannot be fetched, behind a paywall you are not through, the
page goes as a snapshot carrying the ids. A page with ids and no PDF
link goes the same way.

**A video.** On a YouTube watch page, or any page with a player of that
shape, "send this tab" sends the recording as a document rather than
the page. The transcript comes from a caption track: a person's in your
language, else any person's, else the automatic one. It is grouped into
paragraphs at pauses and sentence ends, each headed by its moment.

A frame is taken every thirty seconds, drawn from the tab's own player
after asking it to seek, muted, with the playback put back afterwards.
The options page sets the interval, and there are at most 150 frames. A
frame that looks like the one before is dropped, so a talking head
gives one and a slide change a new one. Where the player does not
deliver a moment, because it is not playing and will not buffer, or the
stream is protected, the seek bar's own preview picture stands in. That
is small, 320×180 at best, but it is there for any video, and the
result says how many were those. An ad the player starts on a seek is
skipped when its button appears, else waited out: an ad blocker's skip
comes late and the player stalls a while. An ad never counts as a
moment not delivered, and the result says how many were sat through.

The caption tracks the watch page hands out answer empty unless the
page's own player asks, with a session token it adds. So the tracks are
taken from YouTube's player service as the iOS client, which answers
plainly, from inside the tab. The page's own are the last resort. A
page that is not YouTube can have a `<video>` of its own with
`<track>`s of captions, such as a course platform or a media site. It
goes the same way: the WebVTT is read as the transcript and the frames
are drawn from the element. That document's page shows the first frame as a link to
the recording rather than a player.

Nothing is downloaded. The document is HTML of prax's own shape, which
`src/prax/parsers/video.py` reads exactly: the chapters a description
lists become headings, each paragraph and frame carries `data-t`, and
the frames are inlined. The door keeps what the page knew of the
recording (`meta.video`), the passages carry their moment, and the
frames are figures the vision pass reads, so a talk's slides become
text. The document's page in the UI shows the player, every moment a
seek. A recording without captions still goes, frames only; one whose
frames cannot be drawn (DRM) goes with its transcript.

**A selection, in the popup.** When the tab in front has text selected,
the popup shows it ("12 words selected") with two ways to keep it.
"Send as excerpt" makes the same small document the context menu makes.
"Add to page…" opens a picker of your prax pages, most recently revised
first, and the words go onto the chosen page quoted, with the page they
are from, as a new human revision. The door's append leaves what was
there as it was. Reading notes without leaving the browser.

**The keyboard.** `Alt+Shift+P` sends the tab in front with the options
page's default domains, popup or no popup. The browser's
extension-shortcut page changes the key. A send without the popup shows
in the toolbar icon's badge: "…" while it runs, "✓" for a few seconds
when every tab went, "!" until the popup is next opened when one did
not. The popup's history has the details.

**Bookmarklet and share target** are fallbacks that post a URL only,
which the door fetches. The mobile share target is a tiny page under
`/ui/` that takes `?url=` and calls `/ingest/url`.

## Settings (the options page)

| Setting | What | Default |
|---|---|---|
| Server | the door's URL: `http://<host on your private network>:8000` on the serving host, `http://127.0.0.1:8000` on the desktop | empty; the popup refuses to send until it is set |
| Token | `PRAX_TOKEN`, stored in `chrome.storage.local` (never `sync`) | empty |
| Default domains | preselected in the popup | none |
| Close tabs after sending | for "send all tabs" | off |
| Domains by site | one line per site, `host domain[, domain]`. A page of that site or a subdomain of it is sent with those domains instead of the defaults, from the keyboard, the context menu and the popup, which preselects them | none |
| Video frames | a frame every N seconds of a video, 5 to 600. At most 150 frames: the interval stretches for a long recording | 30 |

The popup checks `GET /health` on open and shows the server's state:
reachable, token accepted or refused.

## Authentication

- The extension sends `Authorization: Bearer <token>` on every request,
  the same shared secret scripts use (howto 4). Without a token the door
  admits loopback only, which is enough for the desktop.
- The door answers every extension origin (`chrome-extension://…`,
  `moz-extension://…`) over CORS. The token gates it, not the origin, so
  a browser that withholds the host permission and runs the request
  through CORS still gets through. The CORS middleware answers the
  preflight (OPTIONS) without the token, and with
  `Access-Control-Allow-Private-Network: true` for Chrome's
  private-network check. Web origins, a page of your own calling the
  door, go into `door.cors_origins` / `PRAX_CORS_ORIGINS`.
- Transport: the door is reached over your private network (the LAN or a
  VPN; Tailscale is one example), so plain HTTP is private there. The
  extension asks for `host_permissions` on the server URL only, granted
  at install for the configured host. TLS through a reverse proxy is a
  later step, for a door that ever leaves the private network.
- The token is a bearer secret in the browser's extension storage, as
  safe as the profile. Rotating it means setting a new `PRAX_TOKEN` and
  pasting it into the options page.

## Manifest V3, one codebase

- `manifest.json` has `action` (the popup), `options_ui`, `permissions:
  ["activeTab", "scripting", "storage", "tabs"]`, optional
  `host_permissions` for the configured server (requested at runtime
  with `permissions.request` when the options are saved), and
  `browser_specific_settings.gecko` for Firefox.
- `popup.html/js`: the two buttons, the domain checkboxes, tags, the
  result list.
- No persistent content script. The background injects what a capture
  needs with `scripting.executeScript` (a function for the bare DOM,
  SingleFile's files for a snapshot), and nothing stays in the page.
- `background.js` does every send, so the popup closing never aborts a
  loop. The popup only asks and shows progress.
- Size guard: a snapshot above 32 MB is sent as URL only.

## Testing it (`scripts/extension_bed.mjs`)

The extension end to end, in a headless browser, against a throwaway
door. No hand on the mouse, nothing of yours touched:

    node scripts/extension_bed.mjs                     # Chrome
    node scripts/extension_bed.mjs --browser firefox   # Firefox or Waterfox
    node scripts/extension_bed.mjs --browser both
    node scripts/extension_bed.mjs --keep              # leave door and browser up to look
    node scripts/extension_bed.mjs --trace             # log what the fixture served
    node scripts/extension_bed.mjs --door http://127.0.0.1:8000 --token …   # a door of yours

The throwaway door wants a token like a real one. It is a random one
made for the run (`--token` chooses it, which `--keep` wants), never the
`PRAX_TOKEN` of this environment. So the extension's bearer header is
what gets it in, a wrong token is seen refused, and nothing of yours is
touched. Against a door of yours (`--door`), the fixture captures land
in that door's inbox.

The bed serves a fixture site on a port of its own: an article with a
stylesheet, an image and a frame, a second page, and a PDF from the
test fixtures. It starts a door on a temporary data directory
(`uvicorn prax.api:app`, hash embeddings, no extraction, no title
pass). Then it drives the browser through the extension's own
surfaces. The settings the options page stores, and its "test
connection". The popup, opened as a page: the door reachable, its
domains listed, the default preselected. And "send this tab": the
message the popup sends, the progress the background writes.

Then it asks the door what arrived. A snapshot with the image, the
stylesheet and the frame's text inlined, the domain and the tag on it.
No second document when the same tab is sent again. A PDF tab uploaded
as a file. A whole window sent. The options page's test refusing a
wrong token. The keyboard send and its badge. A door that is not there:
the send fails naming the door, and "retry failed" sends it again once
the door is back. A rule for the site sending with the site's domains.
A selection sent as an excerpt, with nothing selected saying so, and
added to a page of yours as a new revision. An abstract page whose
citation tags send the paper's PDF with its ids, filling them in on the
document the PDF already is. A watch page sent as a video. The fixture page
has a player of YouTube's shape and a six-second clip of three colours
(`tests/fixtures/video/bars.webm`). It gives two paragraphs, three
frames of six moments, the chapters, and the moment on a search hit. A
page with a `<video>` and a WebVTT track of its own goes the same
way.
In Firefox, where the host permission is removable, the send without it:
the popup offers the grant, and every tab still goes by URL with a note
naming the missing permission. Each check prints a line, and one failed
check fails the run.

Neither browser loads an unpacked extension the obvious way any more,
so the bed drives them like this. **Chrome** over the DevTools protocol
(`--remote-debugging-port`, `--enable-unsafe-extension-debugging`,
`Extensions.loadUnpacked`), since branded Chrome ignores
`--load-extension`; set `CHROME=` to the binary if it is not found.
**Firefox** over geckodriver's WebDriver (`--allow-system-access`). The
add-on is installed temporarily with its UUID pinned through the
`extensions.webextensions.uuids` pref, so its pages have a known
address, and those pages are opened from the browser's chrome context
(`gBrowser.addTab`), since WebDriver refuses to navigate to
`moz-extension://`. It needs geckodriver (`GECKODRIVER=`, else
`%LOCALAPPDATA%\prax\tools\geckodriver.exe` or
`~/.local/share/prax/tools/geckodriver`) and Firefox or Waterfox
(`FIREFOX=`), Node 22 or later, and the repo's `.venv` for the door.

The fixture image is built in the script rather than pasted. Firefox
refuses an image whose CRC or zlib check is off, where Chrome shrugs,
and SingleFile never fetches a broken image. A corrupt fixture looks
exactly like an extension bug.

## Not in scope

No reading list, no annotation, no sync: prax's pages are where notes
go. No bundling of images or CSS: trafilatura wants the article text,
and the archive keeps the DOM as the original.
