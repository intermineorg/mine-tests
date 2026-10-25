#!/usr/bin/env python3
"""Rehearse a change to a deployed site without touching it.

Relays every request to an upstream mine, except paths that exist under a
local directory, which are served from there instead.  Point a browser (or
`pytest --url-override <mine>=http://localhost:8099`) at the relay to test,
for example, a patched tool bundle against live production data:

    mkdir -p patched/tools/@intermine/bluegenes-msa-viewer/dist
    cp my-fixed-bundle.js patched/tools/@intermine/bluegenes-msa-viewer/dist/bundle.js
    python3 tools/relay/relay.py https://www.humanmine.org patched 8099

Query strings are ignored when matching local files (BlueGenes appends
?v=<version> to tool bundles).  Honours http(s)_proxy from the
environment; MINE_TESTS_IGNORE_TLS=1 disables upstream certificate checks.
Test harness only: no caching, no streaming, not for production.
"""
import http.server
import os
import ssl
import sys
import urllib.error
import urllib.request

UPSTREAM = sys.argv[1].rstrip("/")
LOCAL = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 8099

ctx = ssl.create_default_context()
if os.environ.get("MINE_TESTS_IGNORE_TLS") == "1":
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.ProxyHandler())
HOP = {"transfer-encoding", "connection", "content-encoding", "content-length", "keep-alive"}


class Relay(http.server.BaseHTTPRequestHandler):
    def _local(self):
        if not LOCAL:
            return None
        path = os.path.normpath(os.path.join(LOCAL, self.path.split("?")[0].lstrip("/")))
        return path if path.startswith(LOCAL) and os.path.isfile(path) else None

    def _send(self, status, headers, body):
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        local = self._local()
        if local and self.command == "GET":
            ctype = "text/javascript" if local.endswith(".js") else "text/css" if local.endswith(".css") else "application/octet-stream"
            with open(local, "rb") as fh:
                self._send(200, [("Content-Type", ctype), ("Cache-Control", "no-store")], fh.read())
            sys.stderr.write(f"LOCAL   {self.path}\n")
            return
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "accept-encoding", "content-length", "connection")}
        headers["Accept-Encoding"] = "identity"
        req = urllib.request.Request(UPSTREAM + self.path, data=data, headers=headers, method=self.command)
        try:
            with opener.open(req, timeout=120) as r:
                self._send(r.status, r.headers.items(), r.read())
        except urllib.error.HTTPError as e:
            self._send(e.code, e.headers.items(), e.read())
        except Exception as e:  # upstream unreachable
            self._send(502, [("Content-Type", "text/plain")], f"relay: {e}".encode())

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = _handle

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    sys.stderr.write(f"relaying http://0.0.0.0:{PORT} -> {UPSTREAM}" + (f", local files from {LOCAL}\n" if LOCAL else "\n"))
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Relay).serve_forever()
