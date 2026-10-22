#!/usr/bin/env bash
# Installs ZeroCloud as a systemd service, so it starts by itself every time
# the Pi boots: on stage you only plug in the power cable.
# Usage: ./setup/install-service.sh [model]     (default model: llama3.2:3b)
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${1:-llama3.2:3b}"

sudo tee /etc/systemd/system/zerocloud.service >/dev/null <<EOF
[Unit]
Description=ZeroCloud compliance gateway (step 0 before ELM)
After=network.target ollama.service
Wants=ollama.service

[Service]
User=$USER
WorkingDirectory=$APP_DIR
Environment=ZC_MODEL=$MODEL
ExecStart=/usr/bin/python3 $APP_DIR/app.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable zerocloud
sudo systemctl restart zerocloud
echo "ZeroCloud service installed with model $MODEL."
echo "Live logs: journalctl -u zerocloud -f"
