#!/usr/bin/env bash
# Start the game: upload player files, host the newest seed (generating one
# if none exist) and start the server. Extra arguments go to ansible-playbook.
set -euo pipefail
cd "$(dirname "$0")"
exec ansible-playbook site.yml "$@"
