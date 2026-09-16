# prax — visual design brief

The state of the identity work, written for whoever picks it up next.
Everything here was decided; the open questions are listed at the end.

---

## 1. The mark

**A praxinoscope, in elevation, printed in three passes.**

Not a diagram of the drum from above, not an abstraction of it — the
object itself, seen from the side: the picture drum on its rim, the
mirror prism standing in the middle of it, a turned spindle, a splayed
foot. The 1877 apparatus the project is named after, drawn as something
that sits on a table and gets wound by hand.

Three earlier marks were tried and dropped, and are recorded here only
so nobody re-proposes them: the drum seen from above (too generic once
reduced), a ring with an aperture gap (indistinguishable from every
other ring-and-polygon mark), and a row of book spines on a curve (good,
but it said *shelf* before it said anything else).

### Why three passes

The identity has to be three things at once — old (the object), quirky
(the hand), modern (the type) — and arguing all three into one drawing
produced mush every time. The fix is that they are not one drawing.
A Victorian colour print is literally three blocks pulled in sequence,
so the mark is built the same way:

| Pass | What it is | Registration |
|---|---|---|
| **1 · block** | The silhouette, cut cruder than the key — no collar, no astragal, a chunky single-shape stand. Flat tone. | offset `(-1.8, +1.5)` |
| **2 · key** | The drawing. Rim thickness, six prism edges bunched toward the silhouette, nine foreshortened slits, the turned stand with its mouldings, tone built by hatching. | in register |
| **3 · colour** | One facet of the prism — the mirror catching the picture. | offset `(+1.8, -1.2)` |

The two offsets are the only misregistration, and both are deliberate.
That is what separates this from a distress-texture filter: these three
blocks could actually be cut and pulled.

**Rule: a finer key wants a finer miss.** The offsets are tuned to a
1.1-weight line. If the line ever gets heavier, the offsets go up with
it; if it gets finer, they come down. A big overhang next to a hairline
reads as a mistake, not a register.

### Construction lines

The key keeps a dashed layer at 30% opacity: the centre axis, the hidden
far edge of the base, the guide ellipse the prism stands on. These stay
in the mark — hidden lines read as care.

The dimension ticks with arrowheads appear **only** on the large
presentation figure, never in the mark. Dimension arrows read as
instruction and tip the whole thing toward infographic.

### The reduction ladder

It sheds passes going down, the way a cheap reprint does. **Swap the
file; never scale one down.**

| Size | File | What is left |
|---|---|---|
| ≥ 56px | `prax-mark.svg` | three passes, hatching, construction |
| 24–56px | `prax-mark-medium.svg` | two passes, five slits, no hatching |
| ≤ 24px | `prax-mark-solid.svg` | one pull and a colour |

The silhouette is identical at every step, so the favicon stays
recognisable as the same object. Hatching muddies first, then the tone
block, then the line.

---

## 2. Themes

**Four values are the whole theme and the whole mark.**

```
ground   the paper
tone     pass 1, the block
key      pass 2, the line — and the text colour
colour   pass 3, the one facet
```

Because the mark has exactly three fills and one stroke colour, setting
these four custom properties switches the page and the logo in the same
breath. No per-theme logo asset, no separate dark-mode file.

| Theme | ground | tone | key | colour |
|---|---|---|---|---|
| **Bindery** (default) | `#f3ead6` | `#d9bf92` | `#241d17` | `#8e2f24` |
| **Dessau** | `#e9e3d4` | `#f2b705` | `#16161a` | `#d1362f` |
| **Riso** | `#efeade` | `#7fa8d8` | `#1b1b1c` | `#ff4f9a` |
| **Cyanotype** | `#dde2e4` | `#a9bcc9` | `#12304f` | `#4a7fb5` |
| **Night** | `#16161a` | `#2b2b33` | `#efeade` | `#ff4f9a` |
| **Funk** | `#f6e7c8` | `#e8b33a` | `#3a2215` | `#ee6c2b` |

**Night flips two rules.** The key pulls *light* (cream line on dark),
and the colour pass stops multiplying — `mix-blend-mode: multiply` over
a dark ground turns neon pink to mud. It goes to `normal`. That is why
`--prax-blend` is a token rather than a constant.

The colour palette was chosen against a brief of *no warm-cream-plus-clay*
— that combination is everywhere now. Bindery is warm but goes to
oxblood and gold rather than terracotta.

**The favicon cannot read a custom property.** Either generate it per
theme at build time from `assets/logo/themes/favicon-*.svg`, or pin it
to Bindery and let it stay fixed. Same for `og:image` and anything that
goes out by e-mail.

---

## 3. Type

| Role | Face | Fallback |
|---|---|---|
| Chrome, labels, wordmark | **Bricolage Grotesque** | `"Helvetica Neue", Arial, sans-serif` |
| Anything you read | **Literata** | `Georgia, "Times New Roman", serif` |
| Mathematics | **KaTeX's** (Computer Modern's descendants) | — |

Contemporary faces, doing no period work at all — the object carries the
old on its own. Wordmark is Bricolage 600 at `-0.035em`. The third row
is the one exception and it is not a choice: an equation set in
anything but the maths faces reads as a mistake, so KaTeX (vendored,
`vendor/katex/`) sets a formula chunk and the `$…$` in a paper's prose
or an answer, and nothing else — never a search snippet, which is cut
and marked. The LaTeX stays a click away ("LaTeX" beside the number):
the source is the content, the typesetting is the courtesy.

Rejected: Rye and Abril Fatface (period wood type — costume), Inter and
Roboto (invisible in the wrong way).

---

## 4. The page

The UI takes the *furniture* of a Victorian label without its costume.
What made the label look 1877 was three separable things — wood type,
fleurons, engraved hatching — and all three are dropped. What is kept
has no period at all:

- **Rules that end in a mark.** A hairline, a lozenge, a hairline.
- **Corner brackets instead of card borders.** Four small drawn ticks
  hold a panel; no border, no shadow, no radius.
- **Small caps with wide tracking** (`0.16em`) for every label.
- **A double rule under the masthead** — 2px, 3px gap, 1px.
- **Room.** Generous margins are the cheapest part of the effect.

The lozenge is drawn with the same wobbly hand as the mark, so it reads
as this project's ornament rather than a printer's flower.

### The one rule that keeps it from going twee

> **Ornament may cost you space. It must never cost you a click.**

Everything decorative lives in margins and between sections. Nothing
decorative is a control, hides a control, or needs explaining. Silicon
Valley minimalism spends its restraint budget the other way round —
plain surfaces, dense controls — which is exactly why this does not
read as that.

---

## 5. Files

```
assets/logo/prax-mark.svg           three passes, themeable (inline it)
assets/logo/prax-mark-medium.svg    two passes
assets/logo/prax-mark-solid.svg     one pull and a colour
assets/logo/favicon.svg             static, Bindery
assets/logo/themes/*.svg            static per theme, for <img> and og:image
css/prax-themes.css                 the six themes as custom properties
snippets/ornament.css               plate brackets, rule-with-mark, small caps
snippets/masthead.html              the header, with its double rule
```

**Inline the themeable SVGs in the DOM.** `var()` does not resolve
through `<img src>` — through an `<img>` they fall back to the Bindery
literals baked in as fallbacks, which is a safe but unthemed result.

---

## 6. Open

- **Which theme is the default.** Bindery is set as `:root` because it
  is the calmest. Night is the best-looking and the likeliest to be what
  anyone actually uses in the evening. Worth shipping both and letting
  the setting decide.
- **Whether the favicon follows the theme.** Costs a build step.
- **The wordmark is live text, not outlines.** Fine for the UI; if the
  mark ever needs to go somewhere without webfonts, it needs cutting to
  paths.
- **No motion yet.** The three passes are an obvious place for a
  page-load animation — block, then key, then colour, ~80ms apart. It
  would be charming once and irritating daily, so it probably belongs on
  a landing page and nowhere else.
- **The mark has an axis of symmetry** the earlier candidates did not,
  which makes it sit statically in a header. Worth watching once it is
  in place at real size.
