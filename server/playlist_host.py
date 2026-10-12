#!/usr/bin/env python3
"""Serve the published playlist, guide, companion, and repository packages on the LAN."""
import ipaddress
import json
import re
import sys
import threading
import time
import urllib.request
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

RESIDENT = {}
LOCK = threading.Lock()
MEMORY_ROUTES = {
    "/playlist.m3u8": ("playlist", "application/vnd.apple.mpegurl"),
    "/kodi.m3u8": ("kodi", "application/vnd.apple.mpegurl"),
    "/kodi.xml": ("kodi_epg", "application/xml"),
    "/tvh.m3u8": ("tvh", "application/vnd.apple.mpegurl"),
    "/epg.xml": ("epg", "application/xml"),
}


def read_public(name):
    path = safe_file(server.public_root() / name)
    return path.read_bytes() if path else b""


def load_resident():
    """Keep the published guide in this process so clients are not waiting on disk."""
    fresh = {
        "playlist": read_public("playlist.m3u8"),
        "kodi": read_public("kodi.m3u8"),
        "kodi_epg": read_public("kodi.xml"),
        "tvh": read_public("tvh.m3u8"),
        "epg": read_public("epg.xml"),
        "lineup": read_public("lineup.json"),
        "tuners": {},
        "pluto": {},
    }
    pluto_dir = server.public_root() / "pluto"
    if pluto_dir.is_dir() and not pluto_dir.is_symlink():
        for path in pluto_dir.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix == ".m3u8" and re.fullmatch(r"[0-9a-f]{16,32}", path.stem):
                fresh["pluto"][path.stem] = path.read_bytes()
    tuner_dir = server.public_root() / "tuners"
    if tuner_dir.is_dir() and not tuner_dir.is_symlink():
        for path in tuner_dir.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix in {".m3u8", ".xml"} and path.stem.isdigit():
                fresh["tuners"][(path.stem, path.suffix)] = path.read_bytes()
    with LOCK:
        RESIDENT.clear()
        RESIDENT.update(fresh)


def lineup_bytes():
    with LOCK:
        raw = RESIDENT.get("lineup") or b""
        guide_bytes = len(RESIDENT.get("epg") or b"")
    if not raw:
        raw = read_public("lineup.json")
    try:
        data = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict):
        data["guide_bytes"] = guide_bytes
        data["guide_resident"] = guide_bytes > 0
        raw = json.dumps(data).encode()
    return raw


def notify_tuners(count):
    """Ask each tuner to pull the guide it will hand to Plex. Failures stay local."""
    for index in range(1, count + 1):
        url = "http://%s:%s/api/" % (server.lan_ip(), 34399 + index)
        for cmd in ("updateM3U", "updateXMLTV", "updateXEPG"):
            try:
                request = urllib.request.Request(
                    url, data=json.dumps({"cmd": cmd}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    response.read(4096)
            except Exception:
                return


def remember(payload):
    load_resident()
    threading.Thread(target=notify_tuners, args=(len(payload.get("tuners") or []),), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_body(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def refuse(self):
        self.send_body(b"Not found\n", "text/plain", 404)

    def memory_body(self, key):
        with LOCK:
            body = RESIDENT.get(key) or b""
        return body

    def do_GET(self):
        if not lan_client(self.client_address[0]):
            self.refuse()
            return
        path = urlsplit(self.path).path
        if path == "/lineup.json":
            self.send_body(lineup_bytes(), "application/json")
            return
        route = MEMORY_ROUTES.get(path)
        if route:
            body = self.memory_body(route[0]) or read_public(path[1:])
            if body:
                self.send_body(body, route[1])
                return
            self.refuse()
            return
        pluto = re.fullmatch(r"/pluto/([0-9a-f]{16,32})\.m3u8", path)
        if pluto:
            with LOCK:
                body = RESIDENT.get("pluto", {}).get(pluto.group(1), b"")
            if not body:
                body = read_public("pluto/%s.m3u8" % pluto.group(1))
            if body:
                self.send_body(body, "application/vnd.apple.mpegurl")
                return
            self.refuse()
            return
        tuner = re.fullmatch(r"/tuners/([1-9]\d*)\.(m3u8|xml)", path)
        if tuner:
            suffix = ".m3u8" if tuner.group(2) == "m3u8" else ".xml"
            with LOCK:
                body = RESIDENT.get("tuners", {}).get((tuner.group(1), suffix), b"")
            if not body:
                body = read_public("tuners/%s%s" % (tuner.group(1), suffix))
            if body:
                kind = "application/vnd.apple.mpegurl" if suffix == ".m3u8" else "application/xml"
                self.send_body(body, kind)
                return
            self.refuse()
            return
        target = None
        if path == "/weather.json":
            target = safe_file(server.public_root() / "weather.json")
        elif path == "/client/service.kodiserver.client-0.2.0.zip":
            target = safe_file(server.clients_root() / "service.kodiserver.client-0.2.0.zip")
        else:
            match = re.fullmatch(r"/repos/(repository\.[A-Za-z0-9_.-]+\.zip)", path)
            if match:
                target = safe_file(server.clients_root() / "repositories" / match.group(1))
        if target is None:
            self.refuse()
            return
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_POST(self):
        if not lan_client(self.client_address[0]) or urlsplit(self.path).path != "/lineup/decide":
            self.refuse()
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4096:
            self.send_body(b'{"error":"Bad request"}\n', "application/json", 400)
            return
        try:
            posted = json.loads(self.rfile.read(length).decode())
            import gather
            gather.save_decision(str(posted.get("key") or ""), bool(posted.get("active")))
            payload = gather.rebuild_saved(server.playlist_base())
            gather.store_house(payload)
            remember(payload)
            body = json.dumps(payload["lineup"]).encode()
        except Exception as error:
            message = str(error).split("?", 1)[0].replace("\n", " ")[:180]
            body = json.dumps({"error": message or "The lineup could not be saved"}).encode()
            self.send_body(body, "application/json", 400)
            return
        self.send_body(body, "application/json")

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
                load_resident()
                error_path = server.root() / "publish-error.txt"
                if error_path.is_file():
                    error_path.unlink()
                next_house = time.time() + 3 * 3600
            except Exception as error:
                message = str(error).split("?", 1)[0].replace("\n", " ")[:180]
                try:
                    (server.root() / "publish-error.txt").write_text(message)
                except OSError:
                    pass
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
                    load_resident()
                    last = first
                    next_house = time.time() + 3 * 3600
        except Exception:
            # Keep the previous published list. The portal reports the last good publish.
            pass
        time.sleep(8)

def main():
    # The Pi can serve files built on the big machine. It must not rebuild them.
    import os
    load_resident()
    if os.environ.get("KODISERVER_SERVE_ONLY") != "1":
        threading.Thread(target=watch_inbox, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", server.PLAYLIST_PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
