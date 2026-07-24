# CLAUDE.md — Sketch2AI

Guidance for working in this repo.

## What this is

Sketch2AI is a single-file, infinite-canvas whiteboard. You draw shapes, arrows,
freehand, text, and pasted images, and the visible canvas is copied to the
clipboard as a PNG so you can paste it into Claude. It is a visual supplement to
the text you type into Claude.

Everything lives in `index.html`: markup, CSS, and JavaScript in one file. There
is no build step, no framework, and no dependencies. Keep it that way unless
there is a strong reason not to.

## Files

- `index.html` — the whole app.
- `tools/sketch-bridge.py` — local bridge for the Claude Code `/sketch` command
  (see below). Not loaded by the app; run separately by the CLI command.
- `tools/sketch-diagram-bridge.py` — local bridge for the `/sketch-diagram`
  command: lays out a node/edge graph and injects it onto the canvas as editable
  shapes (see below). Independent of `sketch-bridge.py`.
- `README.md` — user-facing description and shortcuts.
- `LICENSE` — MIT.
- `CLAUDE.md` — this file.

## Claude Code bridge (`/sketch`)

`tools/sketch-bridge.py` is a standard-library HTTP server that lets a drawing go
straight from the board into a local Claude Code session. Flow: the CLI command
runs the script; it serves `index.html` on `127.0.0.1:<free-port>` and opens it
with `?bridge=1`; the page then shows a "Send to Claude" button (and a Cmd+Return
shortcut) that POSTs the exported PNG to `/submit` on the same origin; the server
writes the PNG to a temp file, refocuses the terminal via `osascript`, prints the
path to stdout, and exits so the command can Read it.

- The button/shortcut live in the `bridgeMode()` IIFE in `index.html`, gated on
  the `?bridge=1` query param, so the hosted page never shows them.
- Same-origin is the whole trick: no clipboard permission, no CORS. Do not move
  the POST target off the serving origin.
- Local macOS only by nature (it opens a browser for a human to draw in). The
  script fails fast on non-Darwin or when `open` is missing rather than hanging.

## Diagram bridge (`/sketch-diagram`)

`tools/sketch-diagram-bridge.py` is the reverse direction: Claude describes a
diagram as a `{direction, nodes, edges}` graph, the script lays it out (a pure
Python layered / Sugiyama-style algorithm — no JS libraries, so `index.html`
stays self-contained), converts it to Sketch2AI items (rounded-rect/ellipse
nodes with centered labels, arrows between box boundaries, optional edge
labels), serves the board, and opens it with `?diagram=1`.

- The board's `diagramMode()` IIFE (gated on `?diagram=1`) fetches `/diagram`
  and pushes the items straight onto the canvas, then `fitToContent()`. Because
  they are ordinary items, everything is immediately draggable/editable.
- Layout lives in Python on purpose: LLMs are reliable at graph *semantics* but
  not pixel coordinates, so Claude only emits nodes/edges and the code does
  geometry. Keep it that way.
- Separate server process from the draw bridge: own pidfile, own
  single-instance guard, same local-macOS-only fail-fast.
- Closes the co-editing loop in one command: `bridgeMode()` in `index.html` is
  gated on `?bridge=1` **or** `?diagram=1`, so the "Send to Claude" button
  (⌘↵, POST to `/submit`) also appears on an injected diagram. The Python
  script gained its own `/submit` handler (mirrors `sketch-bridge.py`) and,
  after the diagram loads, blocks up to `SUBMIT_TIMEOUT_S` (570s) waiting for
  it. A submission is optional and its absence is not an error: stdout's first
  line is always the "drew N nodes" confirmation; a second line with the
  edited PNG's path appears only if the user sent one back.

### Connected arrows (draw.io-style)

Boxes (`rect`/`ellipse`) carry a stable `id`; arrows and labels carry `from`/`to`
referencing those ids. `refreshConnectors()` runs before every render/export and
re-routes connected items to the current box boundaries, so moving or resizing a
box drags its arrows and labels along. It handles both-ends-attached and
one-end-attached (the free end stays put). Items without `from`/`to` are never
touched, so hand drawing does not regress.

Two ways to connect, both in select mode:
- Hover a box → four connection ports (edge midpoints) appear; drag from a port
  onto another box to create a connector (`boxPorts`/`portHit`/`drawPorts`, and
  the `connect` pointer mode / `finalizeConnect`).
- Draw an arrow with the arrow tool whose ends land on boxes — `finalizeCreate`
  attaches it the same way.

## Run and test

Open `index.html` in Chrome, or serve the folder:

```
uv run python3 -m http.server 8747 --directory .
```

Note: in this environment use `uv run python3`, not bare `python3`.

To test interactively, drive Chrome and check the console for errors. Synthetic
`PointerEvent`s return an empty `getCoalescedEvents()`, so freehand pen and
highlighter strokes will not accumulate points in a script unless you first
patch `PointerEvent.prototype.getCoalescedEvents = function(){ return [this]; }`.
Shapes, labels, select, and duplicate work with plain dispatched events.

## Architecture

### Coordinate system

The canvas is an infinite world. A `view = { scale, x, y }` maps world to screen:
`screen = world * scale + offset`. Helpers `toWorld(sx, sy)` and
`toScreen(wx, wy)` convert between the two. All item coordinates are stored in
world units. Pan changes `view.x/y`; zoom changes `view.scale` around the cursor.

### Item model

`items` is a flat, z-ordered array (later items draw on top). Types:

- `stroke` — `{ points:[{x,y}], color, size, eraser, highlight }`. Freehand.
  `eraser` paints white; `highlight` draws wide and translucent with `multiply`.
- `image` — `{ img, x, y, w, h }`.
- `text` — `{ text, x, y, size, color, _w, _h }`. `_w/_h` are cached measurements,
  set to `null` to force re-measure.
- `shape` — `{ kind, color, size, text?, textSize? }` where `kind` is:
  - `rect` / `ellipse`: `x, y, w, h` (normalized positive after creation). An
    optional `text` label renders centered inside.
  - `line` / `arrow`: `x1, y1, x2, y2`. `arrow` adds a computed arrowhead.

Only `image`, `text`, and `shape` are selectable. Strokes are background ink.

`selection` is an array of items. Single selection (`length === 1`) shows resize
handles; multi-selection shows outlines only and moves as a group.

### Rendering

`render(overlay = true)` does, in order: clear to white (screen space), dot grid
(screen space), all items (world transform via `worldT()`), then selection and
marquee overlays (screen space via `screenT()`). During an active freehand
stroke, the current stroke is drawn incrementally on top instead of a full
re-render, for smoothness.

`drawItem(g, it)` takes a target context so the same code renders to the visible
canvas and to the offscreen export canvas.

### Pointer modes

`pointerdown` sets `mode` to one of: `draw` (pen/highlight/eraser), `create`
(shapes), `move`, `resize`, `marquee`, or `pan`. `pointermove` dispatches on
`mode`; `pointerup` finalizes and calls `commit(true)` when something changed.
Pan is triggered by the Pan tool, holding Space, or the middle mouse button.

### Export and clipboard

`renderExport()` captures only the visible viewport onto an offscreen canvas, on
white, without the grid or selection handles, at `min(dpr, 2)` resolution. Copy
and Save PNG both use it, so output is a normal viewport-sized image regardless
of how large the drawing is.

`copyToClipboard` hands the PNG to `ClipboardItem` as a Promise so the write
starts inside the user gesture. Awaiting the blob first loses the gesture and
Safari and Chrome silently drop the copy.

## Known constraints and gotchas

- The clipboard needs a secure context (https, localhost, or a local file) and a
  real user gesture. Sandboxed iframes (for example the claude.ai artifact
  preview) block it with `NotAllowedError`. That is why this ships as a hosted
  page and a local file rather than an inline artifact. `Save PNG` is the
  fallback when a copy is blocked.
- Pasted images paste into the Claude desktop app and claude.ai, not the Claude
  Code terminal, which only accepts text.
- The file has no `<head>`; it starts with `<meta charset="utf-8">` then
  `<title>`. Without the charset, symbols like the command glyph, middot, and
  check mark mis-decode when the file is served raw (file:// or plain HTTP).
  GitHub Pages forces UTF-8, so the bug only shows off-Pages.
- Theme follows the OS via CSS custom properties and `prefers-color-scheme`.
  The drawing sheet stays white in both themes on purpose, because white is what
  pastes cleanly into Claude.

## Keyboard shortcuts

Tools: `P` pen, `G` highlighter, `E` eraser, `R` rectangle, `O` ellipse,
`L` line, `A` arrow, `T` text, `M` move, `H` pan.
View: `1` fit, `Cmd +` / `Cmd -` / `Cmd 0` zoom.
Edit: `Cmd+D` duplicate, `Cmd+]` / `Cmd+[` layer front/back, `Cmd+Z` undo,
`Cmd+C` copy, `Cmd+V` paste, `Delete` remove selection, `Esc` clear selection or
cancel text editing.

Undo removes the last added item only. It does not step back individual moves,
resizes, or label edits.

## Deployment

- Repo: `hz47/Sketch2AI` (public). GitHub Pages serves the `main` branch root at
  https://hz47.github.io/Sketch2AI/. Pushing to `main` redeploys after the Pages
  build finishes (about half a minute).
- The account root https://hz47.github.io/ is a separate repo, `hz47/hz47.github.io`
  (branch `master`), holding a single `index.html` that redirects to
  `/Sketch2AI/`. 

## Conventions

- Keep it one self-contained file. Inline CSS and JS, no external requests.
- No em dashes in user-facing copy.
- Match the existing code style: small helper functions, world coordinates for
  stored geometry, screen coordinates only for input and overlays.
- After a change, syntax-check the script (`node --check` on the extracted
  `<script>`) and, for anything non-trivial, verify in a real browser.
