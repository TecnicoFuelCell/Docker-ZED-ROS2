# Jetson Environment Setup

Idempotent, atomic, deterministic provisioning of the `tfc-autonomous` Jetson
environment: users, groups, directories, permissions and sudoers rules.

The manifest is the **data** (what the environment should be). The script is the
**instructions** (how to make it so). Changing permissions in the future = edit the
manifest + re-run.

## Usage

```bash
cd jetson-setup

# preview what would change (no root needed)
python3 setup_jetson.py --manifest manifest.json --dry-run

# apply (must be root)
sudo python3 setup_jetson.py --manifest manifest.json

# re-apply after editing the manifest (only diffs are applied — idempotent)
sudo python3 setup_jetson.py --manifest manifest.json

# roll back a previous run that was interrupted by a hard crash (kill -9, power loss)
sudo python3 setup_jetson.py --recover
```

Options:

| Flag | Effect |
|------|--------|
| `--manifest PATH` | Manifest file (default `manifest.json`) |
| `--dry-run` | Print the exact operations without applying anything |
| `--recover` | Roll back a previously interrupted run from its journal |
| `--force` | Allow removing paths in the protected set (`/`, `/opt`, `/etc`, …) |
| `--verbose` | Detailed per-step output |

Exit codes: `0` success, `1` any failure, `130` interrupted (Ctrl+C), `143` SIGTERM.

## Manifest schema

```json
{
  "groups": [
    {"name": "tfc-autonomous", "state": "present"}
  ],
  "users": [
    {
      "name": "member1",
      "groups": ["docker", "tfc-autonomous"],
      "home": "/home/member1",
      "shell": "/bin/bash",
      "state": "present"
    }
  ],
  "directories": [
    {"path": "/opt/tfc-autonomous/data/rosbags", "owner": "tfcadmin",
     "group": "tfc-autonomous", "mode": "2775", "state": "present"}
  ],
  "sudoers": [
    "%tfc-autonomous ALL=(tfcadmin) NOPASSWD: /usr/bin/git"
  ]
}
```

### Semantics

- `state` accepts `"present"` (create/ensure) or `"absent"` (remove).
  Anything **not listed** is left untouched — the script never guesses.
- `mode` is a 4-digit octal string, e.g. `2750` (setgid + owner rwx + group rx)
  or `2775` (setgid + owner/group rwx). The setgid bit is enforced exactly.
- `owner`/`group` may name users/groups already on the system **or** declared in
  this manifest (declared entries are applied first).
- `users[].groups` are supplementary memberships (the user's primary group is the
  username by default). Members are added to `docker` so they can use the container.
- `sudoers` is a fully generated file (`/etc/sudoers.d/tfc-autonomous`): the file
  always equals the rule list. An empty list removes the file.
- Users/groups declared `absent` are removed. Removing a user keeps the home
  directory unless `"remove_home": true` is set. A removed home **cannot** be
  restored by rollback.
- Every removal operation (a `state: absent` group/user/directory, or an empty
  `sudoers` list removing the file) is logged explicitly as
  `removing: <operation>`, even without `--verbose`.
- Removing paths in the protected set (`/`, `/opt`, `/etc`, `/usr`, `/var`, …)
  requires `--force`.

### Example: add a member

Add to `users` and re-run:

```json
{"name": "member3", "groups": ["docker", "tfc-autonomous"],
 "home": "/home/member3", "shell": "/bin/bash", "state": "present"}
```

### Example: remove a member

```json
{"name": "member2", "state": "absent"}
```

Re-run. Only that user is removed.

### Example: change a directory permission

```json
{"path": "/opt/tfc-autonomous/config/params", "owner": "tfcadmin",
 "group": "tfc-autonomous", "mode": "2775", "state": "present"}
```

Change `mode`, re-run. The script corrects the mode on the existing directory.

## Atomicity model

The script guarantees all-or-nothing: a failure at any point rolls the system back
to the state before the run.

1. **Validate** — the manifest is fully checked (names, references, modes,
   protected-path conflicts) *before anything is touched*.
2. **Snapshot** — `/etc/passwd`, `/etc/shadow`, `/etc/group`, `/etc/gshadow` are
   copied to `/var/tmp/tfc_setup/snapshot` and the prior owner/group/mode of every
   managed directory is recorded.
3. **Write-ahead journal** — before each operation executes, its undo data is
   persisted to `/var/tmp/tfc_setup/journal.jsonl`. Destructive operations
   (removals) run **last**.
4. **Apply + verify** — every operation is followed by a state re-check; a command
   that exits 0 but does not produce the desired state is treated as a failure.
5. **Rollback** — on any failure or interrupt, the account database is restored
   from the snapshot, directory changes are undone in reverse order, and the
   prior sudoers content is restored.
6. **Recovery** — if the script is killed hard (no chance to roll back), the
   journal survives on disk; the next run detects it and requires `--recover` to
   finish the rollback.

Because the script is convergent, a partial failure followed by a re-run also
reaches the desired state.

## Interrupt handling

- Ctrl+C / SIGTERM / SIGHUP are caught and **deferred**: the current command is
  allowed to finish, then the full rollback runs and the script exits `130`/`143`.
- A **second** Ctrl+C during rollback forces an immediate exit (best-effort
  rollback up to that point).

## Robustness

- All commands run via a wrapper that captures output, enforces a timeout (hung
  commands are killed with their process group) and uses exec-array form (no shell).
- Every command is post-verified against the actual system state (`getent`,
  `stat`, file content).
- sudoers changes are validated with `visudo -cf` before activation — a broken
  rule can never disable sudo.
- A lockfile prevents two concurrent runs.

## Scope

This script handles users, groups, directories, permissions and sudoers only.
Cloning repositories, building/starting the container and generating
`/opt/tfc-autonomous/config/tfc_paths.env` from the template are separate steps.
