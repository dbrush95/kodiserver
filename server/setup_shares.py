#!/usr/bin/env python3
"""Install dedicated shares, preserve existing Samba config, and validate before restart."""
import getpass
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile

def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)

def main():
    if os.geteuid() == 0:
        raise SystemExit("Run as the normal Pi account")
    account = pwd.getpwuid(os.getuid()).pw_name
    home = str(Path.home())
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", account) or not re.fullmatch(r"/[a-zA-Z0-9_./-]+", home):
        raise SystemExit("Unsupported account/home path")
    root = Path.home() / ".local/share/kodiserver"
    for name in ("inbox", "inbox/iptv", "backups", "clients"):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    config = Path("/etc/samba/smb.conf")
    include = Path("/etc/samba/kodiserver.conf")
    text = config.read_text()
    if re.search(r"(?im)^\s*\[(KodiBackups|KodiRecovery|KodiClients)\]", text):
        raise SystemExit("Share names already exist in smb.conf; review before installing")
    owner = include.with_suffix(".owner")
    if include.exists() and (not owner.exists() or owner.read_text().strip() != account):
        raise SystemExit("Existing share configuration belongs to another account")
    with tempfile.TemporaryDirectory() as tmp:
        draft = Path(tmp) / "kodiserver.conf"
        sections = []
        for label, folder, readonly in (("KodiBackups", "inbox", "no"), ("KodiPlaylists", "inbox/iptv", "no"), ("KodiRecovery", "backups", "yes"), ("KodiClients", "clients", "yes")):
            sections.append(f"[{label}]\npath = {root / folder}\nvalid users = {account}\nguest ok = no\nread only = {readonly}\nbrowseable = yes\ncreate mask = 0600\ndirectory mask = 0700\n")
        draft.write_text("\n".join(sections))
        run("sudo", "cp", "-n", str(config), "/etc/samba/smb.conf.before-kodiserver")
        original_include = include.read_text() if include.exists() else None
        new_main = text
        line = "include = /etc/samba/kodiserver.conf"
        if not re.search(r"(?im)^\s*include\s*=\s*/etc/samba/kodiserver\.conf\s*$", text):
            new_main += "\n# Kodi Server authenticated shares\n" + line + "\n"
        main_draft = Path(tmp) / "smb.conf"
        main_draft.write_text(new_main)
        run("sudo", "install", "-m", "644", str(draft), str(include))
        run("sudo", "install", "-m", "644", str(main_draft), str(config))
        try:
            run("testparm", "-s", str(config), stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            main_draft.write_text(text)
            run("sudo", "install", "-m", "644", str(main_draft), str(config))
            if original_include is None:
                run("sudo", "rm", "-f", str(include))
            else:
                draft.write_text(original_include)
                run("sudo", "install", "-m", "644", str(draft), str(include))
            raise
        owner_draft = Path(tmp) / "owner"
        owner_draft.write_text(account + "\n")
        run("sudo", "install", "-m", "644", str(owner_draft), str(owner))
    # A one-shot environment value lets a headless install finish. It is not written to disk.
    smb_password = os.environ.get("KODISERVER_SMB_PASSWORD", "")
    if smb_password:
        run("sudo", "smbpasswd", "-a", "-s", account, input=smb_password + "\n" + smb_password + "\n", text=True)
    else:
        print("Create a Samba password for your Pi account. It is entered into smbpasswd, not saved in this project.")
        run("sudo", "smbpasswd", "-a", account)
    run("sudo", "systemctl", "enable", "--now", "smbd")
    run("sudo", "systemctl", "restart", "smbd")
    print("Shares ready: KodiBackups and KodiPlaylists (write), KodiRecovery and KodiClients (read-only).")

if __name__ == "__main__":
    main()
