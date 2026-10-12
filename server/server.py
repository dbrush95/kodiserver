#!/usr/bin/env python3
"""Unprivileged Cockpit helper. Standard library only; never executes shell input."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import sys
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
import uuid
import zipfile
from urllib.parse import urlsplit, urlunsplit

def root():
    path = Path.home() / ".local/share/kodiserver"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Data directory cannot be a symbolic link")
    path.chmod(0o700)
    return path

def backup_root():
    path = root() / "backups"
    if path.is_symlink():
        raise ValueError("Backup directory cannot be a symbolic link")
    path.mkdir(exist_ok=True, mode=0o700)
    return path

def settings():
    path = root() / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {"plex_url": ""}

def normalize_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError("Enter an HTTP or HTTPS server address without spaces")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Use http:// or https:// followed by the Plex server address")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("Do not include credentials, tokens, queries, or fragments")
    if parsed.path not in ("", "/", "/web", "/web/"):
        raise ValueError("Use the server address, optionally ending in /web/")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid port")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

def save_settings(args):
    value = {"plex_url": normalize_url(args.get("plex_url", ""))}
    path = root() / "settings.json"
    temp = root() / (".settings-" + uuid.uuid4().hex)
    try:
        with temp.open("x", encoding="utf-8") as out:
            json.dump(value, out)
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return value

def status():
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            mem[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    disk = shutil.disk_usage(root())
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        uptime = 0
    total = mem.get("MemTotal", 0)
    return {"hostname": socket.gethostname(), "uptime": uptime,
            "memory_total": total, "memory_used": total - mem.get("MemAvailable", 0),
            "disk_free": disk.free, "disk_total": disk.total}

def inventory():
    items = []
    for folder in backup_root().iterdir():
        if folder.is_symlink() or not folder.is_dir() or folder.name.startswith("."):
            continue
        try:
            data = json.loads((folder / "metadata.json").read_text())
            data["path"] = str(folder / data["source_name"])
            items.append(data)
        except (OSError, ValueError, KeyError):
            continue
    return {"path": str(backup_root()), "items": sorted(items, key=lambda x: x["imported"], reverse=True),
            "attached": attached_backups()}

def safe_files(source):
    """Reject links/special files, including symlink directories, before copying."""
    if source.is_symlink():
        raise ValueError("Symbolic links are not supported in backups")
    if source.is_file():
        if not stat.S_ISREG(source.stat().st_mode):
            raise ValueError("Only regular files are supported")
        return [source]
    if not source.is_dir():
        raise ValueError("Choose a backup folder or ZIP file")
    files = []
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in dirs + names:
            p = Path(directory) / name
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError("Backup contains a symbolic link or special file: " + str(p))
        files.extend(Path(directory) / name for name in names)
        if len(files) > 200000:
            raise ValueError("Backup has too many files")
    return files

def import_backup(args):
    raw = Path(args.get("path", ""))
    if not raw.is_absolute():
        raise ValueError("Enter an absolute path on the Pi")
    # Resolve parents, but reject the source itself if it is a symlink.
    if raw.is_symlink():
        raise ValueError("Choose the actual backup, not a symbolic link")
    source = raw.resolve(strict=True)
    destination = backup_root().resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source must be outside the backup library")
    label = args.get("label", "").strip()
    if not label or len(label) > 100:
        raise ValueError("Enter a label between 1 and 100 characters")
    files = safe_files(source)
    if not files:
        raise ValueError("The backup is empty")
    archive = source.is_file()
    if archive:
        if source.suffix.lower() != ".zip":
            raise ValueError("Select a ZIP or dated Kodi backup folder")
        with zipfile.ZipFile(source) as z:
            names = [x.filename.replace("\\", "/") for x in z.infolist()]
            if not any(x.rstrip("/").endswith("xbmcbackup.val") for x in names):
                raise ValueError("ZIP does not contain a Kodi Backup validation file")
    elif not (source / "xbmcbackup.val").is_file():
        raise ValueError("Select the dated backup folder containing xbmcbackup.val")
    size = sum(p.stat().st_size for p in files)
    if shutil.disk_usage(destination).free < size + 100 * 1024 * 1024:
        raise ValueError("Not enough free storage to copy this backup")
    ident = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    stage = destination / ("." + ident)
    final = destination / ident
    stage.mkdir(mode=0o700)
    try:
        target = stage / source.name
        if archive:
            shutil.copy2(source, target)
        else:
            target.mkdir()
            for p in files:
                # Recheck to avoid following a changed link during an ordinary import.
                if p.is_symlink() or any(parent.is_symlink() for parent in p.parents if parent != source.parent):
                    raise ValueError("Backup changed during copy; retry from an unmodified source")
                output = target / p.relative_to(source)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, output)
        data = {"id": ident, "label": label, "source_name": source.name,
                "imported": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "size": size, "files": len(files), "archive": archive}
        (stage / "metadata.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        stage.rename(final)
        seed_merge_output(source)
        return data
    except BaseException:
        shutil.rmtree(stage)
        raise

def check_plex():
    address = settings().get("plex_url")
    if not address:
        raise ValueError("Save a Plex server address first")
    parsed = urlsplit(normalize_url(address))
    try:
        with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=4):
            return {"reachable": True}
    except OSError:
        return {"reachable": False}

def clients_root():
    path = root() / "clients"
    path.mkdir(exist_ok=True, mode=0o700)
    return path

def client_profile():
    path = clients_root() / "profile.json"
    return json.loads(path.read_text()) if path.exists() else {
        "version": 1, "revision": "", "kodi_major": 21, "skin_id": "skin.fentastic",
        "weather_id": "weather.multi", "weather_location": "", "temperature_unit": "F",
        "repo_interval_hours": 24, "skin_settings": {}, "weather_settings": {},
        "repositories": [], "required_addons": ["pvr.hts", "skin.fentastic", "weather.multi"],
        "tvh_host": ""}

def save_profile(args):
    old = client_profile()
    for key in ("skin_id", "weather_id"):
        value = args.get(key, old[key])
        prefix = "skin." if key == "skin_id" else "weather."
        if not re.fullmatch(re.escape(prefix) + r"[a-zA-Z0-9_.-]+", value):
            raise ValueError("Invalid " + key)
        if value != old[key]:
            old["skin_settings" if key == "skin_id" else "weather_settings"] = {}
        old[key] = value
    interval = int(args.get("repo_interval_hours", old["repo_interval_hours"]))
    if not 6 <= interval <= 168:
        raise ValueError("Repository checks must be 6–168 hours apart")
    major = int(args.get("kodi_major", old["kodi_major"]))
    if not 20 <= major <= 30:
        raise ValueError("Use the Kodi major version installed on your clients")
    unit = args.get("temperature_unit", old["temperature_unit"])
    if unit not in ("F", "C"):
        raise ValueError("Choose F or C")
    host = args.get("tvh_host", old.get("tvh_host", "")).strip()
    if host and not re.fullmatch(r"[a-zA-Z0-9.:-]+", host):
        raise ValueError("Use the Pi hostname or IP, without a URL or password")
    old.update(kodi_major=major, repo_interval_hours=interval, temperature_unit=unit,
               weather_location=str(args.get("weather_location", old["weather_location"]))[:120],
               tvh_host=host)
    old["required_addons"] = ["pvr.hts", old["skin_id"], old["weather_id"]]
    return write_profile(old)

def write_profile(profile):
    profile["revision"] = uuid.uuid4().hex
    path = clients_root() / "profile.json"
    temp = clients_root() / (".profile-" + uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return profile

def xml_settings(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Invalid or oversized settings file")
    data = path.read_text(encoding="utf-8")
    if "<!DOCTYPE" in data or "<!ENTITY" in data:
        raise ValueError("Unsupported XML declarations")
    result = {}
    for setting in ET.fromstring(data).iter("setting"):
        key = setting.get("id", "")
        value = setting.get("value", setting.text or "")
        # Share appearance/location preferences, never obvious account secrets.
        if re.search(r"password|token|secret|api.?key|username|login|account", key, re.I):
            continue
        if re.search(r"(?:https?|smb)://[^/ ]+@|(?:token|password|api_key)=", value, re.I):
            continue
        if key and len(key) < 200 and len(value) < 10000:
            result[key] = value
    return result

def capture_profile(args):
    ident = args.get("id", "")
    if not re.fullmatch(r"[0-9TZ]+-[a-f0-9]{8}", ident):
        raise ValueError("Select an imported backup")
    folder = backup_root() / ident
    meta = json.loads((folder / "metadata.json").read_text())
    if meta["archive"]:
        raise ValueError("Capture requires an uncompressed Kodi Backup folder")
    source = folder / meta["source_name"]
    profile = client_profile()
    data = source / "userdata/addon_data"
    # Backup versions may store selected userdata folders directly at backup root.
    if not data.exists():
        data = source / "addon_data"
    skin = xml_settings(data / profile["skin_id"] / "settings.xml")
    weather = xml_settings(data / profile["weather_id"] / "settings.xml")
    if not skin:
        raise ValueError("Selected skin settings were not found in this backup")
    profile["skin_settings"] = skin
    profile["weather_settings"] = weather
    profile["source_backup"] = ident
    # Copy repository add-ons as installable ZIPs; Kodi itself installs them on clients.
    addons = source / "addons"
    repos = []
    for candidate in sorted(addons.glob("repository.*")):
        if not re.fullmatch(r"repository\.[a-zA-Z0-9_.-]+", candidate.name):
            continue
        paths = safe_files(candidate)
        manifest = candidate / "addon.xml"
        if not manifest.is_file():
            continue
        xml = ET.fromstring(manifest.read_text())
        if xml.get("id") != candidate.name or not any(e.get("point") == "xbmc.addon.repository" for e in xml.findall("extension")):
            continue
        outdir = clients_root() / "repositories"
        outdir.mkdir(exist_ok=True)
        zip_path = outdir / (candidate.name + ".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as out:
            for p in paths:
                out.write(p, str(p.relative_to(candidate.parent)))
        repos.append({"id": candidate.name, "name": xml.get("name", candidate.name),
                      "zip": "repositories/" + zip_path.name})
    profile["repositories"] = repos
    return write_profile(profile)

def services():
    result = {}
    for name in ("tvheadend", "smbd", "mariadb"):
        try:
            p = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
            result[name] = p.stdout.strip() or "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            result[name] = "unavailable"
    inbox = root() / "inbox"
    inbox.mkdir(exist_ok=True, mode=0o700)
    result.update(inbox=str(inbox), clients=str(clients_root()))
    return result

PLAYLIST_PORT = 8877

def analyze_playlist(args):
    text = read_playlist(args.get("path", ""))
    return classify_playlist(text)[0]

def read_playlist(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Choose an absolute path to a regular M3U file on the Pi")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Playlist exceeds 20 MB")
    text = path.read_text(encoding="utf-8-sig")
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("This is not an extended M3U playlist")
    return text

def classify_playlist(text):
    """Split an IPTV Merge list into Apple TV direct streams and the full Kodi list."""
    apple = ["#EXTM3U"]
    blocks = []
    current = None
    result = {"total": 0, "direct_candidates": 0, "kodi_dependent": 0, "other": 0}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            current = [line]
            continue
        if current is None:
            continue
        current.append(line)
        if line.startswith("#"):
            continue
        block = current
        blocks.append(block)
        current = None
        result["total"] += 1
        kodiprop = any(item.upper().startswith("#KODIPROP:") for item in block)
        parsed = urlsplit(line.split("|", 1)[0])
        direct = (not kodiprop and parsed.scheme in ("http", "https") and "|" not in line
                  and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
        if direct:
            result["direct_candidates"] += 1
            apple.extend(item for item in block if not item.upper().startswith("#KODIPROP:"))
        elif kodiprop or parsed.scheme == "plugin" or parsed.hostname in ("localhost", "127.0.0.1", "::1") or "|" in line:
            result["kodi_dependent"] += 1
        else:
            result["other"] += 1
    return result, "\n".join(apple) + "\n", blocks

def lan_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.1.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()

def playlist_base():
    return "http://" + lan_ip() + ":" + str(PLAYLIST_PORT)

def public_root():
    path = root() / "public"
    path.mkdir(exist_ok=True, mode=0o700)
    return path

def iptv_inbox():
    path = root() / "inbox" / "iptv"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path

def seed_merge_output(source):
    """Keep the Shield's IPTV Merge output where the house publisher reads it."""
    playlist = source / "addon_data/plugin.program.iptv.merge/playlist.m3u8"
    if not playlist.is_file() or playlist.is_symlink() or playlist.stat().st_size > 20 * 1024 * 1024:
        return
    atomic_write(iptv_inbox() / "playlist.m3u8", playlist.read_bytes())
    guide = playlist.with_name("epg.xml")
    if guide.is_file() and not guide.is_symlink() and guide.stat().st_size <= 250 * 1024 * 1024:
        atomic_write(iptv_inbox() / "epg.xml", guide.read_bytes())

SKIP_SCAN = {"android", "lost.dir", "system volume information", ".trashes", "$recycle.bin", "nvidia_shield"}

def iter_backup_folders():
    inbox = root() / "inbox"
    if inbox.is_dir() and not inbox.is_symlink():
        for child in inbox.iterdir():
            marker = child / "xbmcbackup.val"
            if child.name != "iptv" and child.is_dir() and not child.is_symlink() and marker.is_file() and not marker.is_symlink():
                yield child
    for base in (Path("/mnt/shield-usb"), Path("/media"), Path("/run/media")):
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            path = Path(dirpath)
            if path.is_symlink():
                dirnames.clear()
                continue
            dirnames[:] = [name for name in dirnames if name.lower() not in SKIP_SCAN and not name.startswith(".")]
            if len(path.relative_to(base).parts) > 4:
                dirnames.clear()
                continue
            if "xbmcbackup.val" in filenames:
                yield path
                dirnames.clear()

def attached_backups():
    ledger_path = root() / "backup-ledger.json"
    seen = set(json.loads(ledger_path.read_text())) if ledger_path.is_file() else set()
    items = []
    for folder in iter_backup_folders():
        if folder.is_relative_to(root()):
            continue
        try:
            key = str(folder.resolve())
        except OSError:
            continue
        if key in seen or folder.name in seen:
            continue
        items.append({"path": str(folder), "name": folder.name})
    return items

def atomic_write(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)

def publish_playlist(args):
    source = args.get("path") or str(iptv_inbox() / "playlist.m3u8")
    text = read_playlist(source)
    counts, apple, _blocks = classify_playlist(text)
    epg_value = args.get("epg") or str(Path(source).with_name("epg.xml"))
    epg_path = Path(epg_value)
    has_epg = epg_path.is_absolute() and epg_path.is_file() and not epg_path.is_symlink()
    if has_epg and epg_path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Guide file exceeds 100 MB")
    if has_epg and 'url-tvg="' not in apple.split("\n", 1)[0]:
        apple = '#EXTM3U url-tvg="' + playlist_base() + '/epg.xml"\n' + apple.split("\n", 1)[1]
    folder = public_root()
    atomic_write(folder / "playlist.m3u8", apple.encode())
    atomic_write(folder / "kodi.m3u8", text.encode())
    if has_epg:
        atomic_write(folder / "epg.xml", epg_path.read_bytes())
    meta = dict(counts)
    meta.update(epg=has_epg, published=datetime.datetime.now(datetime.timezone.utc).isoformat())
    atomic_write(folder / "catalog.json", json.dumps(meta).encode())
    # Counts and local URLs only. Stream addresses stay in the hosted files.
    return catalog()

def publish_house(args):
    import gather
    merge_text = ""
    merge_epg = b""
    path = Path(args.get("path") or (iptv_inbox() / "playlist.m3u8"))
    if path.is_absolute() and path.is_file() and not path.is_symlink():
        try:
            merge_text = read_playlist(str(path))
        except ValueError:
            merge_text = ""
        epg_path = Path(args.get("epg") or path.with_name("epg.xml"))
        if (epg_path.is_absolute() and epg_path.is_file() and not epg_path.is_symlink()
                and epg_path.stat().st_size <= 250 * 1024 * 1024):
            merge_epg = epg_path
    payload = gather.build_house(merge_text, merge_epg, playlist_base())
    folder = public_root()
    atomic_write(folder / "playlist.m3u8", payload["playlist"].encode())
    atomic_write(folder / "tvh.m3u8", payload["tvh"].encode())
    atomic_write(folder / "kodi.m3u8", payload["kodi"].encode())
    atomic_write(folder / "epg.xml", payload["epg"])
    meta = {
        "total": payload["total"], "direct_candidates": payload["direct_candidates"],
        "kodi_dependent": payload["kodi_dependent"], "other": 0, "lean": payload["lean"],
        "epg": payload["guide_channels"] > 0,
        "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": payload["sources"],
    }
    atomic_write(folder / "catalog.json", json.dumps(meta).encode())
    return catalog()

WEATHER_TEXT = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Rain", 63: "Rain", 65: "Rain",
    71: "Snow", 73: "Snow", 75: "Snow", 80: "Showers", 81: "Showers", 82: "Showers",
    95: "Thunderstorm",
}

def refresh_weather():
    """Dayton conditions for every client. WHIO is the Dayton station."""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=39.7589&longitude=-84.1916"
           "&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min"
           "&temperature_unit=fahrenheit&timezone=America%2FNew_York&forecast_days=1")
    request = urllib.request.Request(url, headers={"User-Agent": "kodiserver"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode())
    current = data.get("current") or {}
    code = int(current.get("weather_code", -1))
    summary = {"location": "Dayton, OH", "temperature": current.get("temperature_2m"), "unit": "F",
               "condition": WEATHER_TEXT.get(code, "Weather"),
               "updated": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    atomic_write(public_root() / "weather.json", json.dumps(summary).encode())
    if not client_profile().get("weather_location"):
        save_profile({"weather_location": "Dayton, OH", "tvh_host": client_profile().get("tvh_host") or "192.168.1.119"})
    return summary

def weather_summary():
    path = public_root() / "weather.json"
    if not path.is_file() or path.is_symlink():
        return {"location": "Dayton, OH", "temperature": None, "unit": "F", "condition": "", "updated": ""}
    return json.loads(path.read_text())

_backup_quiet = {}

def import_ready_backups():
    """Copy a finished Kodi Backup from the inbox share or a mounted USB stick."""
    ledger_path = root() / "backup-ledger.json"
    seen = set(json.loads(ledger_path.read_text())) if ledger_path.is_file() else set()
    imported = []
    for child in iter_backup_folders():
        marker = child / "xbmcbackup.val"
        if not marker.is_file() or marker.is_symlink():
            continue
        try:
            key = str(child.resolve())
        except OSError:
            continue
        if key in seen or child.name in seen:
            continue
        newest = marker.stat().st_mtime_ns
        for directory, _dirs, names in os.walk(child, followlinks=False):
            for name in names:
                candidate = Path(directory) / name
                if candidate.is_symlink():
                    continue
                newest = max(newest, candidate.stat().st_mtime_ns)
        token = (newest, marker.stat().st_size)
        previous = _backup_quiet.get(key)
        if previous is None or previous[0] != token:
            _backup_quiet[key] = (token, time.time())
            continue
        if key in seen or child.name in seen or time.time() - previous[1] < 45:
            continue
        label = ("Shield " + child.name if "/mnt/" in key or "/media/" in key else "Kodi " + child.name)[:100]
        meta = import_backup({"path": str(child), "label": label})
        seen.add(key)
        imported.append(meta["id"])
    if imported:
        atomic_write(ledger_path, json.dumps(sorted(seen)).encode())
    return {"imported": imported}

def hosted_files():
    base = playlist_base()
    files = []
    companion = clients_root() / "service.kodiserver.client-0.2.0.zip"
    if companion.is_file() and not companion.is_symlink():
        files.append({"name": companion.name, "url": base + "/client/" + companion.name})
    repo_dir = clients_root() / "repositories"
    if repo_dir.is_dir():
        for path in sorted(repo_dir.glob("repository.*.zip")):
            if path.is_file() and not path.is_symlink() and re.fullmatch(r"repository\.[A-Za-z0-9_.-]+\.zip", path.name):
                files.append({"name": path.name, "url": base + "/repos/" + path.name})
    return files

def catalog():
    meta_path = public_root() / "catalog.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {
        "total": 0, "direct_candidates": 0, "kodi_dependent": 0, "other": 0, "epg": False, "published": ""}
    base = playlist_base()
    meta.update(apple_playlist=base + "/playlist.m3u8", kodi_playlist=base + "/kodi.m3u8",
                tvh_playlist=base + "/tvh.m3u8", epg_url=base + "/epg.xml",
                weather_url=base + "/weather.json", inbox=str(iptv_inbox()), files=hosted_files(),
                host=service_state("kodiserver-playlist"), weather=weather_summary())
    return meta

def service_state(name):
    try:
        probe = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return probe.stdout.strip() or "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"

FILE_LIST_LIMIT = 2000
FILE_PREVIEW_LIMIT = 64 * 1024
FILE_UPLOAD_LIMIT = 8 * 1024 * 1024
HIDDEN_NAMES = {"library.xml", "id_rsa", "id_ed25519", "id_ecdsa", "authorized_keys"}

def file_places():
    """Folders the portal may browse. Other system paths stay out."""
    places = [
        {"id": "server", "label": "Server files", "path": str(root())},
        {"id": "home", "label": "Home", "path": str(Path.home())},
    ]
    for label, candidate in (
        ("Shield USB", Path("/mnt/shield-usb")),
        ("Media", Path("/media")),
        ("Removable", Path("/run/media")),
        ("Recordings", Path("/var/lib/tvheadend/recordings")),
    ):
        if candidate.is_dir() and not candidate.is_symlink():
            places.append({"id": label.lower().replace(" ", "-"), "label": label, "path": str(candidate)})
    return places

def file_roots():
    return [Path(place["path"]).resolve() for place in file_places()]

def clean_name(name):
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("Use a file name without slashes")
    if len(name) > 240:
        raise ValueError("That name is too long")
    return name

def resolve_inside(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise ValueError("Choose a folder on this Pi")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("Choose a folder on this Pi")
    if path.is_symlink():
        raise ValueError("Symbolic links are not opened")
    resolved = path.resolve()
    if not any(resolved == base or base in resolved.parents for base in file_roots()):
        raise ValueError("That folder is outside the file manager")
    return resolved

def is_protected(path):
    home = Path.home().resolve()
    data = root().resolve()
    ssh = home / ".ssh"
    if path in (home, data, data / "backups", data / "public", data / "inbox", data / "clients", data / "cache"):
        return True
    return path == ssh or ssh in path.parents

def hidden_file(path):
    return path.name.lower() in HIDDEN_NAMES or path.suffix.lower() in {".pem", ".key"} or is_protected(path)

def list_files(args):
    places = file_places()
    folder = resolve_inside(args.get("path") or places[0]["path"])
    if not folder.is_dir():
        raise ValueError("Choose a folder")
    entries = []
    truncated = False
    for child in folder.iterdir():
        if len(entries) >= FILE_LIST_LIMIT:
            truncated = True
            break
        if child.is_symlink():
            kind = "link"
        elif child.is_dir():
            kind = "dir"
        elif child.is_file():
            kind = "file"
        else:
            kind = "other"
        try:
            stat_result = child.lstat()
            modified = datetime.datetime.fromtimestamp(stat_result.st_mtime, datetime.timezone.utc).isoformat()
            size = stat_result.st_size if kind == "file" else None
        except OSError:
            modified = ""
            size = None
        entries.append({"name": child.name, "kind": kind, "size": size, "modified": modified})
    entries.sort(key=lambda item: (item["kind"] != "dir", item["name"].lower()))
    parent = None
    if folder.parent != folder:
        try:
            parent = str(resolve_inside(str(folder.parent)))
        except ValueError:
            parent = None
    return {"path": str(folder), "parent": parent, "places": places, "entries": entries, "truncated": truncated}

def open_child(args):
    folder = resolve_inside(args.get("path", ""))
    if not folder.is_dir():
        raise ValueError("Choose a folder")
    child = folder / clean_name(args.get("name", ""))
    if child.is_symlink():
        raise ValueError("Symbolic links are not opened")
    if not any(child.resolve() == base or base in child.resolve().parents for base in file_roots()):
        raise ValueError("That file is outside the file manager")
    return folder, child

def read_file_preview(args):
    _folder, child = open_child(args)
    if not child.is_file():
        raise ValueError("Choose a file")
    if hidden_file(child):
        raise ValueError("This file stays hidden")
    if child.stat().st_size > FILE_PREVIEW_LIMIT:
        raise ValueError("This file is too large to preview")
    data = child.read_bytes()
    if b"\x00" in data:
        raise ValueError("This file is not text")
    return {"name": child.name, "text": data.decode("utf-8", "replace")}

def make_directory(args):
    folder = resolve_inside(args.get("path", ""))
    child = folder / clean_name(args.get("name", ""))
    if child.exists() or child.is_symlink():
        raise ValueError("That name already exists")
    child.mkdir()
    return list_files({"path": str(folder)})

def rename_path(args):
    folder, child = open_child(args)
    if not child.exists():
        raise ValueError("That file is not there")
    if is_protected(child):
        raise ValueError("That folder is part of the server and stays put")
    target = folder / clean_name(args.get("to", ""))
    if target.exists() or target.is_symlink():
        raise ValueError("That name already exists")
    child.rename(target)
    return list_files({"path": str(folder)})

def contains_link(folder):
    for directory, dirnames, names in os.walk(folder, followlinks=False):
        for name in dirnames + names:
            if (Path(directory) / name).is_symlink():
                return True
    return False

def delete_path(args):
    folder, child = open_child(args)
    if not child.exists():
        raise ValueError("That file is not there")
    if is_protected(child):
        raise ValueError("That folder is part of the server and stays put")
    if child.is_dir():
        if contains_link(child):
            raise ValueError("This folder contains a symbolic link, so it was left in place")
        shutil.rmtree(child)
    elif child.is_file():
        child.unlink()
    else:
        raise ValueError("Choose a file or folder")
    return list_files({"path": str(folder)})

def write_uploaded_file(args, blob):
    if len(blob) > FILE_UPLOAD_LIMIT:
        raise ValueError("Files larger than 8 MB belong on a network share")
    folder = resolve_inside(args.get("path", ""))
    name = clean_name(args.get("name", ""))
    if name.lower() in HIDDEN_NAMES or Path(name).suffix.lower() in {".pem", ".key"}:
        raise ValueError("That file name is reserved")
    child = folder / name
    if child.exists() or child.is_symlink():
        raise ValueError("That name already exists")
    temporary = folder / (".upload-" + uuid.uuid4().hex)
    try:
        temporary.write_bytes(blob)
        temporary.replace(child)
    finally:
        if temporary.exists():
            temporary.unlink()
    return list_files({"path": str(folder)})

def main():
    os.umask(0o077)
    try:
        action = sys.argv[1]
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        if action == "file-write":
            blob = sys.stdin.buffer.read(FILE_UPLOAD_LIMIT + 1)
            print(json.dumps(write_uploaded_file(args, blob)))
            return 0
        handlers = {"status": lambda: status(), "backups": lambda: inventory(),
                    "files": lambda: list_files(args), "file-read": lambda: read_file_preview(args),
                    "file-mkdir": lambda: make_directory(args), "file-rename": lambda: rename_path(args),
                    "file-delete": lambda: delete_path(args),
                    "settings": lambda: settings(), "save-settings": lambda: save_settings(args),
                    "import": lambda: import_backup(args), "check-plex": lambda: check_plex(),
                    "services": lambda: services(), "profile": lambda: client_profile(),
                    "save-profile": lambda: save_profile(args), "capture-profile": lambda: capture_profile(args),
                    "analyze-playlist": lambda: analyze_playlist(args), "publish-playlist": lambda: publish_playlist(args),
                    "publish-house": lambda: publish_house(args), "catalog": lambda: catalog()}
        if action not in handlers:
            raise ValueError("Unknown action")
        print(json.dumps(handlers[action]()))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        # Operational errors are structured responses for the portal.
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
