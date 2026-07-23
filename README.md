# Sketch2AI

A tiny, single-file whiteboard for explaining ideas to Claude with a picture
instead of a paragraph. Draw a box with an arrow, and it is on your clipboard —
or, in the terminal, one keypress away from your Claude Code session. Claude can
also draw diagrams back onto the board that you then rearrange by hand.

Sometimes it is faster to draw the thing than to describe it. That is the whole idea.

**Try it:** https://hz47.github.io/Sketch2AI/

<p align="center">
  <img src="screenshot.png" alt="Sketch2AI whiteboard" width="620">
</p>

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Use it with Claude Code (macOS)](#use-it-with-claude-code-macos)
  - [`/sketch` — draw, and hand it to Claude](#sketch--draw-and-hand-it-to-claude)
  - [`/sketch-diagram` — Claude draws for you](#sketch-diagram--claude-draws-for-you)
  - [Setup](#setup)
- [Editing diagrams (draw.io-style)](#editing-diagrams-drawio-style)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Notes](#notes)
- [Built with](#built-with)

## What it does

- Draw boxes, ellipses, lines, and arrows. Hold **Shift** to snap to squares,
  circles, and clean angles.
- Double-click a box or ellipse to type a label inside it.
- Freehand **pen**, a **highlighter** for marking up screenshots, and an **eraser**.
- Paste a screenshot or text with **Cmd+V**, or drag image files in from Finder.
- Move, resize, duplicate, and layer anything; drag a marquee to select several.
- Endless canvas — scroll to pan, Cmd+scroll to zoom.
- Every change copies the visible canvas to your clipboard, ready to paste into Claude.

There is a **Copy now** button (when auto-copy is off) and a **Save PNG** button
that downloads the board as a file.

## Quick start

1. Open `index.html` in Chrome (double-click it, or right-click → Open With → Chrome).
2. Draw something.
3. Switch to Claude and press **Cmd+V**.

No install, no account, no server — one HTML file with everything inside it.

> **Make it feel like an app (optional):** in Chrome, menu → *Install page as app*
> (older Chrome: *Create Shortcut* → *Open as window*). You get a dock icon and its
> own window.

## Use it with Claude Code (macOS)

Two commands connect the board to a **local** Claude Code session. Both open a
browser on your machine, so they only work with Claude Code running locally on a
Mac — not in a remote or cloud session. If launched somewhere they cannot work,
they exit immediately with a message instead of hanging.

### `/sketch` — draw, and hand it to Claude

Type `/sketch` in your terminal → the whiteboard opens → draw → press **Cmd+Return**.
The drawing lands in your Claude Code session and focus jumps back to the terminal.
One keypress to send, no clicks to return.

Under the hood, `tools/sketch-bridge.py` serves the board on `127.0.0.1`, catches
the drawing you send, saves it as a PNG, and prints the path for Claude to read.
The board posts the image back to the same local server (same origin — no clipboard
or CORS hurdles). The **Send to Claude** button only appears when the page is opened
with `?bridge=1`, so the hosted site is unchanged.

### `/sketch-diagram` — Claude draws for you

Describe a diagram and Claude draws it onto the board as real, editable shapes:

```
/sketch-diagram the login flow with an email-verification step
```

Boxes, arrows, and labels appear, laid out automatically. Every element is an
ordinary Sketch2AI shape — drag it, relabel it, connect it, or delete it, and mix
it with your own drawing. `tools/sketch-diagram-bridge.py` lays out a node/edge
graph in Python and injects it through a `?diagram=1` gate.

This is the co-editing loop: **ask Claude for a diagram → rearrange and extend it
by hand → send it back with `/sketch`** and iterate.

### Setup

Ready-to-use command templates live in [`commands/`](commands/).

1. Clone this repo somewhere on your Mac.
2. Copy the command files into your global commands folder (so they work in any
   session), then replace the placeholder path with where you cloned the repo:

   ```sh
   cp commands/sketch.md commands/sketch-diagram.md ~/.claude/commands/
   # then edit each file: /ABSOLUTE/PATH/TO/Sketch2AI → your clone path
   ```

   (Use plain `python3` instead of `uv run python3` if you do not use uv. You can
   also put the files in a project's `.claude/commands/` to scope them to that project.)
3. The first run may prompt macOS to let your terminal control System Events
   (used to refocus the terminal). Allow it once.

## Editing diagrams (draw.io-style)

Diagrams are made of normal shapes, with connectors that behave like draw.io:

- **Move** a box → its arrows and labels follow, staying attached to the edges.
- **Connect** — hover a box to reveal four connection ports, then drag from a port
  onto another box. Drop on a **port dot** to pin the arrow to that exact spot;
  drop **inside** the box to attach it floating (routes to the facing edge).
- **Re-route** — select an arrow, grab an endpoint handle, and drag it to a
  different box (or to empty space to detach it).
- **Delete** — select an arrow and press **Delete**.

## Keyboard shortcuts

| Keys | Action |
|------|--------|
| `P` / `G` / `E` | Pen / Highlighter / Eraser |
| `R` / `O` / `L` / `A` | Rectangle / Ellipse / Line / Arrow |
| `T` / `M` / `H` | Text / Move-select / Pan (or hold Space) |
| `1` | Fit everything in view |
| `Cmd +` / `Cmd -` / `Cmd 0` | Zoom in / out / reset to 100% |
| `Cmd+D` | Duplicate the selection |
| `Cmd+]` / `Cmd+[` | Bring to front / send to back |
| `Cmd+Z` | Undo the last added item |
| `Cmd+C` / `Cmd+V` | Copy the drawing / paste image or text |
| `Delete` | Remove the selected items |

## Notes

**Clipboard.** Copying needs the page to run as a local file or a real website. In
a sandboxed preview the browser blocks clipboard access ("Copy blocked"); running
the local file in Chrome fixes it. If a copy is ever blocked, use **Save PNG** and
drag the file in.

**Pasting into Claude.** Image paste works in the Claude desktop app and on
claude.ai. For the Claude Code terminal, use `/sketch` (above), which routes the
drawing in as a file rather than through the clipboard.

## Built with

Plain HTML, CSS, and JavaScript in one file — no libraries, no build step. The
drawing runs on an HTML canvas; the Claude Code bridges are standard-library
Python. See [`CLAUDE.md`](CLAUDE.md) for architecture notes.

## License

MIT. Do whatever you like with it.
