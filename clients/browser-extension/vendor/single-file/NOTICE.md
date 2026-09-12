# SingleFile, vendored

The three scripts here are the built files of SingleFile by Gildas
Lormeau (https://github.com/gildas-lormeau/SingleFile), copied unchanged
from that repository's `lib/` directory:

| file | role |
|---|---|
| `single-file.js` | the core: `singlefile.getPageData(options, {fetch})` turns the current document into one self-contained HTML string |
| `single-file-frames.js` | runs in every frame so the core can inline their content |
| `single-file-hooks-frames.js` | runs in the page's own world so lazy-loaded images and adopted stylesheets are captured |

Taken from commit `5233f7d0b9e4` (2026-09-09). To update: copy the same
three files from `lib/` again and change this line.

License: GNU Affero General Public License v3.0 or later (`LICENSE`
next to this file). This is why the extension as a whole is AGPL
(`extension/LICENSE`); the prax server it talks to over HTTP is a
separate program and stays MIT. Zotero's connector vendors SingleFile
the same way.
