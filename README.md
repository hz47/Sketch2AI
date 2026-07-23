# Sketch2AI

A tiny drawing board that copies whatever you make straight to your clipboard, so you can paste it into Claude (or any chat that accepts images) and explain what you mean with a picture instead of words.

Sometimes it is faster to draw a box with an arrow than to describe it in a paragraph. That is the whole idea.

Try it here: https://hz47.github.io/Sketch2AI/

<img src="screenshot.png" alt="Sketch2AI whiteboard" width="640">

## What it does

- Draw boxes, ellipses, lines, and arrows for flows and diagrams. Hold Shift to snap to squares, circles, and clean angles.
- Double-click a box or ellipse to type a label inside it, so a shape becomes a named component.
- Draw freehand with a pen, or use the highlighter to circle and emphasize parts of a screenshot.
- Paste a screenshot or copied text right onto the board with Cmd+V, and drag image files in from Finder.
- Move, resize, duplicate, and layer anything. Drag a box around several items to select them all at once.
- Work on an endless canvas. Scroll to pan, pinch or Cmd+scroll to zoom, and keep adding content wherever you like.
- Every time you change something, the visible area of the canvas is copied to your clipboard. Pan and zoom to frame what you want, and that is what gets copied. Then you paste it into Claude.

It is meant to sit next to the text you write to Claude: draw the thing, paste the picture, and let the words and the sketch explain the idea together.

There is also a "Copy now" button if you turn off auto-copy, and a "Save PNG" button that downloads the board as a file.

## How to use it

1. Open `index.html` in Chrome (double-click the file, or right-click and choose Open With, then Google Chrome).
2. Draw something.
3. Switch to Claude and press Cmd+V.

That is it. No install, no account, no server. It is one HTML file with everything inside it.

### Make it feel like a real app (optional)

Open the page in Chrome, then go to the menu and pick "Install page as app" (older Chrome calls it "Create Shortcut" with the "Open as window" option). You get a dock icon and its own window.

## A note about the clipboard

The copy feature needs the page to run as a local file or a real website. If you open it inside a sandboxed preview, the browser blocks clipboard access and you will see a "Copy blocked" message. Running the local file in Chrome fixes it. If a copy is ever blocked, use the "Save PNG" button and drag the file in instead.

Pasting an image works in the Claude desktop app and on claude.ai. It does not work in the Claude Code terminal, which only takes text.

## Keyboard shortcuts

- `P` pen, `G` highlighter, `E` eraser
- `R` rectangle, `O` ellipse, `L` line, `A` arrow
- `T` text, `M` move and select
- `H` pan (or hold Space and drag, or drag with the middle mouse button)
- `1` fit everything in view
- `Cmd +` / `Cmd -` / `Cmd 0` zoom in, out, reset to 100%
- `Cmd+D` duplicate the selection
- `Cmd+]` / `Cmd+[` bring to front, send to back
- `Cmd+Z` undo the last thing you added
- `Cmd+C` copy the drawing now
- `Cmd+V` paste an image or text onto the canvas
- `Delete` remove the selected items

## Built with

Plain HTML, CSS, and JavaScript. No libraries, no build step. The drawing runs on an HTML canvas.

## License

MIT. Do whatever you like with it.
