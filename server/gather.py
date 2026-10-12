"""Build one house playlist from free public lists, Ohio news, and IPTV Merge."""
import gzip
import hashlib
import io
import ipaddress
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import server

USER_AGENT = "Mozilla/5.0"
EPG_LIMIT = int(os.environ.get("KODISERVER_EPG_LIMIT", str(20 * 1024 * 1024)))
CALLS = ("WHIO", "WDTN", "WKEF", "WRGT", "WPTD", "WKRC", "WSYX", "WLIO", "WTOV", "WLMB")
CITY = {
    "WHIO": "DAYTON", "WDTN": "DAYTON", "WKEF": "DAYTON", "WRGT": "DAYTON", "WPTD": "DAYTON",
    "WKRC": "CINCINNATI", "WSYX": "COLUMBUS", "WLIO": "LIMA", "WTOV": "STEUBENVILLE", "WLMB": "TOLEDO",
}
# Kodi uses SlyGuy on the device for the other services. These three stay in kodi.m3u8.
KODI_SOURCES = {"Ohio news", "Tubi", "Xumo"}
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
    {"name": "Vizio", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_vizio.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
    {"name": "Klowd", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_klowdtv.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
    {"name": "SOFAST", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_sofast.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
    {"name": "Fire TV", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_firetv.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
    {"name": "ABC News", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_abcnews.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
    {"name": "CBS News", "m3u": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_cbsn.m3u",
     "ttl": 12 * 3600, "limit": 2 * 1024 * 1024},
)
# Guide files only. Used when a playlist channel has the same station name but no programmes.
# The Pi never downloads these: extra_name_guides requires a 50 MB guide cap.
NAME_GUIDE_SOURCES = (
    {"url": "https://www.open-epg.com/files/unitedstates11.xml", "limit": 80 * 1024 * 1024, "ttl": 12 * 3600},
    {"url": "https://raw.githubusercontent.com/yendorsing/KlowdTV/master/klowdtv.xml.gz", "limit": 4 * 1024 * 1024, "ttl": 12 * 3600},
    {"url": "https://i.mjh.nz/PBS/all.xml.gz", "limit": 4 * 1024 * 1024, "ttl": 12 * 3600},
    {"url": "https://raw.githubusercontent.com/acidjesuz/EPGTalk/master/US_guide.xml.gz", "limit": 20 * 1024 * 1024, "ttl": 12 * 3600},
)


def programme_limit():
    """Programme bytes kept per guide. The Pi stays at 8 MB unless the host raises it."""
    return int(os.environ.get("KODISERVER_PROGRAMME_LIMIT", str(8 * 1024 * 1024)))


def fetch_limit(default):
    raw = os.environ.get("KODISERVER_FETCH_LIMIT")
    return int(raw) if raw else default


def m3u_limit():
    return int(os.environ.get("KODISERVER_M3U_LIMIT", str(2 * 1024 * 1024)))


def channel_cap():
    return int(os.environ.get("KODISERVER_CHANNEL_CAP", "800"))


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


PLUTO_BOOT = (
    "https://boot.pluto.tv/v4/start?appName=web&appVersion=9.1.2"
    "&deviceVersion=129.0.0&deviceModel=web&deviceMake=chrome&deviceType=web"
    "&clientID={device}&clientModelNumber=1.0.0"
)


def pluto_session():
    """Public Pluto web session. The player then chooses the highest rung it can play."""
    url = PLUTO_BOOT.format(device=uuid.uuid4())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read(2 * 1024 * 1024))
    token = data.get("sessionToken") or ""
    stitcher = ((data.get("servers") or {}).get("stitcher") or "").rstrip("/")
    host = urlsplit(stitcher).hostname or ""
    if not token or urlsplit(stitcher).scheme != "https" or not host.endswith(".pluto.tv"):
        raise ValueError("Pluto session was incomplete")
    params = data.get("stitcherParams") or ""
    joiner = "&" if params and not params.endswith("&") else ""
    return {"stitcher": stitcher, "query": params + joiner + "jwt=" + token}


def rewrite_pluto(entries, session_getter=None):
    """Replace partner short links. Those return 'channel not permitted' to an iPhone."""
    needs = []
    for entry in entries:
        if entry.get("source") != "Pluto TV":
            continue
        parsed = urlsplit(entry["url"])
        if parsed.hostname != "jmp2.uk":
            continue
        slug = parsed.path.rsplit("/", 1)[-1].split(".", 1)[0]
        channel_id = slug[4:] if slug.startswith("plu-") else ""
        if not re.fullmatch(r"[0-9a-fA-F]{16,32}", channel_id):
            continue
        needs.append((entry, channel_id))
    if not needs:
        return 0
    session = (session_getter or pluto_session)()
    stitcher = session["stitcher"].rstrip("/")
    query = session["query"]
    # Without this flag the variant playlist omits the session and Pluto returns 401,
    # or the embed path plays the "not available on this device" slate.
    if "masterJWTPassthrough=" not in query:
        query += "&masterJWTPassthrough=true"
    if "includeExtendedEvents=" not in query:
        query += "&includeExtendedEvents=true"
    for entry, channel_id in needs:
        entry["url"] = "%s/v2/stitch/hls/channel/%s/master.m3u8?%s" % (stitcher, channel_id, query)
    return len(needs)


# Measured from Pluto's US ladder. 426x240 is 71:40 and 854x480 is 427:240.
# UHF treats those as a non-16:9 picture, leaves AVKit, and holds the Metal
# player on the bottom rung. Only the exact 16:9 rungs are offered.
PLUTO_FRAME = {
    1042180: (640, 360, "avc1.64001e"),
    3321280: (1280, 720, "avc1.64001f"),
}


class _Bits:
    def __init__(self, data):
        self.data = data
        self.i = 0

    def u(self, count):
        value = 0
        for _ in range(count):
            value = (value << 1) | ((self.data[self.i // 8] >> (7 - (self.i % 8))) & 1)
            self.i += 1
        return value

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("bad width code")
        return ((1 << zeros) - 1) + (self.u(zeros) if zeros else 0)

    def se(self):
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def _scaling_list(reader, size):
    last = 8
    nxt = 8
    for _ in range(size):
        if nxt:
            nxt = (last + reader.se() + 256) % 256
        last = nxt or last


def _sps_size(nal):
    """Displayed H.264 size. Pluto's 720p rung is 1280x720 or 1216x684, both 16:9."""
    raw = bytearray()
    index = 0
    while index < len(nal):
        if index + 2 < len(nal) and nal[index:index + 2] == b"\x00\x00" and nal[index + 2] == 3:
            raw += nal[index:index + 2]
            index += 3
        else:
            raw.append(nal[index])
            index += 1
    reader = _Bits(bytes(raw))
    reader.u(8)
    profile = reader.u(8)
    reader.u(8)
    reader.u(8)
    reader.ue()
    chroma = 1
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = reader.ue()
        if chroma == 3:
            reader.u(1)
        reader.ue()
        reader.ue()
        reader.u(1)
        if reader.u(1):
            for item in range(12 if chroma == 3 else 8):
                if reader.u(1):
                    _scaling_list(reader, 16 if item < 6 else 64)
    reader.ue()
    order = reader.ue()
    if order == 0:
        reader.ue()
    elif order == 1:
        reader.u(1)
        reader.se()
        reader.se()
        for _ in range(reader.ue()):
            reader.se()
    reader.ue()
    reader.u(1)
    width = (reader.ue() + 1) * 16
    height = (reader.ue() + 1) * 16
    frame_only = reader.u(1)
    if not frame_only:
        reader.u(1)
        height *= 2
    reader.u(1)
    if reader.u(1):
        unit_x = 2 if chroma in (1, 2) else 1
        unit_y = (2 if chroma == 1 else 1) * (2 - frame_only)
        left, right, top, bottom = reader.ue(), reader.ue(), reader.ue(), reader.ue()
        width -= (left + right) * unit_x
        height -= (top + bottom) * unit_y
    return width, height


def _segment_size(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-262143"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(300000)
    start = data.find(b"\x47")
    if start < 0:
        return None
    payload = bytearray()
    index = start
    while index + 188 <= len(data):
        if data[index] != 0x47:
            index += 1
            continue
        body = data[index + 4:index + 188]
        control = (data[index + 3] >> 4) & 3
        if control in (2, 3) and body:
            body = body[1 + body[0]:]
        payload += body
        index += 188
    blob = bytes(payload)
    for marker in (b"\x00\x00\x00\x01\x67", b"\x00\x00\x01\x67"):
        at = blob.find(marker)
        if at < 0:
            continue
        nal = blob[at + len(marker) - 1:]
        nxt = nal.find(b"\x00\x00\x01", 3)
        if nxt > 0:
            nal = nal[:nxt]
        width, height = _sps_size(nal)
        if 160 <= width <= 3840 and 90 <= height <= 2160:
            return width, height
    return None


def _ffprobe_size(variant_url):
    """Encrypted rungs still open in ffprobe. The clear ones are read directly."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", variant_url],
        capture_output=True, timeout=25)
    numbers = re.findall(r"\d+", proc.stdout.decode())
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    return None


def measure_pluto_frame(variant_url):
    """Read the displayed size from the first picture of one rung."""
    try:
        request = urllib.request.Request(variant_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read(256 * 1024).decode("utf-8", "replace")
        # A KEY tag with METHOD=NONE means the following pictures are clear.
        encrypted = any(
            line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line.split(":", 1)[-1]
            for line in text.splitlines()
        )
        if not encrypted:
            segment = next((urljoin(variant_url, line.strip()) for line in text.splitlines() if line and not line.startswith("#")), "")
            if segment:
                size = _segment_size(segment)
                if size:
                    return size
    except Exception:
        pass
    try:
        return _ffprobe_size(variant_url)
    except Exception:
        return None


def _pluto_parts(text, master_url):
    audio_uri = ""
    rows = []
    pending = None
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line and "describes-video" not in line:
            match = re.search(r'URI="([^"]*)"', line)
            if match and match.group(1) and ("DEFAULT=YES" in line or not audio_uri):
                audio_uri = urljoin(master_url, match.group(1))
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending = line
        elif pending and line and not line.startswith("#"):
            found = re.search(r"BANDWIDTH=(\d+)", pending)
            if found:
                rows.append((int(found.group(1)), urljoin(master_url, line.strip())))
            pending = None
    return audio_uri, rows


def apple_pluto_master(text, master_url, measure=None):
    """One 16:9 video rung plus AAC, so AVKit can play it."""
    audio_uri, rows = _pluto_parts(text, master_url)
    if not rows:
        return ""
    bandwidth, video_uri = max(rows)
    # 3321280 is Pluto's 16:9 top rung (1280x720, or 1216x684 on a cropped
    # muxed encode). Both are exact 16:9. Measuring every channel during
    # publish gets throttled and leaves the raw ladder, whose 426x240 rung
    # is 71:40 and is what UHF locks to Metal.
    frame = None
    if bandwidth in PLUTO_FRAME:
        width, height, codec = PLUTO_FRAME[bandwidth]
        frame = (width, height, codec)
    elif measure:
        for _ in range(3):
            frame = measure(video_uri)
            if frame and frame[0] * 9 == frame[1] * 16 and frame[0] >= 640:
                break
            frame = None
            time.sleep(0.4)
    elif not audio_uri and bandwidth >= 3000000:
        return video_uri + "\n"
    if not frame or frame[0] * 9 != frame[1] * 16 or frame[0] < 640:
        return ""
    codec = frame[2] if len(frame) > 2 else "avc1.64001f"
    # AVERAGE-BANDWIDTH, FRAME-RATE and CHANNELS are protocol version 7.
    # iOS AVPlayer rejects the master when those attributes sit on version 4,
    # so UHF on iPhone and iPad never reaches the 16:9 rung.
    info = (
        '#EXT-X-STREAM-INF:BANDWIDTH=%s,AVERAGE-BANDWIDTH=%s,RESOLUTION=%sx%s,'
        'FRAME-RATE=30.000,CODECS="%s,mp4a.40.2",CLOSED-CAPTIONS=NONE'
        % (bandwidth, bandwidth, frame[0], frame[1], codec)
    )
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if audio_uri:
        lines.append(
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",LANGUAGE="en",'
            'DEFAULT=YES,AUTOSELECT=YES,CHANNELS="2",CODECS="mp4a.40.2",URI="%s"' % audio_uri
        )
        info = info.replace(",CLOSED-CAPTIONS=NONE", ',AUDIO="audio",CLOSED-CAPTIONS=NONE')
    lines.extend((info, video_uri))
    return "\n".join(lines) + "\n"


def install_pluto_apple(entries, base_url, fetch=None, session_getter=None):
    """Point each Pluto channel at a local master. Leave the upstream URL if this fails."""
    jobs = []
    for entry in entries:
        if entry.get("source") != "Pluto TV":
            continue
        match = re.search(r"/channel/([0-9a-fA-F]{16,32})/master\.m3u8(?:\?|$)", entry.get("url") or "")
        if match:
            jobs.append((entry, match.group(1).lower()))
    if not jobs:
        return 0
    getter = session_getter or pluto_session

    def opener(url):
        if fetch:
            return fetch(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(512 * 1024)

    def convert(job):
        entry, channel_id = job
        text = ""
        # One web session can keep only one channel's variant playlist.
        # A second master fetch on that session ends the first playlist,
        # which is what iOS AVPlayer then refuses to play.
        for attempt in range(3):
            try:
                session = getter()
                query = session["query"]
                if "masterJWTPassthrough=" not in query:
                    query += "&masterJWTPassthrough=true"
                if "includeExtendedEvents=" not in query:
                    query += "&includeExtendedEvents=true"
                master_url = "%s/v2/stitch/hls/channel/%s/master.m3u8?%s" % (
                    session["stitcher"].rstrip("/"), channel_id, query)
                body = opener(master_url)
                text = apple_pluto_master(body.decode("utf-8", "replace"), master_url)
                break
            except Exception:
                text = ""
                time.sleep(0.6 * (attempt + 1))
        if text.startswith("https://"):
            entry["url"] = text.strip()
            return ("direct",)
        if text.startswith("#EXTM3U"):
            return ("file", channel_id, entry, text.encode())
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ready = [item for item in pool.map(convert, jobs) if item]
    folder = server.public_root() / "pluto"
    folder.mkdir(exist_ok=True, mode=0o700)
    keep = set()
    root = base_url.rstrip("/")
    for item in ready:
        if item[0] != "file":
            continue
        _kind, channel_id, entry, payload = item
        server.atomic_write(folder / (channel_id + ".m3u8"), payload)
        entry["url"] = "%s/pluto/%s.m3u8" % (root, channel_id)
        keep.add(channel_id)
    for path in folder.iterdir():
        if path.is_file() and path.suffix == ".m3u8" and path.stem not in keep:
            path.unlink()
    return len(ready)


GENRE_ORDER = {
    "News": 0, "Sports": 1, "Movies": 2, "Drama": 3, "Comedy": 4, "Crime": 5,
    "Reality": 6, "Documentary": 7, "Kids": 8, "Music": 9, "Food & Home": 10,
    "En Español": 11, "Classic": 12, "Education": 13, "Entertainment": 14, "General": 15,
}
GENRE_RULES = (
    ("News", ("news", "weather")),
    ("Sports", ("sport", "outdoor")),
    ("Kids", ("kid", "child", "family")),
    ("Movies", ("movie", "film")),
    ("Crime", ("crime", "mystery")),
    ("Comedy", ("comedy",)),
    ("Drama", ("drama", "action")),
    ("Reality", ("reality",)),
    ("Music", ("music",)),
    ("Food & Home", ("food", "cook", "home", "lifestyle")),
    ("Documentary", ("document", "science", "animal", "nature")),
    ("Classic", ("classic", "throwback")),
    ("En Español", ("español", "espanol", "latino", "spanish")),
    ("Entertainment", ("entertainment", "daytime", "game show", "competition", "talk")),
    ("Education", ("pbs", "educational")),
)


def genre_of(name, raw_group, source=""):
    """One label for the combined guide. Provider names stay in source."""
    if source in ("Ohio news", "ABC News", "CBS News"):
        return "News"
    group = (raw_group or "").casefold()
    title = name or ""
    for label, words in GENRE_RULES:
        if any(word in group for word in words):
            return label
    folded = title.casefold()
    for label, words in GENRE_RULES:
        if any(word in folded for word in words):
            return label
    return "General"


def lineup_order(entry):
    """News first, then sports, movies, and the rest of the genre list."""
    return (
        GENRE_ORDER.get(entry.get("group") or "", 50),
        (entry.get("name") or "").casefold(),
        entry.get("key") or "",
    )


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
        raw_group = clean(attrs.get("group-title") or "", 80)
        direct.append({
            "name": name, "url": line, "tvg_id": clean(attrs.get("tvg-id", ""), 160),
            "logo": attrs.get("tvg-logo", "") if attrs.get("tvg-logo", "").startswith("https://") else "",
            "group": genre_of(name, raw_group, source), "source": source,
            "lean": is_lean(name, raw_group, source),
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


def threadfin_text(value):
    """Threadfin turns every apostrophe into a quote, then drops the channel group."""
    return str(value or "").replace('"', "").replace("'", "\u2019").replace("\r", " ").replace("\n", " ")


def threadfin_url(value):
    return str(value or "").replace("'", "%27").replace('"', "%22").replace("\r", "").replace("\n", "")


def render_m3u(entries, base_url, extra_blocks=(), group=None, guide=None, threadfin=False):
    guide_url = guide or (base_url + "/epg.xml")
    if threadfin:
        guide_url = threadfin_url(guide_url)
    lines = ['#EXTM3U url-tvg="%s"' % guide_url]
    seen = set()
    for entry in entries:
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        name = threadfin_text(entry["name"]) if threadfin else entry["name"]
        ident = threadfin_text(entry["tvg_id"]) if threadfin else entry["tvg_id"]
        logo_value = threadfin_url(entry["logo"]) if threadfin else entry["logo"]
        group_value = threadfin_text(group or entry["group"]) if threadfin else (group or entry["group"])
        logo = ' tvg-logo="%s"' % logo_value if logo_value else ""
        number = ' tvg-chno="%s"' % entry["number"] if entry.get("number") else ""
        # Threadfin 1.2.37 stores one channel per tvg-name. The visible name stays after the comma.
        guide_name = name
        if threadfin:
            suffix = entry.get("number") or ident or entry.get("key") or ""
            if suffix:
                guide_name = "%s %s" % (name, suffix)
        lines.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s"%s%s group-title="%s",%s' % (
            ident, guide_name, logo, number, group_value, name))
        lines.append(threadfin_url(entry["url"]) if threadfin else entry["url"])
    for block in extra_blocks:
        lines.extend(block)
    return "\n".join(lines) + "\n"


def load_source(source, fetch):
    text = fetch(source["m3u"], m3u_limit()).decode("utf-8-sig", "replace")
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("Source did not return an extended M3U")
    entries, _plugins = walk_entries(text, source["name"])
    cap = channel_cap()
    if len(entries) > cap:
        entries = entries[:cap]
    guide = ({}, [], [], 0)
    if source.get("epg") and entries:
        raw = fetch(source["epg"], fetch_limit(source["limit"]))
        if source.get("ohio"):
            bind_ohio_guide(raw, entries)
        ids = [entry["tvg_id"] for entry in entries]
        prefer = [entry["tvg_id"] for entry in entries if entry["lean"]]
        guide = filter_guide(raw, ids, prefer or ids, programme_limit())
    return entries, guide


TUNER_SIZE = 450
TUNER_PORT = 34400
GENERIC_NAMES = {"news", "weather", "sports", "movies", "movie", "music", "kids", "live", "channel"}
KEEP_FIELDS = ("name", "url", "tvg_id", "logo", "group", "source", "lean", "key", "height", "bandwidth")


def normalize_name(name):
    """Same station, ignoring the quality words a list sticks on the title."""
    text = (name or "").casefold().replace("&", " and ")
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\((?:hd|sd|fhd|uhd|hdtv|1080p?|720p?|480p?|4k|us|usa)\)", " ", text)
    text = re.sub(r"\b(?:hd|sd|fhd|uhd|hdtv|1080p|720p|480p|1080|720|4k)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def identity_of(name):
    normal = normalize_name(name)
    if len(normal) < 6 or normal in GENERIC_NAMES:
        return ""
    return normal


def numbered_identity(name):
    """Trailing copy number, such as ABC News Live 3. Station numbers such as Boston 25 stay put."""
    normal = normalize_name(name)
    stripped = re.sub(r" \d{1,2}$", "", normal)
    if stripped == normal or re.search(r" \d+$", stripped):
        return ""
    if len(stripped) < 12 or stripped in GENERIC_NAMES:
        return ""
    return stripped


def extra_name_guides(fetch=None):
    """Other published guides. Skipped on the Pi, where the merged guide stays at 20 MB."""
    if EPG_LIMIT < 50 * 1024 * 1024:
        return []
    found = []
    for item in NAME_GUIDE_SOURCES:
        try:
            if fetch:
                found.append(fetch(item["url"], item["limit"]))
            else:
                found.append(cached_download(item["url"], item["limit"], item["ttl"]))
        except Exception:
            continue
    return found


def _guide_elements(xml_source):
    stream = guide_stream(xml_source)
    try:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            yield elem
    except ET.ParseError:
        return
    finally:
        stream.close()


def local_tag(tag):
    return (tag or "").rsplit("}", 1)[-1]


def programme_counts(xml_source, minimum=""):
    """How many programmes each channel has. minimum is a YYYYMMDD stop date."""
    counts = {}
    if not xml_source:
        return counts
    for elem in _guide_elements(xml_source):
        if local_tag(elem.tag) == "programme":
            ident = elem.get("channel") or ""
            stop = (elem.get("stop") or "")[:8]
            if ident and (not minimum or (stop.isdigit() and stop >= minimum)):
                counts[ident] = counts.get(ident, 0) + 1
        if local_tag(elem.tag) in ("channel", "programme"):
            elem.clear()
    return counts


def display_names(elem):
    found = []
    for child in list(elem):
        if local_tag(child.tag) == "display-name" and child.text:
            found.append(child.text)
    return found


def winning_programmes(xml_source, needed, today):
    """Current programmes for the one channel per station name with the most listings."""
    if not xml_source or not needed or not today:
        return {}
    keys = {}
    stats = {}
    for elem in _guide_elements(xml_source):
        if local_tag(elem.tag) == "channel":
            ident = elem.get("id") or ""
            for label in display_names(elem):
                key = identity_of(label)
                if ident and key in needed:
                    keys[ident] = key
                    break
        elif local_tag(elem.tag) == "programme":
            ident = elem.get("channel") or ""
            stop = (elem.get("stop") or "")[:8]
            if ident and stop.isdigit():
                current, total, latest = stats.get(ident, (0, 0, ""))
                total += 1
                if stop >= today:
                    current += 1
                    if stop > latest:
                        latest = stop
                stats[ident] = (current, total, latest)
        if local_tag(elem.tag) in ("channel", "programme"):
            elem.clear()
    best = {}
    tied = set()
    for ident, key in keys.items():
        score = stats.get(ident)
        if not score or score[0] <= 0:
            continue
        current = best.get(key)
        if current is None or score > current[0]:
            best[key] = (score, ident)
            tied.discard(key)
        elif score == current[0] and ident != current[1]:
            tied.add(key)
    winners = {key: ident for key, (score, ident) in best.items() if key not in tied}
    if not winners:
        return {}
    wanted = set(winners.values())
    blobs = {ident: [] for ident in wanted}
    for elem in _guide_elements(xml_source):
        if local_tag(elem.tag) == "programme" and (elem.get("channel") or "") in blobs:
            stop = (elem.get("stop") or "")[:8]
            if stop.isdigit() and stop >= today:
                blobs[elem.get("channel")].append(ET.tostring(elem, encoding="utf-8"))
        if local_tag(elem.tag) in ("channel", "programme"):
            elem.clear()
    return {key: blobs.get(ident) or [] for key, ident in winners.items()}


def station_calls(name, tvg_id):
    """Call letters printed on a PBS station, not a subchannel such as Create."""
    upper = (name or "").upper()
    if "PBS" not in upper or re.search(r"\bDT2\b|CREATE", upper):
        return []
    calls = []
    for token in re.findall(r"\(([A-Z0-9]{4,6})\)", upper):
        if re.match(r"^[KW][A-Z]{3}$", token):
            calls.append(token)
        elif token.endswith("TV") and re.match(r"^[KW][A-Z]{3}$", token[:-2]):
            calls.append(token[:-2])
    for token in re.findall(r"\b([A-Z]{3,}\d)\b", upper):
        if token not in calls:
            calls.append(token)
    if calls:
        return calls
    feed = (tvg_id or "").rsplit("@", 1)[-1].upper()
    if feed.endswith("TV") and re.match(r"^[KW][A-Z]{3}$", feed[:-2]):
        feed = feed[:-2]
    if re.match(r"^[KW][A-Z]{3}$", feed):
        return [feed]
    return []


def programmes_by_call(xml_source, wanted, today):
    """Current PBS programmes keyed by call letters such as KETS or KET2."""
    if not xml_source or not wanted or not today:
        return {}
    owners = {}
    for elem in _guide_elements(xml_source):
        if local_tag(elem.tag) == "channel":
            ident = elem.get("id") or ""
            blob = " ".join(display_names(elem)).upper()
            if ident and "PBS" in blob:
                found = set(re.findall(r"\b([KW][A-Z]{3})\b", blob))
                found.update(re.findall(r"\b([A-Z]{3,}\d)\b", blob))
                for call in found & wanted:
                    owners.setdefault(call, set()).add(ident)
        if local_tag(elem.tag) in ("channel", "programme"):
            elem.clear()
    chosen = {call: next(iter(ids)) for call, ids in owners.items() if len(ids) == 1}
    if not chosen:
        return {}
    wanted_ids = set(chosen.values())
    blobs = {ident: [] for ident in wanted_ids}
    for elem in _guide_elements(xml_source):
        if local_tag(elem.tag) == "programme" and (elem.get("channel") or "") in blobs:
            stop = (elem.get("stop") or "")[:8]
            if stop.isdigit() and stop >= today:
                blobs[elem.get("channel")].append(ET.tostring(elem, encoding="utf-8"))
        if local_tag(elem.tag) in ("channel", "programme"):
            elem.clear()
    return {call: blobs.get(ident) or [] for call, ident in chosen.items()}


def retarget_programme(blob, channel_id):
    elem = ET.fromstring(blob)
    elem.set("channel", channel_id)
    return ET.tostring(elem, encoding="utf-8")


def copy_listings_by_name(xml_bytes, entries, donors=()):
    """Give a channel the programmes of the same station name. Generic names are left alone."""
    xml_bytes = xml_bytes or b'<?xml version="1.0" encoding="UTF-8"?>\n<tv></tv>\n'
    today = time.strftime("%Y%m%d", time.gmtime())
    counts = programme_counts(xml_bytes, today)
    pending = []
    seen = set()
    for entry in entries:
        ident = entry.get("tvg_id") or ""
        if not ident or ident in seen or counts.get(ident):
            continue
        seen.add(ident)
        exact = identity_of(entry.get("name") or "")
        loose = numbered_identity(entry.get("name") or "")
        calls = station_calls(entry.get("name") or "", ident)
        if exact or loose or calls:
            pending.append([ident, exact, loose, calls])
    if not pending:
        return xml_bytes, 0
    groups = []
    for source in (xml_bytes,) + tuple(donors):
        if not pending:
            break
        needed = {key for _ident, exact, loose, _calls in pending for key in (exact, loose) if key}
        try:
            found = winning_programmes(source, needed, today)
        except Exception:
            found = {}
        try:
            call_found = programmes_by_call(
                source, {call for _ident, _exact, _loose, calls in pending for call in calls}, today)
        except Exception:
            call_found = {}
        still = []
        for ident, exact, loose, calls in pending:
            blobs = found.get(exact) if exact else None
            if not blobs and loose and loose != exact:
                blobs = found.get(loose)
            if not blobs:
                for call in calls:
                    blobs = call_found.get(call)
                    if blobs:
                        break
            if not blobs:
                still.append([ident, exact, loose, calls])
                continue
            try:
                groups.append([retarget_programme(blob, ident) for blob in blobs])
            except ET.ParseError:
                still.append([ident, exact, loose, calls])
        pending = still
    if not groups:
        return xml_bytes, 0
    room = EPG_LIMIT - len(xml_bytes)
    kept = []
    size = 0
    copied = 0
    for group in groups:
        need = sum(len(blob) + 1 for blob in group)
        if need < 1 or size + need > room:
            continue
        kept.extend(group)
        size += need
        copied += 1
    if not kept:
        return xml_bytes, 0
    addition = b"\n".join(kept) + b"\n"
    if b"</tv>" in xml_bytes:
        xml_bytes = xml_bytes.replace(b"</tv>", addition + b"</tv>", 1)
    else:
        xml_bytes = xml_bytes + b"<tv>\n" + addition + b"</tv>\n"
    return xml_bytes, copied


def name_height(name):
    text = (name or "").casefold()
    if re.search(r"1080|fhd|\buhd\b|\b4k\b", text):
        return 1080
    if re.search(r"\b720\b|720p|\bhd\b|hdtv", text):
        return 720
    if re.search(r"\b480\b|480p|\bsd\b", text):
        return 480
    return 0


def xml_text(value):
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def read_json(path, default):
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        return default
    try:
        data = json.loads(file.read_text())
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def assign_keys(entries):
    seen = set()
    for entry in entries:
        ident = entry.get("tvg_id") or identity_of(entry.get("name") or "") or (entry.get("name") or "").casefold()
        key = hashlib.sha256((entry.get("source", "") + "\n" + ident).encode()).hexdigest()[:16]
        if key in seen:
            key = hashlib.sha256((key + "\n" + entry["url"]).encode()).hexdigest()[:16]
        seen.add(key)
        entry["key"] = key


def decision_map():
    data = read_json(server.root() / "lineup-decisions.json", {})
    chosen = {}
    for key in data.get("active") or []:
        if re.fullmatch(r"[0-9a-f]{16}", str(key)):
            chosen[key] = True
    for key in data.get("inactive") or []:
        if re.fullmatch(r"[0-9a-f]{16}", str(key)) and key not in chosen:
            chosen[key] = False
    return chosen


def save_decision(key, active):
    if not re.fullmatch(r"[0-9a-f]{16}", key or ""):
        raise ValueError("Unknown channel")
    path = server.root() / "lineup-decisions.json"
    data = read_json(path, {})
    active_keys = [item for item in data.get("active") or [] if item != key]
    inactive_keys = [item for item in data.get("inactive") or [] if item != key]
    (active_keys if active else inactive_keys).append(key)
    server.atomic_write(path, json.dumps({"active": active_keys, "inactive": inactive_keys}).encode())


def read_master(url):
    """Read the advertised top rung. The media itself is not downloaded."""
    request = urllib.request.Request(url.split("|", 1)[0], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(65536)
    except Exception:
        return {"bandwidth": 0, "height": 0}
    text = data.decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in text:
        return {"bandwidth": 0, "height": 0}
    bandwidths = [int(item) for item in re.findall(r"BANDWIDTH=(\d+)", text)]
    heights = [int(item) for item in re.findall(r"RESOLUTION=\d+x(\d+)", text)]
    return {
        "bandwidth": max(bandwidths) if bandwidths else 0,
        "height": max(heights) if heights else 0,
    }


def measure_duplicates(entries, probe):
    groups = {}
    for entry in entries:
        entry["identity"] = identity_of(entry.get("name") or "")
        groups.setdefault(entry["identity"] or entry["key"], []).append(entry)
    todo = [entry for group in groups.values() if len(group) > 1 for entry in group]
    for entry in entries:
        entry.setdefault("height", 0)
        entry.setdefault("bandwidth", 0)
    if not todo:
        return groups
    if probe == "stored":
        return groups
    if probe is not None:
        for entry in todo:
            sample = probe(entry["url"]) or {}
            entry["height"] = int(sample.get("height") or 0)
            entry["bandwidth"] = int(sample.get("bandwidth") or 0)
        return groups
    cache_path = cache_dir() / "quality.json"
    cache = read_json(cache_path, {})
    missing = []
    now = time.time()
    for entry in todo:
        digest = hashlib.sha256((entry.get("source", "") + "\n" + (entry.get("tvg_id") or entry["url"])).encode()).hexdigest()[:20]
        entry["_quality"] = digest
        row = cache.get(digest)
        if isinstance(row, dict) and now - float(row.get("checked") or 0) < 24 * 3600:
            entry["height"] = int(row.get("height") or 0)
            entry["bandwidth"] = int(row.get("bandwidth") or 0)
        else:
            missing.append(entry)
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            samples = list(pool.map(read_master, [entry["url"] for entry in missing]))
        for entry, sample in zip(missing, samples):
            entry["height"] = int(sample.get("height") or 0)
            entry["bandwidth"] = int(sample.get("bandwidth") or 0)
            cache[entry["_quality"]] = {
                "height": entry["height"], "bandwidth": entry["bandwidth"], "checked": int(now),
            }
        server.atomic_write(cache_path, json.dumps(cache).encode())
    for entry in entries:
        entry.pop("_quality", None)
    return groups


def rank_height(entry):
    return int(entry.get("height") or 0) or name_height(entry.get("name") or "")


def quality_text(entry):
    parts = []
    height = rank_height(entry)
    bandwidth = int(entry.get("bandwidth") or 0)
    if height:
        parts.append("%dp" % height)
    if bandwidth:
        parts.append("%.1f Mbps" % (bandwidth / 1000000))
    return " · ".join(parts) if parts else "quality not measured"


def rank_entries(entries, groups, overrides):
    source_order = {item["name"]: index for index, item in enumerate(SOURCES)}
    source_order["IPTV Merge"] = 100
    winners = {}
    for ident, group in groups.items():
        ordered = sorted(group, key=lambda entry: (
            -rank_height(entry), -int(entry.get("bandwidth") or 0),
            source_order.get(entry.get("source"), 90), entry["key"]))
        best = ordered[0]
        winners[ident] = best
        for entry in ordered:
            forced = overrides.get(entry["key"])
            if forced is True:
                entry["active"] = True
            elif forced is False:
                entry["active"] = False
            else:
                entry["active"] = entry is best
            entry["duplicate"] = len(group) > 1 and entry is not best
            entry["kept_key"] = best["key"]
            entry["kept_name"] = best["name"]
            entry["kept_source"] = best["source"]
    return winners


def assign_numbers(entries):
    path = server.root() / "lineup-numbers.json"
    numbers = read_json(path, {})
    used = {int(value) for value in numbers.values() if isinstance(value, int)}
    nxt = max(used or {0}) + 1
    changed = False
    for entry in sorted(entries, key=lineup_order):
        current = numbers.get(entry["key"])
        if not isinstance(current, int):
            while nxt in used:
                nxt += 1
            numbers[entry["key"]] = nxt
            used.add(nxt)
            nxt += 1
            changed = True
        entry["number"] = int(numbers[entry["key"]])
    if changed:
        server.atomic_write(path, json.dumps(numbers).encode())


def assign_tuners(active):
    path = server.root() / "lineup-tuners.json"
    store = read_json(path, {})
    counts = {}
    pending = []
    for entry in sorted(active, key=lambda item: item["number"]):
        idx = store.get(entry["key"])
        if isinstance(idx, int) and idx > 0 and counts.get(idx, 0) < TUNER_SIZE:
            entry["tuner"] = idx
            counts[idx] = counts.get(idx, 0) + 1
        else:
            pending.append(entry)
    changed = False
    for entry in pending:
        for idx in range(1, 64):
            if counts.get(idx, 0) < TUNER_SIZE:
                entry["tuner"] = idx
                if store.get(entry["key"]) != idx:
                    store[entry["key"]] = idx
                    changed = True
                counts[idx] = counts.get(idx, 0) + 1
                break
        else:
            raise ValueError("The lineup needs more than 63 tuners")
    if changed:
        server.atomic_write(path, json.dumps(store).encode())


def channel_ids(xml_bytes):
    found = set()
    stream = guide_stream(xml_bytes)
    try:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag == "channel" and elem.get("id"):
                found.add(elem.get("id"))
            if elem.tag in ("channel", "programme"):
                elem.clear()
    finally:
        stream.close()
    return found


def add_guide_stubs(xml_bytes, entries):
    """Every mapped channel gets a guide name, even when the source has no programmes."""
    present = channel_ids(xml_bytes) if xml_bytes else set()
    stubs = []
    for entry in entries:
        if not entry.get("tvg_id"):
            entry["tvg_id"] = "house-" + entry["key"]
        if entry["tvg_id"] in present:
            continue
        stubs.append('<channel id="%s"><display-name>%s</display-name><category lang="en">%s</category></channel>' % (
            xml_text(entry["tvg_id"]), xml_text(entry["name"]), xml_text(entry.get("group") or "General")))
        present.add(entry["tvg_id"])
    if not stubs:
        return xml_bytes or b'<?xml version="1.0" encoding="UTF-8"?>\n<tv></tv>\n'
    addition = ("\n".join(stubs) + "\n").encode()
    if b"</tv>" in xml_bytes:
        return xml_bytes.replace(b"</tv>", addition + b"</tv>", 1)
    return xml_bytes + b"<tv>\n" + addition + b"</tv>\n"


def tag_channel_genres(xml_bytes, entries):
    """Put the same genre on guide channels that already came from a provider file."""
    genres = {}
    for entry in entries:
        ident = entry.get("tvg_id") or ""
        if ident:
            genres.setdefault(ident, entry.get("group") or "General")
    if not xml_bytes or not genres:
        return xml_bytes

    def replace(match):
        ident = match.group(1).decode().replace("&amp;", "&").replace("&quot;", '"')
        body = match.group(0)
        genre = genres.get(ident)
        if not genre or b"<category" in body:
            return body
        label = xml_text(genre).encode()
        return body.replace(b"</channel>", b'<category lang="en">%s</category></channel>' % label, 1)

    return re.sub(br'<channel\b[^>]*\bid="([^"]+)"[^>]*>.*?</channel>', replace, xml_bytes, flags=re.DOTALL)


def guide_index(xml_bytes):
    names = {}
    shows = {}
    if not xml_bytes:
        return names, shows
    stream = guide_stream(xml_bytes)
    try:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag == "channel":
                ident = elem.get("id") or ""
                if ident and ident not in names:
                    label = ""
                    for child in elem.findall("display-name"):
                        if child.text:
                            label = clean(child.text)
                            break
                    names[ident] = label
            elif elem.tag == "programme":
                ident = elem.get("channel") or ""
                bucket = shows.setdefault(ident, [])
                title_node = elem.find("title")
                title = clean(title_node.text) if title_node is not None and title_node.text else ""
                if title and len(bucket) < 2:
                    bucket.append({"start": (elem.get("start") or "")[:19], "title": title})
            if elem.tag in ("channel", "programme"):
                elem.clear()
    finally:
        stream.close()
    return names, shows


def tuner_guide(xml_bytes, entries):
    ids = [entry["tvg_id"] for entry in entries if entry.get("tvg_id")]
    if not ids:
        return b'<?xml version="1.0" encoding="UTF-8"?>\n<tv></tv>\n'
    part = filter_guide(xml_bytes, ids, ids, max(programme_limit(), 8 * 1024 * 1024))
    body, _count = merge_guides([part], max(EPG_LIMIT, 8 * 1024 * 1024))
    return body


def public_lineup(entries, names, shows):
    active = [entry for entry in entries if entry["active"]]
    held = []
    by_key = {entry["key"]: entry for entry in entries}
    for entry in entries:
        if not entry.get("duplicate"):
            continue
        kept = by_key.get(entry["kept_key"])
        held.append({
            "key": entry["key"],
            "name": entry["name"],
            "source": entry["source"],
            "group": entry["group"],
            "quality": quality_text(entry),
            "kept_name": entry["kept_name"],
            "kept_source": entry["kept_source"],
            "kept_quality": quality_text(kept) if kept else "",
            "guide_name": names.get(entry.get("tvg_id"), ""),
            "programmes": shows.get(entry.get("tvg_id"), []),
            "kept_guide_name": names.get(kept.get("tvg_id"), "") if kept else "",
            "kept_programmes": shows.get(kept.get("tvg_id"), []) if kept else [],
            "active": entry["active"],
        })
    held.sort(key=lambda item: (item["name"].casefold(), item["source"].casefold()))
    counts = {}
    for entry in active:
        counts[entry["tuner"]] = counts.get(entry["tuner"], 0) + 1
    return {
        "active": len(active),
        "inactive": len(entries) - len(active),
        "held": held,
        "sort": "genre, then channel name",
        "quality": "measured picture height, then advertised bitrate",
        "tuners": [
            {"index": idx, "port": TUNER_PORT + idx - 1, "channels": counts[idx]}
            for idx in sorted(counts)
        ],
    }


def finish_lineup(entries, plugins, epg_source, base_url, probe=None, name_guides=()):
    assign_keys(entries)
    for entry in entries:
        if entry.get("key") and not entry.get("tvg_id"):
            entry["tvg_id"] = "house-" + entry["key"]
    groups = measure_duplicates(entries, probe)
    rank_entries(entries, groups, decision_map())
    assign_numbers(entries)
    active = [entry for entry in entries if entry["active"]]
    assign_tuners(active)
    filled, name_matches = copy_listings_by_name(epg_source, entries, name_guides)
    published = tag_channel_genres(add_guide_stubs(filled, entries), entries)
    names, shows = guide_index(published)
    ordered = sorted(active, key=lineup_order)
    # UHF gets every direct channel. Kodi gets Tubi, Ohio local news, and Xumo only.
    everyone = sorted(entries, key=lineup_order)
    kodi_rows = [entry for entry in everyone if entry.get("source") in KODI_SOURCES]
    lean = [entry for entry in ordered if entry.get("lean")]
    slices = {}
    for entry in active:
        slices.setdefault(entry["tuner"], []).append(entry)
    tuner_files = []
    for idx in sorted(slices):
        rows = sorted(slices[idx], key=lambda entry: entry["number"])
        tuner_files.append({
            "index": idx,
            "playlist": render_m3u(
                rows, base_url, group="House", threadfin=True,
                guide="%s/tuners/%s.xml" % (base_url, idx)),
            "guide": tuner_guide(published, rows),
        })
    lineup = public_lineup(entries, names, shows)
    internal = {
        "entries": [{field: entry.get(field, "") for field in KEEP_FIELDS} for entry in entries],
        "plugins": plugins,
    }
    return {
        "playlist": render_m3u(everyone, base_url),
        "tvh": render_m3u(lean, base_url),
        "kodi": render_m3u(kodi_rows, base_url, guide=base_url + "/kodi.xml"),
        "kodi_epg": tuner_guide(published, kodi_rows),
        "epg": published,
        "epg_source": filled,
        "name_matches": name_matches,
        "lineup": lineup,
        "tuners": tuner_files,
        "internal": internal,
        "lean": len(lean),
        "active": lineup["active"],
        "inactive": lineup["inactive"],
    }


def store_house(payload):
    folder = server.public_root()
    tuner_dir = folder / "tuners"
    tuner_dir.mkdir(exist_ok=True, mode=0o700)
    server.atomic_write(folder / "playlist.m3u8", payload["playlist"].encode())
    server.atomic_write(folder / "tvh.m3u8", payload["tvh"].encode())
    server.atomic_write(folder / "kodi.m3u8", payload["kodi"].encode())
    server.atomic_write(folder / "kodi.xml", payload["kodi_epg"])
    server.atomic_write(folder / "epg.xml", payload["epg"])
    server.atomic_write(folder / "lineup.json", json.dumps(payload["lineup"]).encode())
    server.atomic_write(server.root() / "guide-source.xml", payload["epg_source"])
    internal_path = server.root() / "lineup-internal.json"
    server.atomic_write(internal_path, json.dumps(payload["internal"]).encode())
    os.chmod(internal_path, 0o600)
    keep = set()
    for tuner in payload["tuners"]:
        stem = str(tuner["index"])
        keep.add(stem)
        server.atomic_write(tuner_dir / (stem + ".m3u8"), tuner["playlist"].encode())
        server.atomic_write(tuner_dir / (stem + ".xml"), tuner["guide"])
    for path in tuner_dir.iterdir():
        if path.is_file() and path.suffix in {".m3u8", ".xml"} and path.stem not in keep:
            path.unlink()


def rebuild_saved(base_url):
    internal_path = server.root() / "lineup-internal.json"
    source_path = server.root() / "guide-source.xml"
    if not internal_path.is_file() or internal_path.is_symlink() or not source_path.is_file() or source_path.is_symlink():
        raise ValueError("The lineup is still loading")
    internal = json.loads(internal_path.read_text())
    return finish_lineup(internal["entries"], internal.get("plugins") or [], source_path.read_bytes(), base_url, probe="stored")


def build_house(merge_text="", merge_epg=b"", base_url="http://127.0.0.1:8877", fetch=None, probe=None):
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
                part = filter_guide(merge_epg, ids, ids, programme_limit())
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
    try:
        rewritten = rewrite_pluto(entries)
        apple = install_pluto_apple(entries, base_url) if rewritten else 0
        if rewritten:
            for item in sources:
                if item["name"] == "Pluto TV":
                    item["detail"] = "16:9 AVKit masters" if apple else "direct master playlists"
    except Exception as error:
        for item in sources:
            if item["name"] == "Pluto TV":
                item["detail"] = safe_detail(error)
    unique = []
    seen = set()
    for entry in entries:
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        unique.append(entry)
    epg, guide_channels = merge_guides(guides, EPG_LIMIT)
    donors = extra_name_guides(fetch)
    payload = finish_lineup(unique, plugin_blocks, epg, base_url, probe=probe, name_guides=donors)
    matched = payload.get("name_matches") or 0
    sources.append({"name": "Same-name guides", "channels": matched, "kodi_only": 0, "guide": matched,
                    "ok": True, "detail": "station name already had programmes"})
    payload.update(sources=sources, total=len(unique) + len(plugin_blocks),
                   direct_candidates=len(unique), kodi_dependent=len(plugin_blocks),
                   guide_channels=guide_channels)
    return payload
