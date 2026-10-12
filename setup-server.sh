#!/usr/bin/env bash
set -euo pipefail
if [[ "${EUID}" -eq 0 ]]; then echo "Run as the normal Pi user, not root." >&2; exit 1; fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
tv)
  sudo apt-get update
  sudo apt-get install -y curl ca-certificates
  repo_script="$(mktemp)"
  trap 'rm -f -- "$repo_script"' EXIT
  curl --fail --show-error --silent --location --proto '=https' 'https://dl.cloudsmith.io/public/tvheadend/tvheadend/setup.deb.sh' -o "$repo_script"
  # Upstream's documented repository setup; use its signed package distribution.
  sudo bash "$repo_script"
  sudo apt-get update
  # Headless installs can preseed the administrator. A normal terminal still asks.
  if [[ -n "${TVH_ADMIN_USER:-}" && -n "${TVH_ADMIN_PASSWORD:-}" ]]; then
    sudo debconf-set-selections <<EOF
tvheadend tvheadend/admin_username string ${TVH_ADMIN_USER}
tvheadend tvheadend/admin_password password ${TVH_ADMIN_PASSWORD}
EOF
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y tvheadend
  else
    sudo apt-get install -y tvheadend
  fi
  sudo systemctl enable --now tvheadend
  echo "Open http://$(hostname).local:9981. Configure admin/client accounts, channels, guide, and recording storage."
  echo "If no admin was requested, run sudo dpkg-reconfigure tvheadend before adding sources."
  ;;
shares)
  sudo apt-get update
  sudo apt-get install -y samba
  python3 "$source_dir/server/setup_shares.py"
  ;;
library)
  sudo apt-get update
  sudo apt-get install -y mariadb-server
  python3 "$source_dir/server/setup_library.py"
  ;;
*)
  echo "Usage: bash setup-server.sh tv|shares|library"
  exit 2
  ;;
esac
