# Kodi Server
A home media control portal for **Raspberry Pi OS Lite 64-bit**, plus a Kodi companion for shared setup.

## What runs where
| Pi server | Each Kodi device |
|---|---|
| Cockpit portal and authenticated terminal | Local playback, remote/audio settings |
| Tvheadend: supported IPTV, EPG, DVR | Tvheadend HTSP Client |
| Private backups and authenticated SMB shares | Kodi Backup and device-specific restore |
| Shared skin/weather profile and repository ZIP packages | Companion applies profile and schedules repository checks |
| Optional MariaDB native media library | One designated client scans network media |
| Published setup configuration | Streaming-service add-ons, logins and DRM playback |

This is not a remote host for SlyGuy subscription add-ons. IPTV Merge playlists must be inspected: plugin URLs, Kodi-specific properties and local proxy URLs cannot simply move into Tvheadend. The portal has a local playlist inspection tool. Even direct HTTP candidates need playback and authorization checks.

**Plex setup is deferred.** Its portal section is a placeholder; no Plex installation or connection is required.

## Install the portal
```bash
sudo apt update
sudo apt install -y git
git clone --branch codex/pi-portal https://github.com/dbrush95/kodiserver.git
cd kodiserver
bash install.sh
```
Run as your regular Pi account. The installer uses sudo for system packages, installs Cockpit, builds the companion ZIP and initializes private profile storage. Open **https://YOUR-PI:9090**, sign in with your Pi account, and choose **Media server**. Verify the address before accepting Cockpit’s local certificate.

Then enable server components as needed:
```bash
bash setup-server.sh shares
bash setup-server.sh tv
bash setup-server.sh library
```
The shares installer prompts for a Samba password. The TV installer uses [Tvheadend’s documented repository](https://docs.tvheadend.org/documentation/installation/linux), then its package installer; enter an administrator account when prompted. If no account was requested, run `sudo dpkg-reconfigure tvheadend`. Confirm package availability for your Pi OS before relying on the TV install.

The optional library installer prompts for your Pi hostname/IP and client subnet. It creates a narrowly scoped MariaDB user for Kodi’s versioned MyVideos/MyMusic databases and places credentials in the authenticated read-only client share. It does not scan or import existing libraries by itself. Do not forward ports 9090, 9981, 9982, 445 or 3306 from the router.

## Shared storage
All data stays outside Git, under `~/.local/share/kodiserver`, private to the Pi user.

| SMB share | Use |
|---|---|
| KodiBackups | Writable inbox for dated backups, preferably in a separate folder for each source device |
| KodiRecovery | Read-only imported recovery copies |
| KodiClients | Read-only profile.json, companion ZIP, repository ZIPs and optional library.xml |

In Kodi File Manager add a network location: SMB, Pi address, share name, Samba username/password. Kodi stores the credentials locally. Use a stable Ethernet address or DHCP reservation.

## New device setup
1. Install the same Kodi major version as the server profile.
2. Add the Pi SMB location, then install `service.kodiserver.client-0.2.0.zip` from **KodiClients** using Install from zip.
3. Open **Kodi Server Companion** under Program add-ons. Browse to **KodiClients/profile.json**.
4. Use its menu to install repository ZIPs packaged from your tested Shield backup, then install the required skin, weather provider and TV client. These are Kodi’s normal install dialogs; installation is not silent.
5. Apply the shared skin/weather profile. This opts that Kodi user profile into subsequent updates while idle.
6. Enter the client’s Tvheadend credentials and streaming-service logins. The companion pre-fills the TV host when configured.
7. Optionally connect the shared native movie/music library and restart Kodi. Add identical network media paths on each device; enable the daily scanning role on only one configured client.
8. Enable Kodi’s “Install updates automatically” policy if desired. The companion checks enabled repositories; Kodi remains responsible for installing available updates.

Enrollment applies to the active Kodi user profile; enroll additional profiles that should follow the same home layout. Local audio, remote controls and service account data remain separate.

## Same skin and weather everywhere
In **Devices & profile**, set Kodi version, skin ID, weather provider ID, location, temperature unit, repository interval and TV host. Publish the profile.

Configure the desired skin and weather location on the Shield first, then make an **uncompressed** Kodi Backup and import it to the server. Select that recovery copy and **Capture & publish settings**. This captures settings.xml for the selected skin/weather provider and packages installed repository add-ons.

Location names alone do not resolve provider-specific weather IDs. Use the companion’s weather setup on the source device and select the location there; then capture its resolved settings. Provider API keys and obvious login/token fields are excluded and may need entry on individual devices. Skin helper files, local artwork paths and add-on widgets may need separate migration; this is a settings template, not a guaranteed complete clone of every skin. Test a spare client before enrolling all devices.

## Scheduler and loading times
The companion waits **2–5 minutes after Kodi starts**, checks the shared profile every 15 minutes, and does repo work only while not playing. Repository check intervals are configurable from 6 to 168 hours (default 24). Offline clients catch up when they are online. It calls Kodi’s documented UpdateAddonRepos; Kodi’s own update policy and independent built-in checks still apply.

Repository ZIPs captured from backups are bootstrap copies. After they are installed, the clients use those repositories’ original upstream URLs to discover current add-ons. The Pi is not a complete upstream package mirror.

Tvheadend should fetch/maintain guide data on its own schedule once grabbers are configured. MariaDB holds native library metadata and watched status, but Kodi still performs media scans and artwork caching. One companion can request daily video-library scans while idle. Streaming add-on catalogs, authentication and skin-widget requests are not moved into MariaDB. Keep thumbnail caches local, reduce startup widgets, disable unused services, and measure cold-start times before/after.

## IPTV and DVR
Run the TV installer, then open **IPTV & DVR → Open TV management**. In Tvheadend:
- Add a backend-compatible source using an IPTV Automatic Network; set the provider’s concurrent stream limit.
- Scan/map channels, configure an XMLTV guide grabber and its update schedule.
- Select DVR storage, preferably an external mounted drive writable by the Tvheadend service account.
- Create non-admin streaming/DVR accounts for Kodi clients.
- In Kodi’s Tvheadend HTSP Client use Pi host, HTTP 9981, HTSP 9982 and that client’s credentials.

Test live TV, EPG updates and a scheduled recording before removing duplicate IPTV Simple channels. This code installs the backend; it cannot supply your channel sources, provider accounts or guide configuration.

## Shield backup directly to the server
1. Install **Backup by robweber** from Kodi’s official repository → Program add-ons.
2. Add an authenticated SMB network location for **KodiBackups**, create a dedicated **Shield** subfolder, and choose it in Backup → Settings → Remote Directories.
3. Include User Addons, Addon Data, Config Files, Database, Playlist, Profiles if used, and Thumbnails/Fanart. Leave compression off to support skin/profile capture.
4. Run Backup and verify a dated restore entry appears (cancel without restoring).
5. In the portal import that dated folder from `~/.local/share/kodiserver/inbox/Shield/<date>`, using its full absolute path.
6. Keep another copy off the Pi. Set Backup’s own schedule for periodic Shield backups.

Do not point the Backup add-on at an entire mixed-use share; it manages retention inside its selected destination.

## Restore and update clients
A full restore is safest on the original Shield using the same Kodi major version. In another Android client, choose compatible settings and reinstall native components as needed. Never restore the Android userdata tree wholesale onto Linux/tvOS.

For a recovery restore, the portal displays the original dated backup path. Browse KodiRecovery to that import’s parent folder and set it as Backup’s destination, then select the dated entry. Keep recovery and shared-profile publication separate: publishing a skin does not restore databases or overwrite streaming accounts.

## Update portal
```bash
cd kodiserver
git pull --ff-only
bash install.sh
```
Log out of Cockpit and back in. Existing user data is preserved. Companion updates are installed from its ZIP; a self-updating companion repository is not yet implemented.

## Validation and limitations
GitHub Actions tests backup/profile handling, syntax, ZIP packaging and desktop/mobile browser flows using fixtures. Real Raspberry Pi packages, Cockpit authentication/terminal, Samba access, Kodi APIs/skin behavior, EPG sources, recording storage and companion enrollment require hardware validation before rollout. No claims of measured startup improvement are made.

References: [Kodi backup](https://kodi.wiki/view/Backup), [Kodi built-ins](https://kodi.wiki/view/List_of_built-in_functions), [Tvheadend client](https://kodi.wiki/view/Tvheadend), [shared library](https://kodi.wiki/view/MySQL).
