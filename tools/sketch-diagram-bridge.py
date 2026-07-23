#!/usr/bin/env python3
"""Sketch2AI diagram bridge for the Claude Code /sketch-diagram command.

Reads a graph description (nodes + edges) as JSON, lays it out with a layered
(Sugiyama-style) algorithm, converts it to Sketch2AI canvas items (rounded-rect
nodes with centered labels, arrows between them, optional edge labels), serves
the whiteboard, and opens it with ?diagram=1 so the board drops the items onto
the canvas as ordinary, editable shapes.

This is a separate, self-contained sibling of sketch-bridge.py — the draw-to-Claude
bridge is untouched. Layout runs here in Python, so index.html needs no libraries.

Input JSON (from a file path argument, or stdin):

    {
      "direction": "TB",                 # TB (top-bottom, default) or LR (left-right)
      "nodes": [{"id": "a", "label": "Start", "shape": "rect"}],
      "edges": [{"from": "a", "to": "b", "label": "yes"}]
    }

Nodes referenced by an edge but never declared are created automatically, so the
input is forgiving. `shape` may be "rect" (default) or "ellipse".
"""
import http.server
import json
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
PIDFILE = os.path.join(tempfile.gettempdir(), "sketch-diagram-bridge.pid")
SERVE_TIMEOUT_S = 120  # if the board never loads, give up rather than hang

# --- visual constants (world units, matching the Sketch2AI item model) ---
INK = "#191d24"
MUTED = "#626d84"
NODE_LINE = 3
EDGE_LINE = 2.5
FONT_SIZE = 16
EDGE_LABEL_SIZE = 13
CHAR_W = FONT_SIZE * 0.58          # rough advance width for the sans label font
PAD_X, PAD_Y = 18, 12
MIN_W, MIN_H = 96, 46
WRAP_CHARS = 20                    # wrap long labels to keep boxes reasonable
RANK_GAP = 84                      # space between layers
NODE_GAP = 46                      # space between nodes within a layer
MARGIN = 60

served = threading.Event()


# ---------------------------------------------------------------- layout ----
def _wrap(label):
    """Wrap a label to a handful of lines on word boundaries."""
    words, lines, cur = str(label).split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) > WRAP_CHARS and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def _node_size(label):
    lines = _wrap(label)
    w = max(MIN_W, int(max(len(ln) for ln in lines) * CHAR_W) + 2 * PAD_X)
    h = max(MIN_H, int(len(lines) * FONT_SIZE * 1.25) + 2 * PAD_Y)
    return w, h, "\n".join(lines)


def _rank_nodes(ids, edges_dag):
    """Longest-path layering over a DAG. Returns {id: rank}."""
    succ = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for a, b in edges_dag:
        succ[a].append(b)
        indeg[b] += 1
    # Kahn topological order.
    queue = [i for i in ids if indeg[i] == 0]
    order, indeg2 = [], dict(indeg)
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in succ[n]:
            indeg2[m] -= 1
            if indeg2[m] == 0:
                queue.append(m)
    rank = {i: 0 for i in ids}
    for n in order:
        for m in succ[n]:
            if rank[m] < rank[n] + 1:
                rank[m] = rank[n] + 1
    return rank


def _break_cycles(ids, edges):
    """Split edges into DAG edges (for ranking) keeping all for drawing.

    Depth-first: an edge to a node currently on the recursion stack is a back
    edge and is excluded from ranking so the layering stays acyclic.
    """
    succ = {i: [] for i in ids}
    for a, b in edges:
        if a in succ:
            succ[a].append(b)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}
    back = set()

    def dfs(u):
        color[u] = GREY
        for v in succ[u]:
            if color.get(v) == GREY:
                back.add((u, v))
            elif color.get(v) == WHITE:
                dfs(v)
        color[u] = BLACK

    for i in ids:
        if color[i] == WHITE:
            dfs(i)
    dag = [(a, b) for (a, b) in edges if (a, b) not in back]
    return dag


def _order_layers(layers, edges, pos):
    """A few barycenter sweeps to reduce edge crossings within layers."""
    preds = {}
    succs = {}
    for a, b in edges:
        succs.setdefault(a, []).append(b)
        preds.setdefault(b, []).append(a)

    def sweep(layer_indices, neigh):
        for r in layer_indices:
            layer = layers[r]
            if len(layer) < 2:
                continue
            keyed = []
            for i, n in enumerate(layer):
                ns = [pos[x] for x in neigh.get(n, []) if x in pos]
                bary = sum(ns) / len(ns) if ns else pos[n]
                keyed.append((bary, i, n))
            keyed.sort()
            layers[r] = [n for _, _, n in keyed]
            for idx, n in enumerate(layers[r]):
                pos[n] = idx

    for _ in range(4):
        sweep(range(1, len(layers)), preds)          # top-down by parents
        sweep(range(len(layers) - 2, -1, -1), succs)  # bottom-up by children


def _box_edge_point(cx, cy, hw, hh, tx, ty):
    """Point where the ray from box center toward (tx,ty) exits the box."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    if dx == 0:
        return cx, cy + (hh if dy > 0 else -hh)
    if dy == 0:
        return cx + (hw if dx > 0 else -hw), cy
    s = min(hw / abs(dx), hh / abs(dy))
    return cx + dx * s, cy + dy * s


def layout(graph):
    """Turn a {nodes, edges, direction} graph into Sketch2AI items."""
    direction = str(graph.get("direction", "TB")).upper()
    horizontal = direction in ("LR", "RL")

    raw_nodes = graph.get("nodes") or []
    raw_edges = graph.get("edges") or []

    # Index nodes; auto-create any referenced-but-undeclared node.
    node = {}
    order_ids = []

    def ensure(nid, label=None, shape="rect"):
        nid = str(nid)
        if nid not in node:
            node[nid] = {"id": nid, "label": label if label is not None else nid,
                         "shape": shape}
            order_ids.append(nid)
        elif label is not None:
            node[nid]["label"] = label
        return nid

    for n in raw_nodes:
        if isinstance(n, dict) and "id" in n:
            ensure(n["id"], n.get("label"), n.get("shape", "rect"))

    edges = []
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        a, b = e.get("from"), e.get("to")
        if a is None or b is None:
            continue
        a, b = ensure(a), ensure(b)
        edges.append({"from": a, "to": b, "label": e.get("label")})

    if not order_ids:
        return []

    edge_pairs = [(e["from"], e["to"]) for e in edges]
    dag = _break_cycles(order_ids, edge_pairs)
    rank = _rank_nodes(order_ids, dag)

    # Group into layers, preserving declaration order as the initial ordering.
    max_rank = max(rank.values())
    layers = [[] for _ in range(max_rank + 1)]
    for nid in order_ids:
        layers[rank[nid]].append(nid)
    pos = {}
    for layer in layers:
        for i, nid in enumerate(layer):
            pos[nid] = i
    _order_layers(layers, edge_pairs, pos)

    # Size every node.
    for nid in order_ids:
        w, h, text = _node_size(node[nid]["label"])
        node[nid].update(w=w, h=h, text=text)

    # Assign centers. "cross" axis packs nodes within a layer; "rank" axis
    # steps between layers. For TB rank=y, cross=x; for LR rank=x, cross=y.
    centers = {}
    rank_pos = 0.0
    for layer in layers:
        if not layer:
            continue
        thickness = max((node[n]["h"] if not horizontal else node[n]["w"]) for n in layer)
        # total cross extent of this layer
        cross_sizes = [(node[n]["w"] if not horizontal else node[n]["h"]) for n in layer]
        total = sum(cross_sizes) + NODE_GAP * (len(layer) - 1)
        cross = -total / 2.0
        for n, cs in zip(layer, cross_sizes):
            c_cross = cross + cs / 2.0
            c_rank = rank_pos + thickness / 2.0
            if horizontal:
                centers[n] = (c_rank, c_cross)
            else:
                centers[n] = (c_cross, c_rank)
            cross += cs + NODE_GAP
        rank_pos += thickness + RANK_GAP

    # Normalize to positive coordinates with a margin.
    minx = min(centers[n][0] - node[n]["w"] / 2 for n in order_ids)
    miny = min(centers[n][1] - node[n]["h"] / 2 for n in order_ids)
    ox, oy = MARGIN - minx, MARGIN - miny
    for n in order_ids:
        cx, cy = centers[n]
        centers[n] = (cx + ox, cy + oy)

    # Stable ids so arrows/labels can stay connected to their boxes when the
    # user drags things around on the canvas (see refreshConnectors in the app).
    nid_of = {n: "dgm:" + n for n in order_ids}

    items = []
    # Draw edges first so nodes sit on top.
    for e in edges:
        a, b = e["from"], e["to"]
        ax, ay = centers[a]
        bx, by = centers[b]
        x1, y1 = _box_edge_point(ax, ay, node[a]["w"] / 2, node[a]["h"] / 2, bx, by)
        x2, y2 = _box_edge_point(bx, by, node[b]["w"] / 2, node[b]["h"] / 2, ax, ay)
        items.append({"type": "shape", "kind": "arrow", "x1": round(x1, 1),
                      "y1": round(y1, 1), "x2": round(x2, 1), "y2": round(y2, 1),
                      "color": INK, "size": EDGE_LINE,
                      "from": nid_of[a], "to": nid_of[b]})
        if e.get("label"):
            lx, ly = (x1 + x2) / 2, (y1 + y2) / 2
            label = str(e["label"])
            items.append({"type": "text", "text": label,
                          "x": round(lx - len(label) * EDGE_LABEL_SIZE * 0.29, 1),
                          "y": round(ly - EDGE_LABEL_SIZE, 1),
                          "size": EDGE_LABEL_SIZE, "color": MUTED,
                          "from": nid_of[a], "to": nid_of[b]})

    for n in order_ids:
        cx, cy = centers[n]
        w, h = node[n]["w"], node[n]["h"]
        kind = "ellipse" if node[n].get("shape") == "ellipse" else "rect"
        items.append({"type": "shape", "kind": kind, "id": nid_of[n],
                      "x": round(cx - w / 2, 1), "y": round(cy - h / 2, 1),
                      "w": w, "h": h, "color": INK, "size": NODE_LINE,
                      "text": node[n]["text"], "textSize": FONT_SIZE})
    return items


# ---------------------------------------------------------- process glue ----
def _looks_like_bridge(pid):
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5)
        return "sketch-diagram-bridge.py" in out.stdout
    except Exception:
        return False


def ensure_single_instance():
    try:
        if os.path.exists(PIDFILE):
            with open(PIDFILE) as f:
                old = int(f.read().strip())
            if old != os.getpid() and _looks_like_bridge(old):
                os.kill(old, signal.SIGTERM)
    except (ValueError, ProcessLookupError, OSError):
        pass
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def frontmost_app():
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first '
             'process whose frontmost is true'],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


def activate_app(name):
    if not name:
        return
    subprocess.run(
        ["osascript", "-e",
         f'tell application "System Events" to set frontmost of process "{name}" to true'],
        check=False, capture_output=True)


def make_handler(payload):
    body = json.dumps({"items": payload}).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    with open(INDEX, "rb") as f:
                        html = f.read()
                except OSError as e:
                    self.send_error(500, str(e))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif path == "/diagram":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                served.set()
            else:
                self.send_error(404)

    return Handler


def read_graph():
    if len(sys.argv) > 1:
        path = sys.argv[1]
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            print(f"Could not read graph JSON from {path}: {e}", file=sys.stderr)
            sys.exit(2)
    data = sys.stdin.read()
    try:
        return json.loads(data)
    except ValueError as e:
        print(f"Could not parse graph JSON from stdin: {e}", file=sys.stderr)
        sys.exit(2)


def main():
    if sys.platform != "darwin" or not shutil.which("open"):
        print("/sketch-diagram only works in Claude Code running locally on a Mac "
              "— it opens a browser on your machine, so it can't run in a remote, "
              "cloud, or non-macOS session.", file=sys.stderr)
        sys.exit(3)
    if not os.path.exists(INDEX):
        print(f"Cannot find index.html at {INDEX}", file=sys.stderr)
        sys.exit(2)

    graph = read_graph()
    try:
        items = layout(graph)
    except Exception as e:
        print(f"Layout failed: {e}", file=sys.stderr)
        sys.exit(4)
    if not items:
        print("Graph had no nodes to draw.", file=sys.stderr)
        sys.exit(5)

    ensure_single_instance()
    caller = frontmost_app()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 0), make_handler(items)) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{port}/?diagram=1"
        node_count = sum(1 for it in items if it.get("type") == "shape"
                         and it.get("kind") in ("rect", "ellipse"))
        print(f"Diagram ready ({node_count} nodes) at {url}", file=sys.stderr)
        subprocess.run(["open", url], check=False)
        if not served.wait(SERVE_TIMEOUT_S):
            print("The board did not load the diagram in time.", file=sys.stderr)
            httpd.shutdown()
            sys.exit(1)
        time.sleep(0.4)  # let the response finish before we tear down
        httpd.shutdown()
    activate_app(caller)
    print(f"Drew {node_count} nodes onto the Sketch2AI canvas.")


if __name__ == "__main__":
    main()
