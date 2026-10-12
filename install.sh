#!/usr/bin/env bash
set -euo pipefail
if [[ "${EUID}" -eq 0 ]]; then
  echo "Run as your regular Pi user: bash install.sh (sudo will be requested as needed)." >&2
  exit 1
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Install on Raspberry Pi OS Lite 64-bit." >&2
  exit 1
fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sudo apt-get update
sudo apt-get install -y --no-install-recommends cockpit cockpit-system cockpit-ws python3
sudo install -d -m 755 /usr/local/share/cockpit/kodiserver /usr/local/lib/kodiserver
for name in manifest.json index.html style.css app.js; do
  sudo install -m 644 "$source_dir/portal/$name" "/usr/local/share/cockpit/kodiserver/$name"
done
sudo install -m 755 "$source_dir/server/server.py" "$source_dir/server/playlist_host.py" /usr/local/lib/kodiserver/
mkdir -p "$HOME/.local/share/kodiserver/backups" "$HOME/.local/share/kodiserver/public" "$HOME/.local/share/kodiserver/inbox/iptv"
chmod 700 "$HOME/.local/share/kodiserver" "$HOME/.local/share/kodiserver/backups" "$HOME/.local/share/kodiserver/public" "$HOME/.local/share/kodiserver/inbox" "$HOME/.local/share/kodiserver/inbox/iptv"
python3 "$source_dir/server/build_client.py" "$HOME/.local/share/kodiserver/clients"
python3 /usr/local/lib/kodiserver/server.py save-profile '{}'
account="$(id -un)"
group="$(id -gn)"
sudo tee /etc/systemd/system/kodiserver-playlist.service >/dev/null <<EOF
[Unit]
Description=Kodi Server playlist and repository host
After=network-online.target
[Service]
Type=simple
User=${account}
Group=${group}
ExecStart=/usr/bin/python3 /usr/local/lib/kodiserver/playlist_host.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now cockpit.socket kodiserver-playlist.service
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  lan="$(python3 -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.168.1.1", 9)); print(s.getsockname()[0])')"
  prefix="${lan%.*}.0/24"
  sudo ufw allow from "$prefix" to any port 8877 proto tcp comment 'playlist lan' || true
fi
echo
echo "Installed. Open https://$(hostname).local:9090 and select Media server."
echo "Sign in using this Pi account. A local IP address also works."
echo "Your browser may warn about Cockpit's locally generated certificate."
echo "If updating an existing installation, log out of Cockpit and log in again."
