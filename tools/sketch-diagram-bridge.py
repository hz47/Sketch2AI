#!/usr/bin/env python3
"""Sketch2AI diagram bridge for the Claude Code /sketch-diagram command.

Reads a graph description (nodes + edges) as JSON, lays it out with a layered
(Sugiyama-style) algorithm, converts it to Sketch2AI canvas items (rounded-rect
nodes with centered labels, arrows between them, optional edge labels), serves
the whiteboard, and opens it with ?diagram=1 so the board drops the items onto
the canvas as ordinary, editable shapes.

Once the diagram is drawn, the server keeps running and the board shows a
"Send to Claude" button (same ?bridge-style POST /submit as sketch-bridge.py,
same origin). If the user rearranges or extends the diagram by hand and sends
it back, this prints the resulting PNG's path as its last stdout line — the
co-editing loop. If nothing is sent back before the timeout, it exits having
only drawn the diagram, same as before.

Repeated calls reuse one board tab instead of opening a new one each time:
the server always binds DIAGRAM_PORT, and a small session file (SESSION_FILE)
remembers what has been drawn so far. A later call appends its new nodes to
that session (never redrawing or moving anything already on the canvas — the
open tab, and whatever the user has done to it by hand, is left alone) and
pushes just the delta to the already-open tab over Server-Sent Events, which
the browser reconnects to automatically once the new call's server is up. A
call finds nothing to reuse (fresh session, or the last one is older than
SESSION_TTL_S) and opens a new tab instead.

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
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
PIDFILE = os.path.join(tempfile.gettempdir(), "sketch-diagram-bridge.pid")
SESSION_FILE = os.path.join(tempfile.gettempdir(), "sketch-diagram-session.json")
DIAGRAM_PORT = 48717  # fixed, so a later call can find the tab an earlier one opened
SESSION_TTL_S = 4 * 3600  # a session (and its open tab) older than this counts as gone
PUSH_GAP = 100  # world-unit gap placed between successive pushes' bounding boxes
SERVE_TIMEOUT_S = 120  # if the board never loads, give up rather than hang
SUBMIT_TIMEOUT_S = 570  # stay under the /sketch-diagram command's 10-minute Bash ceiling

# --- visual constants (world units, matching the Sketch2AI item model) ---
INK = "#191d24"
EDGE_INK = "#5b6577"          # connectors a shade lighter than the node borders
MUTED = "#626d84"
NODE_LINE = 1.8
EDGE_LINE = 1.2               # the board's "thin" preset, so the format bar can reproduce this
EDGE_DASH = [4.5, 3.5]        # short dashes, so a connector reads lighter than a box
EDGE_HEAD = 8                 # small arrowhead; the default is sized for pen strokes
FONT_SIZE = 16
EDGE_LABEL_SIZE = 13
CHAR_W = FONT_SIZE * 0.58          # rough advance width for the sans label font
PAD_X, PAD_Y = 18, 12
MIN_W, MIN_H = 96, 46
WRAP_CHARS = 20                    # wrap long labels to keep boxes reasonable
RANK_GAP = 84                      # space between layers
NODE_GAP = 46                      # space between nodes within a layer
MARGIN = 60

delivered = threading.Event()  # set once the new items reach a tab, fresh or reused
submitted = threading.Event()
submit_result = {"path": None}
session_lock = threading.Lock()  # guards read-modify-write of SESSION_FILE across requests


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


def layout(graph, known_ids=None, offset=(0, 0)):
    """Turn a {nodes, edges, direction} graph into Sketch2AI items.

    known_ids are box ids (see nid_of below) already drawn by an earlier push
    in this session: they may still be referenced by new edges (so a new node
    can connect to an existing one) but no box item is emitted for them again
    — the live canvas, and whatever the user did to that box by hand, is left
    alone. offset shifts every new coordinate, so a later push lands clear of
    what is already on the canvas instead of on top of it.
    """
    known_ids = known_ids or set()
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
    ox, oy = MARGIN - minx + offset[0], MARGIN - miny + offset[1]
    for n in order_ids:
        cx, cy = centers[n]
        centers[n] = (cx + ox, cy + oy)

    # Stable ids so arrows/labels can stay connected to their boxes when the
    # user drags things around on the canvas (see refreshConnectors in the
    # app). A graph id that already matches a known box exactly (e.g. "b1"
    # from --list-boxes, a hand-drawn box) is used as-is so the edge attaches
    # to that real box instead of minting a "dgm:b1" duplicate; anything else
    # gets this push's own "dgm:" namespace.
    nid_of = {n: (n if n in known_ids else "dgm:" + n) for n in order_ids}

    items = []
    labels = []   # appended after every arrow, so no arrow paints over a label
    slot = {}     # labels already placed per (from, to), to stack repeats
    # Draw edges first so nodes sit on top.
    for e in edges:
        a, b = e["from"], e["to"]
        ax, ay = centers[a]
        bx, by = centers[b]
        x1, y1 = _box_edge_point(ax, ay, node[a]["w"] / 2, node[a]["h"] / 2, bx, by)
        x2, y2 = _box_edge_point(bx, by, node[b]["w"] / 2, node[b]["h"] / 2, ax, ay)
        items.append({"type": "shape", "kind": "arrow", "x1": round(x1, 1),
                      "y1": round(y1, 1), "x2": round(x2, 1), "y2": round(y2, 1),
                      "color": EDGE_INK, "size": EDGE_LINE, "dash": EDGE_DASH,
                      "head": EDGE_HEAD, "headFill": True,
                      "from": nid_of[a], "to": nid_of[b]})
        if e.get("label"):
            label = str(e["label"])
            # Beside the line rather than on it (the board's refreshConnectors
            # keeps it there when boxes move): step off along the edge normal,
            # which flips with direction so a->b and b->a sit on opposite
            # sides, and stack any repeat of the same direction further out.
            lw = len(label) * EDGE_LABEL_SIZE * 0.58 + 6
            lh = EDGE_LABEL_SIZE * 1.25 + 4
            ln = math.hypot(x2 - x1, y2 - y1) or 1.0
            nx, ny = -(y2 - y1) / ln, (x2 - x1) / ln
            k = slot.get((a, b), 0)
            slot[(a, b)] = k + 1
            off = (abs(nx) * (lw / 2 + 5) + abs(ny) * (lh / 2 + 5)
                   + k * (abs(nx) * (lw + 4) + abs(ny) * (lh + 2)))
            lx = (x1 + x2) / 2 + nx * off
            ly = (y1 + y2) / 2 + ny * off
            labels.append({"type": "text", "text": label,
                           "x": round(lx - lw / 2, 1), "y": round(ly - lh / 2, 1),
                           "size": EDGE_LABEL_SIZE, "color": MUTED,
                           "from": nid_of[a], "to": nid_of[b]})
    items.extend(labels)

    for n in order_ids:
        if nid_of[n] in known_ids:
            continue  # already on the canvas from an earlier push; anchor only
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


def load_session(ignore_ttl=False):
    """The current session {items, push_id, max_x, max_y, live_boxes}, or None
    if there isn't a live one (never used, or its tab is presumed gone by
    now). ignore_ttl is for a /sync request updating a session this same
    still-running server already vouched for as live."""
    try:
        if not ignore_ttl and time.time() - os.path.getmtime(SESSION_FILE) > SESSION_TTL_S:
            return None
        with open(SESSION_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError):
        pass
    return None


def save_session(data):
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def make_handler(all_items, new_items, push_id):
    diagram_body = json.dumps({"items": all_items, "pushId": push_id}).encode("utf-8")
    new_body = json.dumps({"items": new_items, "pushId": push_id}).encode("utf-8")

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
                # A fresh tab's one-time load: everything drawn in this session
                # so far (earlier pushes plus this one).
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(diagram_body)))
                self.end_headers()
                self.wfile.write(diagram_body)
                delivered.set()
            elif path == "/events":
                # An already-open tab's EventSource, auto-reconnecting here
                # after the previous call's server exited. Just this call's
                # new items — the client tracks pushId so a reconnect within
                # the same call's lifetime never double-applies them.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(b"retry: 4000\n")
                    self.wfile.write(b"data: " + new_body + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                delivered.set()
            else:
                self.send_error(404)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/submit":
                length = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(length) if length else b""
                if not data:
                    self.send_error(400, "empty body")
                    return
                fd, out = tempfile.mkstemp(prefix="sketch-diagram-", suffix=".png")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                submit_result["path"] = out
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                submitted.set()
            elif path == "/sync":
                # The board reports its full current box list (id + label)
                # after every committed change, Claude-drawn or hand-drawn, so
                # a later push can look one up and connect to it by id — see
                # known_ids in main() and --list-boxes.
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw) if raw else {}
                except ValueError:
                    payload = {}
                boxes = payload.get("boxes") if isinstance(payload, dict) else None
                boxes = [b for b in boxes if isinstance(b, dict) and b.get("id")] if isinstance(boxes, list) else []
                with session_lock:
                    data = load_session(ignore_ttl=True) or {"items": [], "push_id": 0, "max_x": 0, "max_y": 0}
                    data["live_boxes"] = [{"id": str(b["id"]), "text": str(b.get("text", ""))} for b in boxes]
                    save_session(data)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_error(404)

    return Handler


def known_boxes(session):
    """{id: label} for every box drawn so far this session — from Claude's own
    pushes and from the board's live /sync reports, which also cover boxes
    the user drew or relabeled by hand."""
    if not session:
        return {}
    known = {}
    for it in session.get("items", []):
        if it.get("type") == "shape" and it.get("id"):
            known[it["id"]] = it.get("text", it["id"])
    for b in session.get("live_boxes", []):
        if b.get("id"):
            known[b["id"]] = b.get("text") or b["id"]
    return known


def list_boxes():
    """Standalone lookup (`--list-boxes`, no graph needed): print every box
    currently known for this session as `id<TAB>label`, one per line, so a
    new push's edges can reference an existing box — including one the user
    drew or renamed by hand — by its real id instead of guessing."""
    known = known_boxes(load_session())
    if not known:
        print("No diagram session yet, or nothing is on the canvas.")
        return
    for box_id, label in known.items():
        print(f"{box_id}\t{label}")


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
    if len(sys.argv) > 1 and sys.argv[1] == "--list-boxes":
        list_boxes()
        return
    if sys.platform != "darwin" or not shutil.which("open"):
        print("/sketch-diagram only works in Claude Code running locally on a Mac "
              "— it opens a browser on your machine, so it can't run in a remote, "
              "cloud, or non-macOS session.", file=sys.stderr)
        sys.exit(3)
    if not os.path.exists(INDEX):
        print(f"Cannot find index.html at {INDEX}", file=sys.stderr)
        sys.exit(2)

    graph = read_graph()

    session = load_session()
    prev_items = session["items"] if session else []
    known = known_boxes(session)
    known_ids = set(known)
    if known:
        listing = ", ".join(f'{k}="{v}"' for k, v in known.items())
        print(f"Boxes already on the canvas: {listing}", file=sys.stderr)
    max_x = session.get("max_x", 0) if session else 0
    max_y = session.get("max_y", 0) if session else 0
    push_id = (session.get("push_id", 0) if session else 0) + 1
    reuse_tab = session is not None

    horizontal = str(graph.get("direction", "TB")).upper() in ("LR", "RL")
    if not prev_items:
        offset = (0, 0)
    elif horizontal:
        offset = (max_x + PUSH_GAP, 0)
    else:
        offset = (0, max_y + PUSH_GAP)

    try:
        new_items = layout(graph, known_ids=known_ids, offset=offset)
    except Exception as e:
        print(f"Layout failed: {e}", file=sys.stderr)
        sys.exit(4)
    if not new_items:
        print("Graph had no new nodes or edges to draw.", file=sys.stderr)
        sys.exit(5)

    node_count = sum(1 for it in new_items if it.get("type") == "shape"
                     and it.get("kind") in ("rect", "ellipse"))
    boxes = [it for it in new_items if it.get("type") == "shape"
             and it.get("kind") in ("rect", "ellipse")]
    max_x = max([max_x] + [it["x"] + it["w"] for it in boxes])
    max_y = max([max_y] + [it["y"] + it["h"] for it in boxes])
    all_items = prev_items + new_items
    save_session({"items": all_items, "push_id": push_id, "max_x": max_x, "max_y": max_y})

    ensure_single_instance()
    caller = frontmost_app()
    try:
        httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", DIAGRAM_PORT), make_handler(all_items, new_items, push_id))
    except OSError as e:
        print(f"Could not bind port {DIAGRAM_PORT}: {e}", file=sys.stderr)
        sys.exit(6)
    with httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{DIAGRAM_PORT}/?diagram=1"
        print(f"Diagram ready ({node_count} new node(s)) at {url}", file=sys.stderr)
        if reuse_tab:
            print("Reusing the board tab that's already open.", file=sys.stderr)
        else:
            subprocess.run(["open", url], check=False)
        if not delivered.wait(SERVE_TIMEOUT_S) and reuse_tab:
            # Assumed-open tab never reconnected — it may have been closed, or
            # hit a hard error while the previous call's server was down (a
            # dead ERR_CONNECTION_REFUSED page has no JS running to retry).
            # Fall back to opening a fresh one rather than failing outright.
            print("That tab didn't respond — opening a new one instead.", file=sys.stderr)
            subprocess.run(["open", url], check=False)
            delivered.wait(SERVE_TIMEOUT_S)
        if not delivered.is_set():
            print("The board did not pick up the diagram in time.", file=sys.stderr)
            httpd.shutdown()
            sys.exit(1)
        print("Drawn. Waiting for the user to rearrange it and send it back "
              "(or the wait will time out)...", file=sys.stderr)
        # The user may now edit by hand and click "Send to Claude" (?diagram=1
        # gets the same button as ?bridge=1, see index.html bridgeMode). Wait
        # for that, but drawing already succeeded either way, so a timeout here
        # is not an error.
        submitted.wait(SUBMIT_TIMEOUT_S)
        httpd.shutdown()
    activate_app(caller)
    print(f"Drew {node_count} new node(s) onto the Sketch2AI canvas "
          f"({len(all_items)} items total in this session).")
    if submit_result["path"]:
        print(submit_result["path"])


if __name__ == "__main__":
    main()
