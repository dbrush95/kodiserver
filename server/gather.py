"""Build one house playlist from free public lists, Ohio news, and IPTV Merge."""
import gzip
import hashlib
import io
import ipaddress
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

import server

USER_AGENT = "Mozilla/5.0"
EPG_LIMIT = 20 * 1024 * 1024
CALLS = ("WHIO", "WDTN", "WKEF", "WRGT", "WPTD", "WKRC", "WSYX", "WLIO", "WTOV", "WLMB")
CITY = {
    "WHIO": "DAYTON", "WDTN": "DAYTON", "WKEF": "DAYTON", "WRGT": "DAYTON", "WPTD": "DAYTON",
    "WKRC": "CINCINNATI", "WSYX": "COLUMBUS", "WLIO": "LIMA", "WTOV": "STEUBENVILLE", "WLMB": "TOLEDO",
}
SOURCES = (
    {"name": "Ohio news", "m3u": "https://iptv-org.github.io/iptv/subdivisions/us-oh.m3u",
     "epg": "https://www.open-epg.com/files/unitedstates11.xml", "ohio": True, "ttl": 12 * 3600, "limit": 80 * 1024 * 1024},
    {"name": "Pluto TV", "m3u": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_us.m3u",
     "epg": "https://i.mjh.nz/PlutoTV/us.xml.gz", "ttl": 3 * 3600, "limit": 20 * 1024 * 1024},
    {"name": "Samsung TV Plus", "m3u": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_us.m3u",
     "epg": "https://i.mjh.nz/SamsungTVPlus/us.xml.gz", "ttl": 3 * 3600, "limit": 20 * 1024 * 1024},
    {"name": "Plex", "m3u": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_us.m3u",
     "epg": "https://i.mjh.nz/Plex/us.xml.gz", "ttl": 3 * 3600, "limit": 20 * 1024 * 1024},
    {"name": "Roku", "m3u": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/roku_all.m3u",
     "epg": "https://i.mjh.nz/Roku/all.xml.gz", "ttl": 3 * 3600, "limit": 20 * 1024 * 1024},
    {"name": "Tubi", "m3u": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_all.m3u",
     "epg": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_epg.xml", "ttl": 3 * 3600, "limit": 8 * 1024 * 1024},
    {"name": "Xumo", "m3u": "https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_playlist.m3u",
     "epg": "https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_epg.xml.gz", "ttl": 3 * 3600, "limit": 8 * 1024 * 1024},
    {"name": "PBS", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_pbs.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
)


def cache_dir():
    path = server.root() / "cache"
    path.mkdir(exist_ok=True, mode=0o700)
    return path


def safe_detail(error):
    text = str(error).split("?", 1)[0].replace("\n", " ")
    return text[:180]


def gunzip(data):
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def download(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    chunks = []
    size = 0
    with urllib.request.urlopen(request, timeout=90) as response:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError("Download exceeds the size limit")
            chunks.append(chunk)
    if not chunks:
        raise ValueError("Download was empty")
    return b"".join(chunks)


def cached_download(url, limit, ttl):
    path = cache_dir() / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".bin")
    if path.is_file() and not path.is_symlink() and time.time() - path.stat().st_mtime < ttl:
        return path.read_bytes()
    data = download(url, limit)
    server.atomic_write(path, data)
    return data


def usable_url(value):
    parsed = urlsplit(value.split("|", 1)[0])
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or parsed.username or "|" in value:
        return False
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(("youtube.com", "youtu.be")):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return bool(host)


def attributes(line):
    return dict(re.findall(r'([A-Za-z0-9_-]+)="([^"]*)"', line))


def clean(value, limit=120):
    return re.sub(r"[\r\n\"]+", " ", value or "").strip()[:limit]


def callsign(name):
    upper = name.upper()
    for call in CALLS:
        if call in upper:
            return call
    return ""


def is_lean(name, group, source):
    """Ohio stays small. Other lists contribute only news and weather to Tvheadend."""
    if source == "Ohio news":
        return True
    blob = (name + " " + group).lower()
    return "news" in blob or "weather" in blob


def walk_entries(text, source):
    direct = []
    plugins = []
    current = None
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
        current = None
        kodiprop = any(item.upper().startswith("#KODIPROP:") for item in block)
        info = next(item for item in block if item.startswith("#EXTINF"))
        meta, _, title = info.rpartition(",")
        attrs = attributes(meta)
        name = clean(title or attrs.get("tvg-name") or "Channel")
        if kodiprop or line.startswith("plugin://") or not usable_url(line):
            plugins.append(block)
            continue
        group = clean(attrs.get("group-title") or source, 80)
        direct.append({
            "name": name, "url": line, "tvg_id": clean(attrs.get("tvg-id", ""), 160),
            "logo": attrs.get("tvg-logo", "") if attrs.get("tvg-logo", "").startswith("https://") else "",
            "group": source, "source": source, "lean": is_lean(name, group, source),
        })
    return direct, plugins


def bind_ohio_guide(xml_bytes, entries):
    """Point Ohio streams at the matching Open-EPG channel when one exists."""
    wanted = {callsign(entry["name"]) for entry in entries}
    wanted.discard("")
    chosen = {}
    for _event, elem in ET.iterparse(io.BytesIO(gunzip(xml_bytes)), events=("end",)):
        if elem.tag == "channel":
            names = [item.text or "" for item in elem.findall("display-name")]
            blob = " ".join(names).upper()
            for call in wanted:
                if call not in blob:
                    continue
                score = 2 if CITY.get(call, "___") in blob else 1
                current = chosen.get(call)
                if current is None or score > current[0]:
                    chosen[call] = (score, elem.get("id"), names[0] if names else "")
            elem.clear()
    for entry in entries:
        match = chosen.get(callsign(entry["name"]))
        if match and match[1]:
            entry["tvg_id"] = match[1]
            if match[2]:
                entry["name"] = clean(match[2])


def guide_stream(source):
    """Open XMLTV from memory or a file. Gzip is streamed so a large guide is not copied twice."""
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(gunzip(bytes(source)))
    path = source if isinstance(source, Path) else Path(source)
    with path.open("rb") as handle:
        magic = handle.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return path.open("rb")


def filter_guide(xml_source, wanted, prefer, limit):
    wanted = {item for item in wanted if item}
    prefer = set(prefer) & wanted
    channels = {}
    preferred = []
    rest = []
    size = 0
    stream = guide_stream(xml_source)
    try:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag == "channel" and elem.get("id") in wanted and elem.get("id") not in channels:
                blob = ET.tostring(elem, encoding="utf-8")
                channels[elem.get("id")] = blob
                size += len(blob) + 1
            elif elem.tag == "programme" and elem.get("channel") in wanted and size < limit:
                blob = ET.tostring(elem, encoding="utf-8")
                if size + len(blob) <= limit:
                    (preferred if elem.get("channel") in prefer else rest).append(blob)
                    size += len(blob) + 1
            if elem.tag in ("channel", "programme"):
                elem.clear()
    finally:
        stream.close()
    return channels, preferred, rest, limit


def merge_guides(parts, limit):
    channels = {}
    programmes = []
    for part_channels, preferred, rest, _part_limit in parts:
        for ident, blob in part_channels.items():
            channels.setdefault(ident, blob)
        programmes.extend(preferred)
        programmes.extend(rest)
    body = [b'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="kodiserver">\n']
    size = 0
    for blob in list(channels.values()) + programmes:
        if size + len(blob) > limit:
            break
        body.append(blob)
        body.append(b"\n")
        size += len(blob) + 1
    body.append(b"</tv>\n")
    return b"".join(body), len(channels)


def render_m3u(entries, base_url, extra_blocks=()):
    lines = ['#EXTM3U url-tvg="%s/epg.xml"' % base_url]
    seen = set()
    for entry in entries:
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        logo = ' tvg-logo="%s"' % entry["logo"] if entry["logo"] else ""
        lines.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s"%s group-title="%s",%s' % (
            entry["tvg_id"], entry["name"], logo, entry["group"], entry["name"]))
        lines.append(entry["url"])
    for block in extra_blocks:
        lines.extend(block)
    return "\n".join(lines) + "\n"


def load_source(source, fetch):
    text = fetch(source["m3u"], 2 * 1024 * 1024).decode("utf-8-sig", "replace")
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("Source did not return an extended M3U")
    entries, _plugins = walk_entries(text, source["name"])
    if len(entries) > 800:
        entries = entries[:800]
    guide = ({}, [], [], 0)
    if source.get("epg") and entries:
        raw = fetch(source["epg"], source["limit"])
        if source.get("ohio"):
            bind_ohio_guide(raw, entries)
        ids = [entry["tvg_id"] for entry in entries]
        prefer = [entry["tvg_id"] for entry in entries if entry["lean"]]
        guide = filter_guide(raw, ids, prefer or ids, 8 * 1024 * 1024)
    return entries, guide


def build_house(merge_text="", merge_epg=b"", base_url="http://127.0.0.1:8877", fetch=None):
    getter = fetch or (lambda url, limit: cached_download(url, limit, next(item["ttl"] for item in SOURCES if item["m3u"] == url or item.get("epg") == url)))
    entries = []
    guides = []
    sources = []
    plugin_blocks = []
    if merge_text:
        merged, plugin_blocks = walk_entries(merge_text, "IPTV Merge")
        entries.extend(merged)
        ids = [entry["tvg_id"] for entry in merged]
        guide_channels = 0
        if merge_epg and ids:
            try:
                part = filter_guide(merge_epg, ids, ids, 8 * 1024 * 1024)
            except ET.ParseError:
                part = ({}, [], [], 0)
            guides.append(part)
            guide_channels = len(part[0])
        sources.append({"name": "IPTV Merge", "channels": len(merged), "kodi_only": len(plugin_blocks),
                        "guide": guide_channels, "ok": True, "detail": ""})
    else:
        sources.append({"name": "IPTV Merge", "channels": 0, "kodi_only": 0, "guide": 0, "ok": False,
                        "detail": "Waiting for the Shield to save playlist.m3u8 in KodiPlaylists"})
    for source in SOURCES:
        try:
            found, guide = load_source(source, getter)
            entries.extend(found)
            guides.append(guide)
            sources.append({"name": source["name"], "channels": len(found), "kodi_only": 0,
                            "guide": len(guide[0]), "ok": bool(found), "detail": "" if found else "No direct channels"})
        except Exception as error:
            sources.append({"name": source["name"], "channels": 0, "kodi_only": 0, "guide": 0,
                            "ok": False, "detail": safe_detail(error)})
    unique = []
    seen = set()
    for entry in entries:
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        unique.append(entry)
    lean = [entry for entry in unique if entry["lean"]]
    epg, guide_channels = merge_guides(guides, EPG_LIMIT)
    return {
        "playlist": render_m3u(unique, base_url),
        "tvh": render_m3u(lean, base_url),
        "kodi": render_m3u(unique, base_url, plugin_blocks),
        "epg": epg,
        "sources": sources,
        "total": len(unique) + len(plugin_blocks),
        "direct_candidates": len(unique),
        "kodi_dependent": len(plugin_blocks),
        "lean": len(lean),
        "guide_channels": guide_channels,
    }
