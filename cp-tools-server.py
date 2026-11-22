#!/usr/bin/env python3
"""Servidor local do CP Tools.

Põe este ficheiro e o cp-tools.html na mesma pasta (por exemplo, dentro da pasta do Syncthing) e corre:
    python3 cp-tools-server.py
Os dados ficam em cp-tools-data.json, nessa pasta.
"""
import http.server, os, sys, threading, webbrowser

DIR = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(DIR, "cp-tools.html")
DATA = os.path.join(DIR, "cp-tools-data.json")
PORT = next((int(a) for a in sys.argv[1:] if a.isdigit()), 8765)


class Handler(http.server.BaseHTTPRequestHandler):
    def local(self):
        # only answer requests addressed to this machine by name
        return self.headers.get("Host", "").split(":")[0] in ("localhost", "127.0.0.1")

    def send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("X-CP-Tools", "1")
        self.send_header("Cache-Control", "no-store")
        if code != 204:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if code != 204:
            self.wfile.write(body)

    def do_GET(self):
        if not self.local():
            return self.send(403, b"{}")
        path = self.path.split("?")[0]
        if path in ("/", "/cp-tools.html"):
            with open(PAGE, "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/data":
            if not os.path.exists(DATA):
                return self.send(204)
            with open(DATA, "rb") as f:
                return self.send(200, f.read())
        self.send(404, b"{}")

    def do_PUT(self):
        if not self.local() or self.path != "/data" or self.headers.get("X-CP-Tools") != "1":
            return self.send(403, b"{}")
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        tmp = DATA + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA)  # the file is never seen half-written
        self.send(200, b"{}")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://localhost:%d/" % PORT
    print("CP Tools em " + url + "  (Ctrl+C para parar)")
    print("Dados: " + DATA)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
