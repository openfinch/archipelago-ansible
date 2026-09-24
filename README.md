# Archipelago server

Ansible playbook that installs the official Archipelago Linux release on a
remote host and runs `ArchipelagoServer` as a systemd service.

Tested on Ubuntu 24.04 (x86_64). Needs `ansible-core` only, no collections.

## Setup

1. `cp inventory.ini.example inventory.ini` and set your host and SSH user.
   The user must be root or able to `sudo`.
2. Set a password in `group_vars/all.yml` (`archipelago_password`), or pass
   it with `-e`. Allowed characters: letters, digits, `-`, `_`, `.`.
3. Put each player's YAML in `players/`.
   Templates: <https://archipelago.gg/games>
4. Put any `.apworld` files for games not bundled with Archipelago in
   `custom_worlds/`.
5. Open TCP port 38281 in the host firewall or cloud security group.

## Usage

```sh
./start.sh     # start or resume the game (generates a seed if none exists)
./restart.sh   # roll a new seed and restart; asks for confirmation
./stop.sh      # stop the server; progress is saved
```

The scripts wrap the playbook. Extra arguments are passed through to Ansible,
for example `./start.sh --check`. Equivalent playbook commands:

```sh
# First run: installs Archipelago, generates a seed, starts the server.
ansible-playbook site.yml

# Later runs keep the current seed. To roll a new seed from players/:
ansible-playbook site.yml -e archipelago_regenerate=true

# Host a seed generated elsewhere (for example on archipelago.gg):
ansible-playbook site.yml -e archipelago_seed_file=/path/to/AP_123.zip
```

Players connect to `<host>:38281` with the password.

Download each player's patch file (if their game has one) from the `AP_*.zip`
in `/opt/archipelago/seeds/` on the server.

## Discord notifications

Optional. Posts to a Discord channel webhook whenever someone sends an item
to another player, pinging the receiver, and when a player completes their
goal. No bot and no extra slot are needed, so it can be turned on at any time.

Create a webhook (channel settings -> Integrations -> Webhooks), then put
it and each player's Discord user ID in `host_vars/<host>.yml` (gitignored):

```yaml
archipelago_discord_webhook_url: https://discord.com/api/webhooks/...
archipelago_discord_mentions:
  player1: "123456789012345678"
  player2: "234567890123456789"
```

User IDs: Discord settings -> Advanced -> Developer Mode, then right-click a
user -> Copy User ID. Players not listed are named in bold without a ping.

It runs as `archipelago-discord.service`, reading the server's journal.
Messages are batched every 3 seconds to stay under Discord's rate limit.

## Web tracker

Optional. A read-only page showing each player's progress, received items and
hints, plus a live feed of item sends. It connects to the server as a
"Tracker" client for every slot, keeps state in memory, and serves the page
and `/api/state` from `archipelago-tracker.service`.

Enable it by setting the address it should listen on in `host_vars/<host>.yml`,
normally one only a reverse proxy can reach:

```yaml
archipelago_tracker_listen_host: 172.18.0.1   # e.g. a Docker bridge IP
archipelago_tracker_listen_port: 38282        # default
```

Players are read from the hosted seed. Online status and the send feed start
empty when the tracker restarts; progress, items and hints come from the
server.

`/spoilers` (not linked from the main page) lists every player's remaining
locations and their items, and searches items and locations. Picking a result
shows the still-unchecked checks needed to reach it, grouped into rounds where
each round unlocks the next. To work this out the tracker rebuilds the hosted
seed with Archipelago's own logic from `players/` and the seed number, and
refuses if the result differs from the hosted seed (for example after player
files change without regenerating). It runs on a standalone Python matching
the release (`archipelago_tracker_python_*` in `group_vars/all.yml`), using
the libraries bundled with the release.

## Layout on the server

| Path | Contents |
| --- | --- |
| `/opt/archipelago/<version>/` | Extracted release |
| `/opt/archipelago/current` | Symlink to the active version |
| `/opt/archipelago/players/` | Mirror of local `players/` |
| `/opt/archipelago/custom_worlds/` | Mirror of local `custom_worlds/` |
| `/opt/archipelago/seeds/` | `AP_*.zip` seeds and `.apsave` save files |

The newest seed in `seeds/` is hosted. Old seeds and their saves are kept.

Logs: `journalctl -u archipelago -f`

## Testing

Molecule runs the playbook against a local Debian 13 systemd container
(Podman). Requires Podman and network access to GitHub.

```sh
uv tool install molecule --with ansible-core
molecule test
```

The `default` scenario checks, in order:

1. First run installs, generates a seed from `molecule/default/files/`, and
   starts the service.
2. Second run reports no changes (idempotence).
3. The service is active, port 38281 answers, and the unit has the password.
   The Discord notifier is running and turns sample server log lines into
   one batched webhook message that pings only mapped players.
   The tracker serves its page, follows every slot, and shows a check sent
   by a test client in both the player's progress and the send feed.
4. Removing a player and running with `archipelago_regenerate=true` deletes
   that player's file on the server and hosts a new seed.
5. A seed passed with `archipelago_seed_file` is uploaded and hosted.
6. Passwords with unsafe characters are rejected before anything changes.

Check 3 runs again after each of 4, 5 and 6. The release tarball is cached in
`~/.cache/archipelago-molecule/` between runs.

For a faster loop while editing: `molecule converge`, `molecule verify`,
`molecule destroy`.

## Upgrading Archipelago

Set `archipelago_version` and `archipelago_sha256` in `group_vars/all.yml`,
then re-run the playbook. The checksum is the SHA-256 of
`Archipelago_<version>_linux-x86_64.tar.gz` from the releases page.
