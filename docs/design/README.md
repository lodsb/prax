# Handing this to Claude Code

This folder is the design state for prax's identity and UI shell.
It is meant to be dropped into the repo and read, not admired.

## 1. Put it in the repo

```
cp -r prax-design/ /path/to/prax/docs/design/
```

`docs/design/` sits beside the other docs the project already keeps, so
it will be picked up the same way.

## 2. Point `CLAUDE.md` at it

Add one line to the invariants file, so every session loads it:

```md
Visual design — the mark, the theme tokens and the UI's ornament rules:
`docs/design/BRIEF.md`. Themes are four custom properties that switch the
page and the logo together; do not add a per-theme logo asset.
```

That is the whole handover. `BRIEF.md` is written to be read cold: it
says what was decided, what was tried and dropped, and what is still
open, so a fresh session does not re-litigate the drum-from-above or
propose a dark-mode PNG.

## 3. First task to give it

Something concrete, so the assets get exercised rather than discussed:

> Read `docs/design/BRIEF.md`. Wire `css/prax-themes.css` into the web UI
> at `/ui/`, inline `assets/logo/prax-mark.svg` into the masthead using
> `snippets/masthead.html` as the shape, and add a theme setting to
> `prax.yaml` that writes `data-prax-theme` on the root element.
> Respect the reduction ladder: the sidebar and the tab favicon take the
> medium and solid files, not a scaled-down mark.

## 4. What is deliberately not here

- **No PNGs.** Everything is SVG and the mark is three fills and one
  stroke; raster it at build time if something needs it.
- **No icon set.** The lozenge and the corner tick are the only two
  ornaments, and both are in `snippets/ornament.css`.
- **No component library.** The masthead snippet is a shape to copy, not
  a dependency.

## 5. The exploration behind it

The full canvas — the rejected marks, the six colour worlds, the
Victorian label, the plate drawn large with its dimension ticks, and the
theme comparison — lives at:

https://claude.ai/code/artifact/5d97a396-7065-423e-bc58-b8a0eec45057

Useful for deciding, not needed for building. `BRIEF.md` carries every
decision that came out of it.
