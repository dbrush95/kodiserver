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
    return {"path": str(backup_root()), "items": sorted(items, key=lambda x: x["imported"], reverse=True)}

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

def analyze_playlist(args):
    path = Path(args.get("path", ""))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Choose an absolute path to a regular M3U file on the Pi")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Playlist exceeds 20 MB")
    text = path.read_text(encoding="utf-8-sig")
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("This is not an extended M3U playlist")
    result = {"total": 0, "direct_candidates": 0, "kodi_dependent": 0, "other": 0}
    dependent = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.upper().startswith("#KODIPROP:"):
                dependent = True
            continue
        result["total"] += 1
        parsed = urlsplit(line.split("|", 1)[0])
        if dependent or parsed.scheme == "plugin" or parsed.hostname in ("localhost", "127.0.0.1", "::1") or "|" in line:
            result["kodi_dependent"] += 1
        elif parsed.scheme in ("http", "https"):
            result["direct_candidates"] += 1
        else:
            result["other"] += 1
        dependent = False
    return result

def main():
    os.umask(0o077)
    try:
        action = sys.argv[1]
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        handlers = {"status": lambda: status(), "backups": lambda: inventory(),
                    "settings": lambda: settings(), "save-settings": lambda: save_settings(args),
                    "import": lambda: import_backup(args), "check-plex": lambda: check_plex(),
                    "services": lambda: services(), "profile": lambda: client_profile(),
                    "save-profile": lambda: save_profile(args), "capture-profile": lambda: capture_profile(args),
                    "analyze-playlist": lambda: analyze_playlist(args)}
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
