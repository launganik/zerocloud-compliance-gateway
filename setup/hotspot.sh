#!/usr/bin/env bash
# Creates a Wi-Fi network called "ZeroCloud" broadcast by the Pi itself.
# It has no internet uplink: the MacBook joins it and can reach only the Pi
# (at 10.42.0.1). That is the off-grid proof for the demo.
#
# Created with autoconnect OFF on purpose: if something goes wrong, pulling
# the power cable brings the Pi back on your normal Wi-Fi. Turn autoconnect
# on only after you have tested it (see the guide).
#
# Usage: ./setup/hotspot.sh <password, at least 8 characters>
set -euo pipefail

PASSWORD="${1:?Usage: ./setup/hotspot.sh <password, at least 8 characters>}"
if [ "${#PASSWORD}" -lt 8 ]; then
  echo "The password needs at least 8 characters." >&2
  exit 1
fi

sudo nmcli connection delete ZeroCloud >/dev/null 2>&1 || true
sudo nmcli connection add type wifi ifname wlan0 con-name ZeroCloud ssid ZeroCloud \
  autoconnect no \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
  ipv4.method shared \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSWORD" \
  wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.pmf disable

echo "Hotspot 'ZeroCloud' created (not active yet)."
echo "Start it with:  sudo nmcli connection up ZeroCloud"
echo "Your SSH session will freeze: join the ZeroCloud Wi-Fi on the Mac, then: ssh $USER@10.42.0.1"
