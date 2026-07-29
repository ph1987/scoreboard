#!/usr/bin/env bash
# Provisiona o servidor do zero numa EC2 Ubuntu recem-criada.
# Uso (ja logado por SSH na instancia, como usuario ubuntu):
#   curl -fsSL https://raw.githubusercontent.com/ph1987/scoreboard/main/deploy/setup.sh | bash
set -euo pipefail

REPO="https://github.com/ph1987/scoreboard.git"
APP_DIR="/home/ubuntu/scoreboard"

echo "==> Atualizando pacotes"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https

echo "==> Clonando/atualizando o projeto"
if [ -d "$APP_DIR/.git" ]; then
	git -C "$APP_DIR" pull --ff-only
else
	git clone "$REPO" "$APP_DIR"
fi

echo "==> Instalando dependencias Python"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

echo "==> Instalando o Caddy"
if ! command -v caddy >/dev/null; then
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
		| sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
		| sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
	sudo apt-get update -qq
	sudo apt-get install -y -qq caddy
fi

echo "==> Configurando servicos"
sudo cp "$APP_DIR/deploy/scoreboard.service" /etc/systemd/system/scoreboard.service
sudo cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now scoreboard
sudo systemctl restart caddy

echo
echo "Pronto. Verifique com:"
echo "  systemctl status scoreboard"
echo "  journalctl -u scoreboard -f"
echo "  curl -s localhost:8000/api/partidas | head -c 300"
