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

def main():
    os.umask(0o077)
    try:
        action = sys.argv[1]
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        handlers = {"status": lambda: status(), "backups": lambda: inventory(),
                    "settings": lambda: settings(), "save-settings": lambda: save_settings(args),
                    "import": lambda: import_backup(args), "check-plex": lambda: check_plex()}
        if action not in handlers:
            raise ValueError("Unknown action")
        print(json.dumps(handlers[action]()))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
