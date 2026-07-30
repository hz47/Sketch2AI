# CLAUDE.md — Sketch2AI

Guidance for working in this repo.

## What this is

Sketch2AI is a single-file, infinite-canvas whiteboard. You draw shapes, arrows,
freehand, text, and pasted images, and the visible canvas is copied to the
clipboard as a PNG so you can paste it into Claude. It is a visual supplement to
the text you type into Claude.

Two things grew out of that: diagrams are editable the way draw.io is
(connectors that stay attached, a format bar, connector labels), and a board can
be kept — as a `.json` document you reopen and keep editing, and as a
localStorage autosave — rather than only exported as a picture.

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
- `commands/` — copy-paste templates for the two Claude Code slash commands.
- `README.md` — user-facing description and shortcuts.
- `screenshot.png` — the image in the README.
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
  they are ordinary items, everything is immediately draggable/editable. It also
  starts the board in select mode instead of with the pen, since an injected
  diagram is there to be rearranged.
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

### One board across several calls

Repeated `/sketch-diagram` calls extend one board instead of opening a new tab
each time. The server always binds the fixed `DIAGRAM_PORT`, and `SESSION_FILE`
remembers what has been drawn (until `SESSION_TTL_S` passes). A later call
appends only its new nodes and pushes that delta to the open tab over
Server-Sent Events (`/events`), which the tab's `EventSource` reconnects to on
its own; each push carries a `pushId` so a reconnect never applies one twice.
Nothing already on the canvas is moved or redrawn.

The board reports its live box list back to `/sync` after each change
(`syncDiagramState()` in `index.html`), so `known_boxes()` sees boxes the user
drew or relabelled by hand, not just Claude's own. `--list-boxes` prints that
list as `id<TAB>label`, which lets a new call's edges attach to an existing box
by its real id: an id already known is used as-is instead of getting this
push's `dgm:` prefix.

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

Edge labels are placed by `placeLabels()`, called at the end of
`refreshConnectors()` so it sees every re-routed line. A label does not sit on
its line: it steps sideways along the line normal, which flips with edge
direction, so the labels of `a->b` and `b->a` (identical midpoints) land on
opposite sides. `LABEL_SPOTS` is the candidate list, cheapest first, sliding
along the line and stepping further off it; the first candidate that clears
every box and every label already placed wins. `drawItem` also paints a
sheet-colored plate under a label carrying `from`/`to`, so a line crossing
behind it cannot run through the letters. Hand-placed text has no `from`/`to`,
so it gets neither the plate nor the repositioning. The diagram bridge emits its
labels after all its arrows for the same reason (z-order).

`fanOutParallel()` spreads connectors that share a box pair. Both ends of such a
group route to the same boundary points, so an `a->b` / `b->a` pair used to draw
as one double-headed line (two dashed ones even interleave into an apparently
solid one). The whole group is offset along **one** reference normal, `group[0]`'s:
each member's own normal flips with its direction and would push them all the
same way.

Double-clicking a connector labels it (`openConnectorLabel`), draw.io style: it
reuses the existing `from`/`to` label if there is one, otherwise it creates a
text item carrying the same `from`/`to` so `placeLabels` owns its position. A
loose line (no attachment) gets plain text at its midpoint instead.

### Format bar

`#stylebar` is a floating bar over the stage, shown while a shape is selected
(`syncStyleBar()`, called at the end of `render()` and cheap because it early-outs
on an unchanged signature). It sets line weight, dash pattern, and the arrowhead;
the arrowhead group hides for boxes. `restyle()` applies each change to every
selected shape and commits.

The presets and the diagram bridge deliberately share numbers: thin `1.2` =
`EDGE_LINE`, dashed `[4.5, 3.5]` = `EDGE_DASH`, solid head `headLen(1.2)` = `8` =
`EDGE_HEAD`, and the slate swatch `#5b6577` = `EDGE_INK`. That is what lets a
hand-drawn arrow be restyled into an exact match for a generated connector, so
keep them in sync if you change either side.

## Run and test

Open `index.html` in Chrome, or serve the folder:

```
python3 -m http.server 8747 --directory .
```

(If the repo is checked out somewhere that manages Python with uv, use
`uv run python3` instead — the scripts themselves need nothing but the standard
library either way.)

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

  An optional `dash` (a `setLineDash` pattern in world units, or `true` for the
  default) strokes the outline dashed. An arrowhead always stays solid. The
  diagram bridge sets it on connectors so they read lighter than the boxes; the
  drawing tools never set it, so hand shapes stay solid. On an `arrow`, `head`
  overrides the arrowhead length (the default scales for pen strokes and is too
  big on a thin connector) and `headFill` closes it into a solid triangle. All
  three are set either by the diagram bridge or by the format bar.

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
`Shift+Cmd+Z` (or `Cmd+Y`) redo, `Cmd+C` copy, `Cmd+V` paste, `Cmd+S` save the
board as a file, `Cmd+O` open one, `Delete` remove selection, `Esc` clear
selection or cancel text editing.

## History, files, and autosave

Undo/redo is a stack of whole-board snapshots, not a log of added items.
`commit()` calls `recordHistory()`, which pushes the state as of the *previous*
commit and takes a fresh one — so `doUndo` steps back through edits (a move, a
resize, a restyle, a label, a delete, a clear) one at a time, and `doRedo`
replays them. `applySnapshot()` always clears the selection, because the old
selection array points at objects the snapshot just replaced. `HISTORY_MAX`
caps the stack. `resetHistory()` is for opening a different board, where undoing
into the replaced document would be nonsense. A `/sketch-diagram` push records
exactly one step, so undo takes the injected diagram back whole.

`serializeBoard()` / `loadBoard()` are the JSON document format
(`{app, version, view, items}`), used by three things: the Save/Open buttons
(`⌘S` / `⌘O`, plus dropping a `.json` on the canvas), and the localStorage
autosave under `sketch2ai.board.v1`. Autosave is debounced off `render()`, so a
drag writes once when it settles; on quota failure it turns itself off and says
so rather than throwing on every frame. `restoreLocal()` runs at init but skips
`?bridge=1` / `?diagram=1`, where the local server owns the canvas and a restore
would duplicate what it pushes.

Image items hold a live `<img>`, so serialization swaps it for a `src` data URL
(`imageSrc()` re-encodes `blob:` and file URLs through a canvas; a cross-origin
image taints that canvas, so it keeps the URL instead). Snapshots do the
opposite and share the decoded `<img>` — they never leave the page.

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
