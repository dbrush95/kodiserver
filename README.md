# Kodi Server
A lightweight home-media management portal for **Raspberry Pi OS Lite (64-bit)**, built as a Cockpit application.

## Included
- Pi login and HTTPS through Cockpit.
- Overview with real hostname, uptime, memory, available disk space, and recovery-copy count.
- **Integrated interactive terminal**, running as the signed-in Pi user.
- **Existing Plex server connection**: save its address, check port reachability from the Pi, and open Plex Web for full library management. Plex Web opens in its own tab, not an unreliable cross-origin iframe.
- Kodi Backup import from a mounted USB folder or ZIP; original files remain unchanged.
- Dated, separately labeled recovery copies plus complete Shield backup and client-restore instructions inside the portal.
- Responsive interface with keyboard navigation and accessible form labels.

No Docker, Node runtime, database, Plex token, or always-running custom backend is needed on the Pi. The helper uses Python's standard library and runs on demand through your authenticated Cockpit session.

## Install on the Pi
Start with Raspberry Pi OS Lite **64-bit**, enable SSH in Raspberry Pi Imager, and connect Ethernet if possible.

While this change is under review, use its branch explicitly:
```bash
sudo apt update
sudo apt install -y git
git clone --branch codex/pi-portal https://github.com/dbrush95/kodiserver.git
cd kodiserver
bash install.sh
```

The installer asks for sudo to install Cockpit and the portal files. Run it as your normal Pi user, not root. It does not install Plex or alter its existing server, format disks, or restore a Kodi backup.

Open **https://media-pi.local:9090** (substitute your hostname or local IP). Sign in with your Pi username/password, then select **Media server**. Cockpit initially uses a locally generated certificate, so a browser certificate warning is expected; verify that you are visiting your own Pi.

Use a strong Pi account password and keep port 9090 on your home network. No router port forwarding is required. Cockpit's terminal intentionally grants the same access as logging in to that Pi account.

## Plex connection
Open Plex in the portal and enter an address such as `http://192.168.1.50:32400`. Save, check the connection, and open Plex Web. Use Plex's own sign-in. The connection check only tests the TCP port; it does not claim to authenticate Plex or enumerate libraries.

## Shield backup
1. Plug a removable USB into the Shield.
2. Kodi → Settings → Add-ons → Install from repository → Kodi Add-on repository → Program add-ons → **Backup by robweber**.
3. Backup → Settings → Remote Directories → Browse Path. Choose a dedicated `KodiBackups` folder on the USB.
4. Enable **User Addons, Addon Data, Config Files, Database, Playlist, Profiles** if used, and **Thumbnails/Fanart**.
5. Leave compression off initially, run Backup, and wait for completion.
6. Open Restore to confirm a new dated entry exists, then cancel. Record the Kodi major version.
7. Keep a second copy on your computer.

A listed backup is not proof of a successful restore. Test on a spare compatible client before relying on it.

## Import to the Pi
Safely eject the USB, connect it to the Pi, and mount it. OS Lite may not mount drives automatically. Use `lsblk -f` in the terminal to identify the correct partition. Do not format it. If already mounted, use the displayed mount path. If not, mount the identified partition at a dedicated directory; filesystem permissions must allow your Pi user to read it.

In **Kodi backups**, enter the absolute path to **one dated backup folder** containing `xbmcbackup.val`, or a Kodi Backup ZIP, and add a label. The portal copies it into:
```text
~/.local/share/kodiserver/backups/<import-id>/<original-backup-name>
```
The original name and internal structure are preserved for Kodi Backup to recognize. ZIPs are copied without extraction; imports are not archive-integrity or playback validation. Links and special files in folder backups are rejected. Do not modify the source while copying.

Backups and settings are private to the signed-in Pi account. Neither belongs in this public repository. This release imports from Pi-accessible paths; it does not include browser uploads, downloads, an SMB server, or automatic client rollout.

## Restore / update another Kodi device
Copy the dated backup folder or ZIP shown in the portal to a destination accessible to the target device (USB or an existing authenticated network share). In its Backup add-on, set the destination to that **parent folder**, choose Restore, then select the dated entry.

A full restore is safest on the original Shield and same Kodi major version. For another Android device, select compatible settings, reinstall native add-ons for that device, and check login, video, audio, skin widgets, and remote behavior. Never restore the Android installation wholesale onto Linux or tvOS.

For updates: create a fresh backup on the source device, import a labeled recovery copy, test selected settings on one spare client, and then repeat on other clients. This portal does not automatically overwrite their settings.

## Live TV scope
This first release is the management foundation. **Tvheadend and channel sources are not installed or configured yet.** Kodi subscription add-ons do not become shared server streams through this portal. Your own Plex media remains on your existing Plex server.

## Update portal
```bash
cd kodiserver
git pull --ff-only
bash install.sh
```
Log out of Cockpit and log in again. The installer preserves user backup data.

## Validation
```bash
python3 -m unittest discover -s tests -v
node --check portal/app.js
bash -n install.sh
```
GitHub Actions additionally checks desktop/mobile browser flows using labeled test fixtures. Those checks do not validate a real Cockpit login, Raspberry Pi installation, terminal session, mounted USB, or Plex playback; test those on the Pi before calling the installation complete.

## References
- [Cockpit package development](https://docs.cockpit-project.org/cockpit-guide/main/guide/packages.html)
- [Cockpit terminal embedding](https://docs.cockpit-project.org/cockpit-guide/main/guide/embedding.html)
- [Kodi backup guidance](https://kodi.wiki/view/Backup)
- [Backup add-on settings](https://github.com/robweber/xbmcbackup/wiki/Configuration)
