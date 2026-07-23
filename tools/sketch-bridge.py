#!/usr/bin/env python3
"""Sketch2AI <-> Claude Code bridge.

Serves the local whiteboard, opens it in the browser with the "Send to Claude"
button enabled, waits for one drawing to be submitted, writes it to a PNG file,
prints that path to stdout, and exits. Driven by the /sketch slash command.

Everything is same-origin: the page is served from http://127.0.0.1:<port>/ and
its Send button POSTs the PNG back to /submit on the same server. No external
requests, no dependencies beyond the Python standard library.
"""
import http.server
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
TIMEOUT_S = 570  # stay under the /sketch command's 10-minute Bash ceiling
PIDFILE = os.path.join(tempfile.gettempdir(), "sketch-bridge.pid")

done = threading.Event()
result = {"path": None}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep stdout clean — only the PNG path is printed there

    def do_GET(self):
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    body = f.read()
            except OSError as e:
                self.send_error(500, str(e))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/submit":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length) if length else b""
        if not data:
            self.send_error(400, "empty body")
            return
        fd, out = tempfile.mkstemp(prefix="sketch-", suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        result["path"] = out
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
        done.set()


def frontmost_app():
    """Name of the app in the foreground right now (the terminal running /sketch)."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first '
             'process whose frontmost is true'],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def activate_app(name):
    """Bring the given app back to the foreground so the user lands in the terminal."""
    if not name:
        return
    subprocess.run(
        ["osascript", "-e",
         f'tell application "System Events" to set frontmost of process "{name}" to true'],
        check=False, capture_output=True,
    )


def _looks_like_bridge(pid):
    """True if pid is a live process whose command line is this bridge script."""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5)
        return "sketch-bridge.py" in out.stdout
    except Exception:
        return False


def ensure_single_instance():
    """Kill any previous bridge still waiting, then record our own pid.

    Keeps at most one bridge alive so repeated /sketch calls never pile up. The
    ps check guards against killing an unrelated process that reused the pid.
    """
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


def main():
    # This bridge opens a browser on the local machine for a human to draw in, so
    # it only makes sense in a local Mac session. Fail fast (rather than hang for
    # ~9.5 minutes) when there is no local browser to open — remote/cloud sessions,
    # headless boxes, or non-macOS.
    if sys.platform != "darwin" or not shutil.which("open"):
        print("/sketch only works in Claude Code running locally on a Mac — it "
              "opens a browser on your machine to draw in, so it can't run in a "
              "remote, cloud, or non-macOS session.", file=sys.stderr)
        sys.exit(3)
    if not os.path.exists(INDEX):
        print(f"Cannot find index.html at {INDEX}", file=sys.stderr)
        sys.exit(2)
    ensure_single_instance()  # never leave more than one bridge waiting
    caller = frontmost_app()  # capture the terminal before the browser steals focus
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{port}/?bridge=1"
        print(f"Whiteboard is open at {url}", file=sys.stderr)
        print("Draw, then click \"Send to Claude\" in the browser.", file=sys.stderr)
        subprocess.run(["open", url], check=False)
        if not done.wait(TIMEOUT_S):
            print("Timed out waiting for a drawing.", file=sys.stderr)
            httpd.shutdown()
            sys.exit(1)
        httpd.shutdown()
    activate_app(caller)  # jump focus back to the terminal — no manual click
    print(result["path"])  # stdout: the PNG path for Claude to Read


if __name__ == "__main__":
    main()
