#!/usr/bin/env bash
# Roll a new seed from players/ and restart the server with it. The current
# game's progress is left behind (its seed and save stay on the server).
# Extra arguments go to ansible-playbook.
set -euo pipefail
cd "$(dirname "$0")"
read -r -p "This starts a NEW game and abandons current progress. Type yes to continue: " answer
[ "$answer" = yes ] || { echo "Aborted."; exit 1; }
exec ansible-playbook site.yml -e archipelago_regenerate=true "$@"
