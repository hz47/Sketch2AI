# Sketch2AI

A tiny drawing board that copies whatever you make straight to your clipboard, so you can paste it into Claude (or any chat that accepts images) and explain what you mean with a picture instead of words.

Sometimes it is faster to draw a box with an arrow than to describe it in a paragraph. That is the whole idea.

## What it does

- Draw with a pen in four colors and three sizes.
- Paste a screenshot or copied text right onto the board with Cmd+V.
- Drag image files in from Finder.
- Add text notes, then move and resize anything.
- Every time you finish a stroke or change something, the whole board is copied to your clipboard. Then you just paste it into Claude.

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

- `P` pen
- `T` text
- `M` move and resize
- `E` eraser
- `Cmd+Z` undo the last thing you added
- `Cmd+C` copy the board now
- `Cmd+V` paste an image or text onto the board
- `Delete` remove the selected item

## Built with

Plain HTML, CSS, and JavaScript. No libraries, no build step. The drawing runs on an HTML canvas.

## License

MIT. Do whatever you like with it.
