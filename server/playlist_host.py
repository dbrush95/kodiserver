#!/usr/bin/env python3
"""Serve the published playlist, guide, companion, and repository packages on the LAN."""
import ipaddress
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server

TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".json": "application/json",
}

def lan_client(host):
    if host in ("127.0.0.1", "::1"):
        return True
    try:
        client = ipaddress.ip_address(host)
        local = ipaddress.ip_address(server.lan_ip())
    except ValueError:
        return False
    if client.version != 4 or local.version != 4:
        return False
    return client in ipaddress.ip_network(str(local) + "/24", strict=False)

def safe_file(path):
    if path.is_symlink() or not path.is_file():
        return None
    resolved = path.resolve()
    root = server.root().resolve()
    if root not in resolved.parents:
        return None
    return resolved

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if not lan_client(self.client_address[0]):
            body = b"Not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = urlsplit(self.path).path
        target = None
        if path in ("/playlist.m3u8", "/kodi.m3u8", "/tvh.m3u8", "/epg.xml", "/weather.json"):
            target = safe_file(server.public_root() / path[1:])
        elif path == "/client/service.kodiserver.client-0.2.0.zip":
            target = safe_file(server.clients_root() / "service.kodiserver.client-0.2.0.zip")
        else:
            match = re.fullmatch(r"/repos/(repository\.[A-Za-z0-9_.-]+\.zip)", path)
            if match:
                target = safe_file(server.clients_root() / "repositories" / match.group(1))
        if target is None:
            body = b"Not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

def watch_inbox():
    last = None
    next_house = 0
    next_weather = 0
    next_backup = 0
    while True:
        now = time.time()
        if now >= next_weather:
            try:
                server.refresh_weather()
                next_weather = time.time() + 3600
            except Exception:
                next_weather = time.time() + 600
        if now >= next_house:
            try:
                server.publish_house({})
                next_house = time.time() + 3 * 3600
            except Exception:
                next_house = time.time() + 900
        if now >= next_backup:
            try:
                server.import_ready_backups()
            except Exception:
                pass
            next_backup = time.time() + 60
        path = server.iptv_inbox() / "playlist.m3u8"
        try:
            if path.is_file() and not path.is_symlink():
                first = (path.stat().st_mtime_ns, path.stat().st_size)
                time.sleep(2)
                if path.is_file() and first == (path.stat().st_mtime_ns, path.stat().st_size) and first != last:
                    server.publish_house({"path": str(path)})
                    last = first
                    next_house = time.time() + 3 * 3600
        except Exception:
            # Keep the previous published list. The portal reports the last good publish.
            pass
        time.sleep(8)

def main():
    threading.Thread(target=watch_inbox, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", server.PLAYLIST_PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
