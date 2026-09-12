"""Isolated browser fixture: save by UI, then read back persisted state."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys

store = Path(sys.argv[2])
mode = sys.argv[3] if len(sys.argv) > 3 else "good"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path in ("/client.js", "/client.js.map"):
            source = Path('bundle' if os.getenv('PROBE_BUNDLE') else '.') / self.path[1:]
            if not source.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.end_headers()
            self.wfile.write(source.read_bytes())
            return
        if self.path == "/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(store.read_bytes() if store.exists() else b'{"caption":""}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(('''<!doctype html><h1>Caption editor</h1>
<label>Caption <input id="caption"></label><button>Save</button><p role="status"></p>
<script src="/client.js"></script>''' if mode != "broken-ui" else "<h1>Wrong version</h1>").encode())

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if mode != "lost-write":
            store.write_text(json.dumps(data))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
