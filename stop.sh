#!/usr/bin/env bash
# Stop the Archipelago server. Progress is saved on shutdown; ./start.sh resumes.
# Extra arguments go to ansible.
set -euo pipefail
cd "$(dirname "$0")"
exec ansible archipelago --become -m ansible.builtin.systemd_service -a "name=archipelago state=stopped" "$@"
