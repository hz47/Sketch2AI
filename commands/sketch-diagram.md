---
description: Draw a diagram (from a description) onto the Sketch2AI canvas as editable shapes
---

<!-- Copy this file to ~/.claude/commands/sketch-diagram.md (or a project's
     .claude/commands/) and replace the path below with where you cloned the repo. -->

The user wants you to draw a diagram of what they describe below, rendered as
real, editable shapes on the Sketch2AI whiteboard.

## 1. Turn their request into a graph

Design the diagram, then express it as a JSON graph. Keep node labels short
(a few words). Give every node a stable `id`. Use `edges` for the arrows.

```json
{
  "direction": "TB",
  "nodes": [
    {"id": "a", "label": "Start", "shape": "rect"},
    {"id": "b", "label": "Decision?", "shape": "ellipse"}
  ],
  "edges": [
    {"from": "a", "to": "b"},
    {"from": "b", "to": "a", "label": "retry"}
  ]
}
```

- `direction`: `"TB"` (top-to-bottom) or `"LR"` (left-to-right).
- `shape`: `"rect"` (default) or `"ellipse"` — use ellipse for decisions /
  start-end nodes.
- `label` on an edge is optional (e.g. "yes", "no", "submit").
- Layout is automatic — do NOT compute coordinates. Cycles are fine.

## Always aim for a nice, readable diagram

The layout is a layered (rank-based) algorithm, so the shape of the graph
decides how good it looks:

- **Default to `TB`.** Use `LR` only for a short chain (≈4 nodes or fewer) or a
  genuinely wide/parallel structure. A long chain in `LR` becomes an ugly
  stretched sliver; the same chain in `TB` is a clean vertical flow.
- **Keep it to roughly 10 nodes or fewer.** For a large system, draw the core
  flow and merge minor steps into one box; offer to expand a section afterward.
- **Short labels** — a few words each.
- **Avoid long "shortcut" edges** that skip several ranks; they draw as a long
  line behind the intervening boxes. Prefer a mostly-linear or gently-branching
  DAG. Leave a shortcut path out and mention it, or restructure so ranks stay
  close.
- **Minimise back edges / crossings.** A decision splitting into two reads well;
  a tangle of long back edges does not.

## 2. Write the graph to a temp file

Write the JSON to a scratch file, e.g. `/tmp/sketch-diagram-graph.json`.

## 3. Draw it

Run this in the foreground (it opens the board, injects the diagram, then waits
for the user to optionally rearrange it and send it back before refocusing the
terminal and exiting — pass a Bash `timeout` of 600000 ms):

`uv run python3 /ABSOLUTE/PATH/TO/Sketch2AI/tools/sketch-diagram-bridge.py /tmp/sketch-diagram-graph.json`

- On success its first stdout line reports how many nodes it drew. Tell the
  user the diagram is on their canvas and that every box, arrow, and label is
  a normal Sketch2AI shape they can drag, relabel, restyle, connect, or delete.
- The board also shows a "Send to Claude" button (⌘↵) once the diagram loads.
  If the user rearranges or extends it by hand and sends it back within about
  9.5 minutes, a **second** stdout line appears: the path to a PNG of their
  edited version. If it's there, Read it to see what they changed and continue
  from that. If it's absent, they just wanted the diagram drawn — say so and
  move on, no need to wait or ask.
- If it exits non-zero, report the error from stderr and stop.

The user's diagram request:

$ARGUMENTS
