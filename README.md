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
