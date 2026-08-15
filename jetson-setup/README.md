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
      "umask": "0002",
      "default_password": "changeme",
      "state": "present"
    }
  ],
  "directories": [
    {"name": "tfc_root", "path": "/opt/tfc-autonomous", "owner": "tfcadmin",
     "group": "tfc-autonomous", "mode": "2750", "state": "present", "env": "TFC_ROOT"},
    {"name": "tfc_config", "path": "${tfc_root}/config", "owner": "tfcadmin",
     "group": "tfc-autonomous", "mode": "2775", "state": "present", "env": "TFC_CONFIG_DIR"}
  ],
  "tfc_paths_env": {
    "dest": "${tfc_root}/config/tfc_paths.env",
    "owner": "tfcadmin", "group": "tfc-autonomous", "mode": "0644",
    "exports": {"TFC_PROJECT": "tfc-autonomous", "TFC_CONTAINER_NAME": "tfc-autonomous"}
  },
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
- `directories[].name` (optional) gives that directory a **path name**. Any string
  value anywhere in the manifest may reference it as `${name}`, composed inline
  (e.g. `"path": "${tfc_root}/repositories"`, `"dest": "${tfc_config}/tfc_paths.env"`).
  References may chain to other names. Unknown names, duplicate names and reference
  cycles are rejected with a message naming the offending field. `${...}` is
   therefore reserved — literal paths must not contain it. The `name` field itself is
   metadata and never appears in the plan output.
- `directories[].env` (optional) gives that directory a **variable name** to export
  into the runtime env file (see below). Only `present` directories with an `env`
  field are exported; `env` names must be unique and may not collide with the keys
  of `tfc_paths_env.exports`.
- `users[].groups` are supplementary memberships (the user's primary group is the
  username by default). Members are added to `docker` so they can use the container.
- `users[].default_password` (optional) sets the account's **initial** password
  (applied via `chpasswd` immediately after `useradd`). It is used **only when the
  user is created** and is never re-applied, so a member's changed password survives
  re-runs. The manifest holds it in plaintext — keep the manifest `600` and never
  commit real passwords; see `manifest-example.json` for the field placement.
- `users[].umask` (optional, 4-digit octal) sets that user's file-creation mask:
  - via `Defaults><user> umask=<umask>` in the generated sudoers file (applies to
    everything run as that user, e.g. `sudo -u tfcadmin git clone …`), and
  - via a line in the generated `/etc/profile.d/tfc-autonomous-umask.sh`
    (applies to interactive logins as that user).
  Users without `umask` keep the system default. Example: `tfcadmin` with `0027`
  creates files `0640`/dirs `0750` (group read, no world), so repos under
  `/opt/tfc-autonomous/repositories` stay non-group-writable even though the
  directory is setgid to `tfc-autonomous`.
- `sudoers` is a fully generated file (`/etc/sudoers.d/tfc-autonomous`): the file
  equals the manifest rule list **plus** the auto-generated `Defaults>user
  umask=...` lines above it. An empty rule list with no umasks removes the file.
  Do **not** add `Defaults>… umask=…` lines yourself — they are derived.
- Declaring a directory `absent` removes it **and everything below it** (recursive).
  The dry-run and run output list every child entry that will be removed
  (`also removing: <path>`). Because of that, a directory cannot be declared
  `absent` while one of its descendants is declared `present` — the manifest is
  rejected with an error telling you to mark the children `absent` too.
- Users/groups declared `absent` are removed. Removing a user keeps the home
  directory unless `"remove_home": true` is set. A removed home **cannot** be
  restored by rollback.
- Every removal operation (a `state: absent` group/user/directory, or an empty
  `sudoers` list removing the file) is logged explicitly as
  `removing: <operation>`, even without `--verbose`.
- Removing paths in the protected set (`/`, `/opt`, `/etc`, `/usr`, `/var`, …)
  requires `--force`.

### GNOME Remote Desktop

**Not managed by this script.** GNOME Remote Desktop (remote login, headless
sessions, desktop sharing) is configured manually — with `grdctl`, GNOME
Settings, or the `gnome-remote-desktop` systemd units. The script neither enables
nor disables it, so it has no prerequisite or manifest keys for RDP/GRD.

### Runtime env file (`tfc_paths_env`)

The machine's runtime paths file (the `.env` the code reads for
`TFC_ROSBAG_DIR`, `TFC_LOG_DIR`, …) is **generated from the manifest**, not written
by hand and not copied from a template:

- Each `present` directory with an `env` field is exported. The value mirrors the
  manifest's own definition: an **absolute** `path` is written verbatim
  (e.g. `TFC_ROOT="/opt/tfc-autonomous"`), while a `${name}`-referenced path keeps
  its references — translated to the referenced directory's `env` name
  (e.g. `"path": "${tfc_root}/config"` with `env: TFC_CONFIG_DIR` writes
  `TFC_CONFIG_DIR="${TFC_ROOT}/config"`). Referenced directories must therefore
  have an `env` field too. Directories are emitted in dependency order (referenced
  first) so the shell can expand the references as it sources the file.
- `exports` — an optional map of extra `KEY` → value pairs appended to the file
  (sorted by key), for values that are not directory paths.
- `dest` — where the runtime file is written (e.g. `${tfc_root}/config/tfc_paths.env`).
- `owner`, `group`, `mode` — ownership/permissions of the written file.
- The old `template`/`substitutions` mechanism is gone; a manifest still carrying
  those keys is rejected with a hint.
- Idempotent: re-runs skip the write when the destination already matches. The op
  is atomic and undoable like the sudoers file.

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

This script handles users, groups, directories, permissions, sudoers rules,
per-user umasks (sudoers `Defaults` + a generated `/etc/profile.d/` snippet) and
the runtime `tfc_paths.env` (generated from the manifest's directory `env` fields
and `tfc_paths_env.exports`).

Not handled here — separate, documented steps:

- Cloning repositories (private repos: each member clones with their own SSH key).
- Building/starting the container (`./jetson docker up --build`, see `Docker-ZED-ROS2/docker-compose.yml`).
- GNOME Remote Desktop (remote login / desktop sharing), configured manually.
