#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo: sudo ./scripts/install.sh" >&2
  exit 1
fi

INSTALL_USER=${SUDO_USER:-}
if [[ -z ${INSTALL_USER} || ${INSTALL_USER} == root ]]; then
  echo "Run through sudo from the account that should run the service." >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=/opt/m01-bridge

apt-get update
apt-get install -y python3-venv bluez

install -d -m 0755 "${TARGET}"
find "${TARGET}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -C "${ROOT_DIR}" --exclude=.git --exclude=.venv --exclude='*.zip' -cf - . | tar -C "${TARGET}" -xf -

python3 -m venv "${TARGET}/.venv"
"${TARGET}/.venv/bin/python" -m pip install --upgrade pip
"${TARGET}/.venv/bin/pip" install "${TARGET}"

install -m 0644 "${TARGET}/systemd/m01-bridge@.service" /etc/systemd/system/m01-bridge@.service
if [[ ! -f /etc/default/m01-bridge ]]; then
  install -m 0644 "${TARGET}/systemd/m01-bridge.env.example" /etc/default/m01-bridge
fi

usermod -aG dialout "${INSTALL_USER}"
systemctl daemon-reload

cat <<EOF

Installed to ${TARGET}.

1. Review /etc/default/m01-bridge
2. Log out and back in so the dialout group change takes effect
3. Pair/trust the Bluetooth client with bluetoothctl
4. Start the service:

   sudo systemctl enable --now m01-bridge@${INSTALL_USER}.service

If your client depends on Bluetooth SPP service discovery, see the README section
"Bluetooth pairing and SPP discovery" before enabling the service.
EOF
