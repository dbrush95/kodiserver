#!/usr/bin/env python3
"""Optional MariaDB native Kodi library. Credentials stay in the private client share."""
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import xml.etree.ElementTree as ET

def main():
    if os.geteuid() == 0:
        raise SystemExit("Run as the normal Pi account")
    host = input("Pi Ethernet hostname or IP for Kodi clients: ").strip()
    network = input("Allowed client subnet pattern (example 192.168.1.%): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or not re.fullmatch(r"(?:\d{1,3}\.){3}%", network):
        raise SystemExit("Invalid hostname or subnet pattern")
    if any(int(octet) > 255 for octet in network.rstrip(".%").split(".")):
        raise SystemExit("Invalid subnet")
    root = Path.home() / ".local/share/kodiserver/clients"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / "library.xml"
    if target.exists():
        raise SystemExit("library.xml already exists; preserving credentials. Review existing setup instead of overwriting.")
    password = secrets.token_hex(24)
    # Exact prefix grants permit Kodi's versioned databases only.
    sql = f"CREATE USER IF NOT EXISTS 'kodiclient'@'{network}' IDENTIFIED BY '{password}';\n"
    sql += f"ALTER USER 'kodiclient'@'{network}' IDENTIFIED BY '{password}';\n"
    for name in ("MyVideos%", "MyMusic%"):
        sql += f"GRANT ALL PRIVILEGES ON `{name}`.* TO 'kodiclient'@'{network}';\n"
    subprocess.run(["sudo", "systemctl", "enable", "--now", "mariadb"], check=True)
    subprocess.run(["sudo", "mariadb"], input=sql, text=True, check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "90-kodiserver.cnf"
        config.write_text("[mysqld]\nbind-address = 0.0.0.0\n")
        subprocess.run(["sudo", "install", "-m", "644", str(config), "/etc/mysql/mariadb.conf.d/90-kodiserver.cnf"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "mariadb"], check=True)
    xml = ET.Element("advancedsettings")
    for tag in ("videodatabase", "musicdatabase"):
        db = ET.SubElement(xml, tag)
        for key, value in (("type", "mysql"), ("host", host), ("port", "3306"), ("user", "kodiclient"), ("pass", password)):
            ET.SubElement(db, key).text = value
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(ET.tostring(xml, encoding="utf-8", xml_declaration=True))
    print("Native Kodi shared library prepared. Keep TCP 3306 on the home network.")
    print("Use the companion shared-library setup, restart Kodi, then scan the same network media sources on one client.")

if __name__ == "__main__":
    main()
