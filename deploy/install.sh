#!/usr/bin/env bash
# Install the prax door on a board (Debian or Ubuntu, systemd). Run as root
# from a checkout of the repository:
#
#     sudo bash deploy/install.sh /srv/prax
#
# Makes the prax user, a venv with prax[serve] (the door and vectors, no
# parsers and no models), the data directory, the config and the service.
# Copy the store into the data directory before or after; the door creates
# an empty one if there is none (deploy/README.md).
set -euo pipefail

DATA="${1:-/srv/prax}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "== user and directories"
id prax >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin prax
install -d -o prax -g prax -m 0750 "$DATA"
install -d -o root -g root -m 0755 /etc/prax

echo "== the venv: prax[serve]"
if [ ! -x "$DATA/venv/bin/python" ]; then
  python3 -m venv "$DATA/venv"
fi
"$DATA/venv/bin/pip" install --quiet --upgrade pip
"$DATA/venv/bin/pip" install --quiet "$HERE[serve]"
chown -R prax:prax "$DATA/venv"

echo "== config"
if [ ! -f "$DATA/prax.yaml" ]; then
  install -o prax -g prax -m 0640 "$HERE/deploy/prax.board.yaml" "$DATA/prax.yaml"
fi
if [ ! -f /etc/prax/door.env ]; then
  install -o root -g root -m 0600 "$HERE/deploy/door.env.example" /etc/prax/door.env
  echo "   edit /etc/prax/door.env: PRAX_BIND (the board's private address) and PRAX_TOKEN"
fi

echo "== service"
install -o root -g root -m 0644 "$HERE/deploy/prax-door.service" /etc/systemd/system/prax-door.service
systemctl daemon-reload
systemctl enable prax-door.service
systemctl restart prax-door.service
sleep 2
systemctl --no-pager --lines=5 status prax-door.service || true

echo
echo "The door is at http://\$PRAX_BIND:8000 once door.env is right."
echo "From another machine:  prax --door http://<board>:8000 --token <token> doctor"
