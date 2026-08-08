#!/usr/bin/env python3
"""Provision the tfc-autonomous Jetson environment from a JSON manifest.

Idempotent, atomic, deterministic. Run as root:

    sudo python3 setup_jetson.py --manifest manifest.json   # apply
    python3 setup_jetson.py --manifest manifest.json --dry-run  # preview only
    sudo python3 setup_jetson.py --recover                  # roll back a crashed run

See README.md for the manifest schema and the atomicity model.
"""

import argparse
import fcntl
import grp
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SIGINT = 130
EXIT_SIGTERM = 143

WORK_DIR = "/var/tmp/tfc_setup"
JOURNAL_PATH = os.path.join(WORK_DIR, "journal.jsonl")
LOCK_PATH = "/var/lock/tfc_setup.lock"
SUDOERS_FILENAME = "tfc-autonomous"
SUDOERS_PATH = os.path.join("/etc/sudoers.d", SUDOERS_FILENAME)
UMASK_PROFILE_PATH = "/etc/profile.d/tfc-autonomous-umask.sh"

ACCOUNT_FILES = ["passwd", "shadow", "group", "gshadow"]

NAME_RE = re.compile(r"^[a-z_][a-z0-9._-]*$")
MODE_RE = re.compile(r"^[0-7]{4}$")
REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_-]*)\}")
DIR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

PROTECTED_PATHS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt",
    "/proc", "/root", "/sbin", "/sys", "/tmp", "/usr", "/var",
}

DEFAULT_SHELL = "/bin/bash"


class SetupError(Exception):
    pass


class ValidationError(SetupError):
    pass


class ActionFailure(SetupError):
    def __init__(self, action, message):
        self.action = action
        self.message = message
        super().__init__("{}: {}".format(action, message))


class Interrupted(SetupError):
    pass


_interrupted = False


def _defer_signal(signum, frame):
    global _interrupted
    _interrupted = True


def _force_exit(signum, frame):
    sys.stderr.write("\nsecond interrupt: forcing exit, rollback may be incomplete\n")
    sys.exit(128 + signum)


def install_signal_handlers():
    signal.signal(signal.SIGINT, _defer_signal)
    signal.signal(signal.SIGTERM, _defer_signal)
    try:
        signal.signal(signal.SIGHUP, _defer_signal)
    except (AttributeError, ValueError):
        pass


def install_force_exit_handlers():
    signal.signal(signal.SIGINT, _force_exit)
    signal.signal(signal.SIGTERM, _force_exit)


def check_interrupt():
    if _interrupted:
        raise Interrupted("interrupt received")


class Logger:
    def __init__(self, verbose=False):
        self.verbose = verbose

    def info(self, msg):
        print(msg, flush=True)

    def detail(self, msg):
        if self.verbose:
            print("[detail] " + msg, flush=True)

    def error(self, msg):
        print("[error] " + msg, file=sys.stderr, flush=True)


def group_exists(name):
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def group_gid(name):
    return grp.getgrnam(name).gr_gid


def user_exists(name):
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def user_info(name):
    return pwd.getpwnam(name)


def user_groups(name):
    result = set()
    for g in grp.getgrall():
        if name in g.gr_mem:
            result.add(g.gr_name)
    return result


def resolve_uid(name):
    return pwd.getpwnam(name).pw_uid


def resolve_gid(name):
    return grp.getgrnam(name).gr_gid


class CommandResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self):
        return self.returncode == 0


def run(cmd, timeout=60, input_data=None, extra_env=None):
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return CommandResult(127, "", "failed to spawn {}: {}".format(cmd[0], exc))
    try:
        out, err = proc.communicate(input=input_data, timeout=timeout)
        return CommandResult(proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate(input=input_data)
        rc = proc.returncode if proc.returncode is not None else 1
        return CommandResult(rc, out, err + "\n[command timed out and was killed]")


def exec_cmd(cmd, log, what):
    res = run(cmd)
    if not res.ok:
        raise ActionFailure(what, "command '{}' exited {}:\n{}".format(" ".join(cmd), res.returncode, res.stderr.strip()))
    return res


def _fail(errors, msg):
    errors.append(msg)


def validate_manifest(manifest, log):
    errors = []

    for key in ("groups", "users", "directories", "sudoers"):
        if key in manifest and not isinstance(manifest[key], list):
            _fail(errors, "{}: must be a list".format(key))

    groups = manifest.get("groups", [])
    users = manifest.get("users", [])
    dirs = manifest.get("directories", [])
    sudoers = manifest.get("sudoers", [])

    seen_groups = {}
    for g in groups:
        name = g.get("name")
        state = g.get("state")
        if not isinstance(name, str) or not NAME_RE.match(name):
            _fail(errors, "group: invalid name {!r}".format(name))
            continue
        if name in seen_groups:
            _fail(errors, "group: duplicate name {}".format(name))
        seen_groups[name] = state
        if state not in ("present", "absent"):
            _fail(errors, "group {}: state must be 'present' or 'absent'".format(name))
        gid = g.get("gid")
        if gid is not None and (not isinstance(gid, int) or gid < 0 or gid > 65535):
            _fail(errors, "group {}: invalid gid {!r}".format(name, gid))

    seen_users = {}
    for u in users:
        name = u.get("name")
        state = u.get("state")
        if not isinstance(name, str) or not NAME_RE.match(name):
            _fail(errors, "user: invalid name {!r}".format(name))
            continue
        if name in seen_users:
            _fail(errors, "user: duplicate name {}".format(name))
        seen_users[name] = state
        if state not in ("present", "absent"):
            _fail(errors, "user {}: state must be 'present' or 'absent'".format(name))
        home = u.get("home")
        if home is not None and not (isinstance(home, str) and home.startswith("/")):
            _fail(errors, "user {}: home must be an absolute path".format(name))
        shell = u.get("shell")
        if shell is not None and not (isinstance(shell, str) and shell.startswith("/")):
            _fail(errors, "user {}: shell must be an absolute path".format(name))
        umask = u.get("umask")
        if umask is not None and not (isinstance(umask, str) and MODE_RE.match(umask)):
            _fail(errors, "user {}: umask must be a 4-digit octal string (e.g. 0022)".format(name))
        default_password = u.get("default_password")
        if default_password is not None and not (isinstance(default_password, str) and default_password):
            _fail(errors, "user {}: default_password must be a non-empty string".format(name))
        grd = u.get("gnome_remote_desktop")
        if grd is not None:
            if not isinstance(grd, dict):
                _fail(errors, "user {}: gnome_remote_desktop must be an object".format(name))
            else:
                grd_mode = grd.get("mode", "disabled")
                if grd_mode not in ("multi-user", "single-user", "disabled"):
                    _fail(errors, "user {}: gnome_remote_desktop.mode must be one of "
                                  "multi-user, single-user, disabled".format(name))
                sharing = grd.get("desktop_sharing", False)
                if not isinstance(sharing, bool):
                    _fail(errors, "user {}: gnome_remote_desktop.desktop_sharing must be a boolean".format(name))
        for grp_name in u.get("groups", []):
            if not isinstance(grp_name, str) or not NAME_RE.match(grp_name):
                _fail(errors, "user {}: invalid group {!r}".format(name, grp_name))
            elif grp_name not in seen_groups and not group_exists(grp_name):
                _fail(errors, "user {}: group {} is neither declared nor an existing system group".format(name, grp_name))
            elif seen_groups.get(grp_name) == "absent":
                _fail(errors, "user {}: references group {} which is declared absent".format(name, grp_name))

    user_groups_map = {}
    for u in users:
        if u.get("name"):
            user_groups_map[u["name"]] = set(u.get("groups", []))

    for g_name, g_state in seen_groups.items():
        if g_state != "absent":
            continue
        for u_name, u_state in seen_users.items():
            if u_state != "present":
                continue
            if u_name == g_name:
                _fail(errors, "group {}: declared absent but is the primary group of user {}".format(g_name, u_name))
            if g_name in user_groups_map.get(u_name, set()):
                _fail(errors, "group {}: declared absent but referenced by user {}".format(g_name, u_name))

    seen_dirs = {}
    for d in dirs:
        path = d.get("path")
        state = d.get("state")
        if not isinstance(path, str) or not path.startswith("/") or path == "/":
            _fail(errors, "directory: invalid path {!r}".format(path))
            continue
        if path in seen_dirs:
            _fail(errors, "directory: duplicate path {}".format(path))
        seen_dirs[path] = state
        if state not in ("present", "absent"):
            _fail(errors, "directory {}: state must be 'present' or 'absent'".format(path))
            continue
        if state == "absent":
            continue
        mode = d.get("mode")
        if not isinstance(mode, str) or not MODE_RE.match(mode):
            _fail(errors, "directory {}: mode must be a 4-digit octal string (e.g. 2750)".format(path))
        owner = d.get("owner")
        if owner is not None and not (isinstance(owner, str) and (user_exists(owner) or owner in seen_users)):
            _fail(errors, "directory {}: owner {!r} is neither a declared user nor an existing user".format(path, owner))
        group = d.get("group")
        if group is not None and not (isinstance(group, str) and (group_exists(group) or group in seen_groups)):
            _fail(errors, "directory {}: group {!r} is neither a declared group nor an existing group".format(path, group))

    grd_users = [(u.get("name"), u.get("gnome_remote_desktop", {})) for u in users
                 if u.get("state") == "present" and isinstance(u.get("gnome_remote_desktop"), dict)]
    grd_modes = [g.get("mode", "disabled") for _, g in grd_users]
    has_multi = "multi-user" in grd_modes
    has_single = "single-user" in grd_modes
    if has_multi and has_single:
        _fail(errors, "gnome_remote_desktop: multi-user and single-user cannot be mixed across users — "
                      "the system remote-login service and per-user headless sessions share RDP port 3389")

    system = manifest.get("gnome_remote_desktop_system")
    if system is not None and not isinstance(system, dict):
        _fail(errors, "gnome_remote_desktop_system must be an object")
    elif system is not None:
        tls = system.get("tls", {})
        if not isinstance(tls, dict):
            _fail(errors, "gnome_remote_desktop_system.tls must be an object")
        elif not tls.get("generate") and not (tls.get("cert") and tls.get("key")):
            _fail(errors, "gnome_remote_desktop_system.tls must set generate: true or both cert and key")
        creds = system.get("credentials")
        if creds is not None:
            if not isinstance(creds, dict) or not (creds.get("username") and creds.get("password")):
                _fail(errors, "gnome_remote_desktop_system.credentials must set username and password")
    if has_multi:
        if system is None:
            _fail(errors, "gnome_remote_desktop_system is required when a user has mode multi-user")
        elif not isinstance(system.get("credentials"), dict):
            _fail(errors, "gnome_remote_desktop_system.credentials is required in multi-user mode")

    tfc_env = manifest.get("tfc_paths_env")
    if tfc_env is not None:
        if not isinstance(tfc_env, dict):
            _fail(errors, "tfc_paths_env must be an object")
        else:
            template = tfc_env.get("template")
            dest = tfc_env.get("dest")
            if not (isinstance(template, str) and template.startswith("/")):
                _fail(errors, "tfc_paths_env.template must be an absolute path")
            if not (isinstance(dest, str) and dest.startswith("/")):
                _fail(errors, "tfc_paths_env.dest must be an absolute path")
            if not (isinstance(tfc_env.get("mode"), str) and MODE_RE.match(tfc_env.get("mode", ""))):
                _fail(errors, "tfc_paths_env.mode must be a 4-digit octal string (e.g. 0644)")
            for key in ("owner", "group"):
                val = tfc_env.get(key)
                if val is not None and not (isinstance(val, str) and val):
                    _fail(errors, "tfc_paths_env.{} must be a non-empty string".format(key))
            subs = tfc_env.get("substitutions", {})
            if not isinstance(subs, dict):
                _fail(errors, "tfc_paths_env.substitutions must be an object of key -> value strings")
            else:
                for k, v in subs.items():
                    if not isinstance(k, str) or not isinstance(v, str):
                        _fail(errors, "tfc_paths_env.substitutions: keys and values must be strings")

    for rule in sudoers:
        if not isinstance(rule, str) or not rule.strip():
            _fail(errors, "sudoers: rules must be non-empty strings")

    present_dirs = [d["path"] for d in dirs if d.get("state") == "present"]
    for d in dirs:
        if d.get("state") != "absent":
            continue
        parent = d["path"].rstrip("/") + "/"
        for child in present_dirs:
            if child.startswith(parent):
                _fail(errors, "directory {}: declared absent but {} is declared present below it — "
                              "removing the parent deletes the child. Mark the child absent too or drop it."
                              .format(d["path"], child))

    if errors:
        for e in errors:
            log.error("validation: " + e)
        raise ValidationError("manifest is invalid ({} problem(s))".format(len(errors)))


def load_manifest(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError("cannot load manifest {}: {}".format(path, exc))
    if not isinstance(data, dict):
        raise SetupError("manifest must be a JSON object")
    return data


def resolve_manifest(manifest):
    """Resolve ${name} path references against directories[].name. Mutates in place.

    A directory entry may carry an optional "name"; any string value anywhere in the
    manifest may reference it as ${name}, composed inline (e.g. "${tfc_root}/repositories").
    References may chain to other references. Unknown names, duplicate names and cycles
    are reported as errors. Returns a list of error strings (empty when valid).
    """
    errors = []
    names = {}
    for d in manifest.get("directories", []):
        if not isinstance(d, dict):
            continue
        name = d.get("name")
        if name is None:
            continue
        if not isinstance(name, str) or not DIR_NAME_RE.match(name):
            _fail(errors, "directory {}: invalid path name {!r} (expected [A-Za-z_][A-Za-z0-9_-]*)".format(
                d.get("path", "?"), name))
            continue
        if name in names:
            _fail(errors, "directory: duplicate path name {!r}".format(name))
            continue
        names[name] = d.get("path")

    def resolve_string(value, loc):
        if REF_RE.search(value) is None:
            return value
        seen = set()
        cur = value
        for _ in range(len(names) + 1):
            m = REF_RE.search(cur)
            if m is None:
                return cur
            name = m.group(1)
            if name not in names:
                _fail(errors, "{}: unknown path reference ${{{}}}".format(loc, name))
                return cur
            if name in seen:
                _fail(errors, "{}: path reference cycle involving ${{{}}}".format(loc, name))
                return cur
            seen.add(name)
            cur = cur[:m.start()] + names[name] + cur[m.end():]
        _fail(errors, "{}: too many nested path references in {!r}".format(loc, value))
        return cur

    def walk(node, loc):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if isinstance(k, str) and k.startswith("_"):
                    continue
                node[k] = walk(v, "{}.{}".format(loc, k))
            return node
        if isinstance(node, list):
            for i, v in enumerate(node):
                node[i] = walk(v, "{}[{}]".format(loc, i))
            return node
        if isinstance(node, str):
            return resolve_string(node, loc)
        return node

    walk(manifest, "manifest")
    return errors


def group_ops(g, log):
    name = g["name"]
    if g["state"] == "present":
        if not group_exists(name):
            op = {"kind": "create_group", "name": name}
            if "gid" in g:
                op["gid"] = g["gid"]
            return [op]
        if "gid" in g and group_gid(name) != g["gid"]:
            return [{"kind": "modify_group_gid", "name": name, "old_gid": group_gid(name), "gid": g["gid"]}]
        return []
    if group_exists(name):
        return [{"kind": "delete_group", "name": name}]
    return []


def user_ops(u, log):
    name = u["name"]
    if u["state"] == "present":
        desired = set(u.get("groups", []))
        shell = u.get("shell", DEFAULT_SHELL)
        home = u.get("home", os.path.join("/home", name))
        if not user_exists(name):
            supp = desired - {name}
            op = {"kind": "create_user", "name": name, "groups": sorted(supp), "shell": shell, "home": home}
            if u.get("default_password"):
                op["default_password"] = u["default_password"]
            return [op]
        primary = grp.getgrgid(user_info(name).pw_gid).gr_name
        supp = desired - {primary}
        ops = []
        cur = user_groups(name)
        if cur != supp:
            ops.append({"kind": "set_user_groups", "name": name, "old_groups": sorted(cur), "groups": sorted(supp)})
        if user_info(name).pw_shell != shell:
            ops.append({"kind": "set_user_shell", "name": name, "old_shell": user_info(name).pw_shell, "shell": shell})
        if home and not os.path.isdir(home):
            ops.append({"kind": "ensure_user_home", "name": name, "home": home})
        return ops
    if user_exists(name):
        return [{"kind": "delete_user", "name": name, "remove_home": u.get("remove_home", False)}]
    return []


def dir_ops(d, force, log):
    path = d["path"]
    if d["state"] == "present":
        mode = int(d["mode"], 8)
        owner = d["owner"]
        group = d["group"]
        if not os.path.lexists(path):
            return [{"kind": "create_dir", "path": path, "owner": owner, "group": group, "mode": mode}]
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise ValidationError("path exists but is not a directory: {}".format(path))
        ops = []
        cur_owner = pwd.getpwuid(st.st_uid).pw_name
        cur_group = grp.getgrgid(st.st_gid).gr_name
        cur_mode = stat.S_IMODE(st.st_mode)
        if cur_owner != owner or cur_group != group:
            ops.append({"kind": "chown_dir", "path": path, "old_owner": cur_owner, "old_group": cur_group,
                        "owner": owner, "group": group})
        if cur_mode != mode:
            ops.append({"kind": "chmod_dir", "path": path, "old_mode": cur_mode, "mode": mode})
        return ops
    if os.path.lexists(path):
        if path in PROTECTED_PATHS and not force:
            raise ValidationError("refusing to remove protected path {} (use --force to override)".format(path))
        return [{"kind": "delete_dir", "path": path, "children": enumerate_tree(path)}]
    return []


def enumerate_tree(path):
    """All entries under path (files, dirs, symlinks), for the delete plan display."""
    entries = []
    for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
        for name in dirs + files:
            entries.append(os.path.join(root, name))
    return sorted(entries)


def users_with_umask(users):
    """Present users that declare a umask, in manifest order."""
    return [(u["name"], u["umask"]) for u in users
            if u.get("state") == "present" and u.get("umask")]


def build_sudoers_content(manifest):
    """Generated sudoers = auto 'Defaults>user umask=...' lines + the manifest rules."""
    defaults = ["Defaults>{} umask={}".format(name, umask)
                for name, umask in users_with_umask(manifest.get("users", []))]
    return "".join(line + "\n" for line in defaults + list(manifest.get("sudoers", [])))


def sudoers_ops(manifest, log):
    desired = build_sudoers_content(manifest)
    current = None
    if os.path.exists(SUDOERS_PATH):
        with open(SUDOERS_PATH) as f:
            current = f.read()
    if current == desired:
        return []
    if current is None and desired == "":
        return []
    return [{"kind": "write_sudoers", "old_content": current, "content": desired}]


def write_file_atomic(path, content, mode, log):
    tmp = path + ".new"
    with open(tmp, "w") as f:
        f.write(content)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    os.chmod(path, mode)


def umask_profile_ops(manifest, log):
    """Login-shell umask per user. Fully generated from the manifest."""
    umasks = users_with_umask(manifest.get("users", []))
    content = ""
    if umasks:
        lines = [
            "# tfc-autonomous per-user umask - generated by setup_jetson.py, do not edit",
        ]
        for name, umask in umasks:
            lines.append('if [ "$(id -un)" = "{}" ]; then umask {}; fi'.format(name, umask))
        content = "\n".join(lines) + "\n"
    current = None
    if os.path.exists(UMASK_PROFILE_PATH):
        with open(UMASK_PROFILE_PATH) as f:
            current = f.read()
    if current == content:
        return []
    if content == "":
        return [{"kind": "delete_umask_profile", "old_content": current}]
    return [{"kind": "write_umask_profile", "old_content": current, "content": content}]


def tfc_paths_env_ops(manifest, log):
    """Generate /opt config from the committed env template (tfc_paths_env)."""
    cfg = manifest.get("tfc_paths_env")
    if not cfg:
        return []
    template = cfg["template"]
    dest = cfg["dest"]
    mode = int(cfg["mode"], 8)
    if not os.path.exists(template):
        raise ValidationError("tfc_paths_env template not found: {} — clone the repo or fix the path".format(template))
    with open(template) as f:
        content = f.read()
    for key, val in cfg.get("substitutions", {}).items():
        content = content.replace("@" + key + "@", val)
    current = None
    if os.path.exists(dest):
        with open(dest) as f:
            current = f.read()
    if current == content:
        return []
    return [{"kind": "write_tfc_paths_env", "old_content": current, "content": content,
             "dest": dest, "owner": cfg.get("owner"), "group": cfg.get("group"), "mode": mode}]


def grd_enabled(manifest):
    if manifest.get("gnome_remote_desktop_system"):
        return True
    for u in manifest.get("users", []):
        if u.get("state") == "present" and u.get("gnome_remote_desktop"):
            return True
    return False


def _user_cmd(user, args):
    """Run args as `user` with the user's XDG runtime / DBUS session env (for --user services)."""
    uid = resolve_uid(user)
    env_args = ["XDG_RUNTIME_DIR=/run/user/{}".format(uid),
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{}/bus".format(uid)]
    return ["sudo", "-u", user, "-H", "env"] + env_args + args


def _grd_cmd(mode, user, args):
    """grdctl invocation for mode system | headless | sharing."""
    if mode == "system":
        return ["grdctl", "--system"] + args
    grd = ["grdctl"] + (["--headless"] if mode == "headless" else []) + args
    return _user_cmd(user, grd)


def _grd_tls_dir(scope, user):
    if scope == "system":
        if not user_exists("gnome-remote-desktop"):
            raise ValidationError(
                "gnome_remote_desktop system TLS generation needs the 'gnome-remote-desktop' system user. "
                "Install the GNOME desktop / gnome-remote-desktop first (see jetson-setup/README.md).")
        home = pwd.getpwnam("gnome-remote-desktop").pw_dir
    else:
        home = pwd.getpwnam(user).pw_dir
    return os.path.join(home, ".local", "share", "gnome-remote-desktop")


def _ensure_user_runtime(user):
    uid = resolve_uid(user)
    rt = "/run/user/{}".format(uid)
    if os.path.isdir(rt):
        return
    run(["systemctl", "start", "user@{}.service".format(uid)])
    if not os.path.isdir(rt):
        os.makedirs(rt, mode=0o700, exist_ok=True)
        run(["chown", "{}:{}".format(user, uid), rt])


def grd_ops(manifest, log):
    """Plan GNOME Remote Desktop operations: system remote-login, per-user headless, desktop sharing."""
    if not grd_enabled(manifest):
        return []
    if not shutil.which("grdctl"):
        raise ValidationError(
            "gnome_remote_desktop is configured but grdctl is not installed. Install the GNOME desktop "
            "and gnome-remote-desktop first (see jetson-setup/README.md), then re-run.")

    ops = []
    system_cfg = manifest.get("gnome_remote_desktop_system")
    grd_users = [(u["name"], u.get("gnome_remote_desktop", {})) for u in manifest.get("users", [])
                 if u.get("state") == "present" and u.get("gnome_remote_desktop")]
    homes = {u["name"]: u.get("home", os.path.join("/home", u["name"])) for u in manifest.get("users", [])
             if u.get("state") == "present"}
    has_multi = "multi-user" in [g.get("mode", "disabled") for _, g in grd_users]

    if has_multi:
        tls = system_cfg.get("tls", {})
        if tls.get("generate"):
            if not shutil.which("winpr-makecert"):
                raise ValidationError(
                    "gnome_remote_desktop TLS generation needs winpr-utils (winpr-makecert). "
                    "Install it, or set tls: {cert: ..., key: ...} with existing certificates.")
            tls_dir = _grd_tls_dir("system", None)
            cert = os.path.join(tls_dir, "rdp-tls.crt")
            key = os.path.join(tls_dir, "rdp-tls.key")
            if not (os.path.exists(cert) and os.path.exists(key)):
                ops.append({"kind": "gen_rdp_tls", "scope": "system", "user": "gnome-remote-desktop",
                            "tls_dir": tls_dir})
        else:
            cert, key = tls["cert"], tls["key"]
        ops.append({"kind": "set_rdp_tls", "mode": "system", "user": None, "cert": cert, "key": key})
        creds = system_cfg["credentials"]
        ops.append({"kind": "set_rdp_credentials", "mode": "system", "user": None,
                    "username": creds["username"], "password": creds["password"]})
        ops.append({"kind": "enable_rdp", "mode": "system", "user": None})
        ops.append({"kind": "enable_rdp_service", "mode": "system", "user": None,
                    "service": "gnome-remote-desktop.service", "enable_linger": False})
        ops.append({"kind": "enable_gdm"})

    for name, cfg in grd_users:
        mode = cfg.get("mode", "disabled")
        sharing = cfg.get("desktop_sharing", False)
        if mode == "single-user":
            grd_mode = "headless"
            service = "gnome-remote-desktop-headless.service"
        elif sharing:
            grd_mode = "sharing"
            service = "gnome-remote-desktop.service"
        else:
            continue
        tls_dir = os.path.join(homes[name], ".local", "share", "gnome-remote-desktop")
        cert = os.path.join(tls_dir, "rdp-tls.crt")
        key = os.path.join(tls_dir, "rdp-tls.key")
        if not (os.path.exists(cert) and os.path.exists(key)):
            if not shutil.which("winpr-makecert"):
                raise ValidationError(
                    "user {}: per-user RDP TLS generation needs winpr-utils (winpr-makecert). "
                    "Install it, or configure the member's RDP credentials manually.".format(name))
            ops.append({"kind": "gen_rdp_tls", "scope": "user", "user": name, "tls_dir": tls_dir})
        ops.append({"kind": "set_rdp_tls", "mode": grd_mode, "user": name, "cert": cert, "key": key})
        ops.append({"kind": "enable_rdp", "mode": grd_mode, "user": name})
        ops.append({"kind": "enable_rdp_service", "mode": grd_mode, "user": name,
                    "service": service, "enable_linger": grd_mode == "headless"})
    return ops


def build_plan(manifest, force, log):
    present = []
    removals = []

    for g in manifest.get("groups", []):
        for op in group_ops(g, log):
            (present if not is_destructive(op) else removals).append(op)
    for u in manifest.get("users", []):
        for op in user_ops(u, log):
            (present if not is_destructive(op) else removals).append(op)
    for d in manifest.get("directories", []):
        for op in dir_ops(d, force, log):
            (present if not is_destructive(op) else removals).append(op)
    for op in sudoers_ops(manifest, log):
        (present if not is_destructive(op) else removals).append(op)
    for op in umask_profile_ops(manifest, log):
        (present if not is_destructive(op) else removals).append(op)
    for op in tfc_paths_env_ops(manifest, log):
        (present if not is_destructive(op) else removals).append(op)
    for op in grd_ops(manifest, log):
        (present if not is_destructive(op) else removals).append(op)

    removal_order = {"delete_dir": 0, "delete_user": 1, "delete_group": 2,
                     "delete_umask_profile": 3}
    removals.sort(key=lambda op: removal_order.get(op["kind"], 3))
    return present + removals


def describe(op):
    kind = op["kind"]
    if kind == "create_group":
        return "create group {}".format(op["name"])
    if kind == "modify_group_gid":
        return "set gid of group {} to {}".format(op["name"], op["gid"])
    if kind == "delete_group":
        return "delete group {}".format(op["name"])
    if kind == "create_user":
        pw = " (password set)" if "default_password" in op else ""
        return "create user {} (groups: {}, shell: {}){}".format(op["name"], ",".join(op["groups"]) or "none", op["shell"], pw)
    if kind == "set_user_groups":
        return "set groups of user {} to: {}".format(op["name"], ",".join(op["groups"]) or "none")
    if kind == "set_user_shell":
        return "set shell of user {} to {}".format(op["name"], op["shell"])
    if kind == "ensure_user_home":
        return "create missing home {} for user {}".format(op["home"], op["name"])
    if kind == "create_dir":
        return "create directory {} (owner {}:{}, mode {})".format(op["path"], op["owner"], op["group"], oct(op["mode"])[2:])
    if kind == "chown_dir":
        return "chown {} -> {}:{}".format(op["path"], op["owner"], op["group"])
    if kind == "chmod_dir":
        return "chmod {} -> {}".format(op["path"], oct(op["mode"])[2:])
    if kind == "delete_dir":
        return "remove directory {}".format(op["path"])
    if kind == "delete_user":
        return "delete user {}".format(op["name"])
    if kind == "write_sudoers":
        if not op["content"]:
            return "remove " + SUDOERS_PATH
        return "write {} ({} rules)".format(SUDOERS_PATH, op["content"].count("\n"))
    if kind == "write_umask_profile":
        return "write {} (per-user umask)".format(UMASK_PROFILE_PATH)
    if kind == "delete_umask_profile":
        return "remove {}".format(UMASK_PROFILE_PATH)
    if kind == "write_tfc_paths_env":
        return "write {} (from tfc_paths_env template)".format(op["dest"])
    if kind == "gen_rdp_tls":
        who = "system" if op.get("scope") == "system" else "user " + op.get("user", "?")
        return "generate RDP TLS certificate ({})".format(who)
    if kind == "set_rdp_tls":
        return "set RDP TLS certificate/key ({})".format(op.get("mode", "?"))
    if kind == "set_rdp_credentials":
        who = "system" if op.get("mode") == "system" else "user " + op.get("user", "?")
        return "set RDP credentials ({})".format(who)
    if kind == "enable_rdp":
        return "enable RDP backend ({})".format(op.get("mode", "?"))
    if kind == "enable_rdp_service":
        who = "system" if op.get("mode") == "system" else "user " + op.get("user", "?")
        return "enable {} ({})".format(op.get("service", "gnome-remote-desktop.service"), who)
    if kind == "enable_gdm":
        return "enable GDM (remote login service)"
    return kind


def describe_children(op):
    """Extra lines showing what a destructive op removes, e.g. a directory's contents."""
    if op.get("kind") == "delete_dir":
        return ["    also removing: " + p for p in op.get("children", [])]
    return []


def is_destructive(op):
    kind = op["kind"]
    if kind in ("delete_group", "delete_user", "delete_dir", "delete_umask_profile"):
        return True
    if kind == "write_sudoers" and not op["content"]:
        return True
    return False


def create_snapshot(log):
    os.makedirs(WORK_DIR, mode=0o700, exist_ok=True)
    snapshot_dir = os.path.join(WORK_DIR, "snapshot")
    if os.path.isdir(snapshot_dir):
        shutil.rmtree(snapshot_dir)
    os.makedirs(snapshot_dir, mode=0o700)
    for name in ACCOUNT_FILES:
        src = os.path.join("/etc", name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(snapshot_dir, name))
    log.detail("account database snapshot: " + snapshot_dir)
    return snapshot_dir


def _journal_write(header_line):
    fd = os.open(JOURNAL_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(header_line)


def _journal_append(line):
    fd = os.open(JOURNAL_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(line + "\n")


def clear_journal():
    try:
        os.remove(JOURNAL_PATH)
    except OSError:
        pass


def build_undo(op, snapshot_dir, index):
    kind = op["kind"]
    if kind == "create_group":
        return {"kind": "create_group", "name": op["name"]}
    if kind == "modify_group_gid":
        return {"kind": "modify_group_gid", "name": op["name"], "old_gid": op["old_gid"]}
    if kind == "create_user":
        return {"kind": "create_user", "name": op["name"], "home": op["home"],
                "home_existed": os.path.isdir(op["home"])}
    if kind == "set_user_groups":
        return {"kind": "set_user_groups", "name": op["name"], "old_groups": op["old_groups"]}
    if kind == "set_user_shell":
        return {"kind": "set_user_shell", "name": op["name"], "old_shell": op["old_shell"]}
    if kind == "create_dir":
        return {"kind": "create_dir", "path": op["path"]}
    if kind == "chown_dir":
        return {"kind": "chown_dir", "path": op["path"], "old_owner": op["old_owner"], "old_group": op["old_group"]}
    if kind == "chmod_dir":
        return {"kind": "chmod_dir", "path": op["path"], "old_mode": op["old_mode"]}
    if kind == "delete_dir":
        path = op["path"]
        if os.path.islink(path):
            return {"kind": "delete_dir", "path": path, "was_link": True, "link_target": os.readlink(path), "backup": None}
        backup = os.path.join(snapshot_dir, "removed_{}".format(index))
        return {"kind": "delete_dir", "path": path, "was_link": False, "backup": backup}
    if kind == "delete_user":
        return {"kind": "delete_user", "name": op["name"], "remove_home": op.get("remove_home", False)}
    if kind == "delete_group":
        return {"kind": "delete_group", "name": op["name"]}
    if kind == "write_sudoers":
        return {"kind": "write_sudoers", "old_content": op["old_content"]}
    if kind in ("write_umask_profile", "delete_umask_profile"):
        return {"kind": "write_umask_profile", "old_content": op["old_content"]}
    if kind == "write_tfc_paths_env":
        return {"kind": "write_tfc_paths_env", "old_content": op["old_content"], "dest": op["dest"],
                "owner": op.get("owner"), "group": op.get("group"), "mode": op["mode"]}
    if kind == "gen_rdp_tls":
        return {"kind": "noop"}
    if kind == "set_rdp_tls":
        return {"kind": "noop"}
    if kind == "set_rdp_credentials":
        return {"kind": "set_rdp_credentials", "mode": op["mode"], "user": op.get("user"), "clear": True}
    if kind == "enable_rdp":
        return {"kind": "enable_rdp", "mode": op["mode"], "user": op.get("user"), "disable": True}
    if kind == "enable_rdp_service":
        return {"kind": "enable_rdp_service", "mode": op["mode"], "user": op.get("user"),
                "service": op.get("service", "gnome-remote-desktop.service"),
                "enable_linger": op.get("enable_linger", False), "disable": True}
    if kind == "enable_gdm":
        return {"kind": "enable_gdm", "disable": True}
    raise SetupError("no undo defined for op kind {}".format(kind))


def execute_op(op, undo, log):
    kind = op["kind"]
    if kind == "create_group":
        cmd = ["groupadd"]
        if "gid" in op:
            cmd += ["-g", str(op["gid"])]
        cmd.append(op["name"])
        exec_cmd(cmd, log, "create group " + op["name"])
    elif kind == "modify_group_gid":
        exec_cmd(["groupmod", "-g", str(op["gid"]), op["name"]], log, "set gid of group " + op["name"])
    elif kind == "delete_group":
        if not group_exists(op["name"]):
            return
        exec_cmd(["groupdel", op["name"]], log, "delete group " + op["name"])
    elif kind == "create_user":
        cmd = ["useradd", "-m", "-s", op["shell"]]
        if op["groups"]:
            cmd += ["-G", ",".join(op["groups"])]
        cmd.append(op["name"])
        exec_cmd(cmd, log, "create user " + op["name"])
        if "default_password" in op:
            res = run(["chpasswd"], input_data="{}:{}".format(op["name"], op["default_password"]))
            if not res.ok:
                raise ActionFailure("create_user", "chpasswd failed for {}:\n{}".format(op["name"], res.stderr.strip()))
    elif kind == "set_user_groups":
        cmd = ["usermod", "-G", ",".join(op["groups"]) if op["groups"] else "", op["name"]]
        exec_cmd(cmd, log, "set groups of user " + op["name"])
    elif kind == "set_user_shell":
        exec_cmd(["usermod", "-s", op["shell"], op["name"]], log, "set shell of user " + op["name"])
    elif kind == "ensure_user_home":
        os.makedirs(op["home"], exist_ok=True)
        exec_cmd(["chown", op["name"], op["home"]], log, "ensure home of user " + op["name"])
    elif kind == "create_dir":
        os.makedirs(op["path"], exist_ok=True)
        exec_cmd(["chown", "{}:{}".format(op["owner"], op["group"]), op["path"]], log, "set owner of " + op["path"])
        exec_cmd(["chmod", oct(op["mode"])[2:], op["path"]], log, "set mode of " + op["path"])
    elif kind == "chown_dir":
        exec_cmd(["chown", "{}:{}".format(op["owner"], op["group"]), op["path"]], log, "set owner of " + op["path"])
    elif kind == "chmod_dir":
        exec_cmd(["chmod", oct(op["mode"])[2:], op["path"]], log, "set mode of " + op["path"])
    elif kind == "delete_dir":
        path = op["path"]
        if not os.path.lexists(path):
            return
        if os.path.islink(path):
            os.unlink(path)
        else:
            backup = undo["backup"]
            if os.path.isdir(backup):
                shutil.rmtree(backup)
            shutil.copytree(path, backup)
            shutil.rmtree(path)
    elif kind == "delete_user":
        if not user_exists(op["name"]):
            return
        cmd = ["userdel"]
        if op.get("remove_home"):
            cmd.append("-r")
        cmd.append(op["name"])
        exec_cmd(cmd, log, "delete user " + op["name"])
    elif kind == "write_sudoers":
        if not op["content"]:
            if os.path.exists(SUDOERS_PATH):
                os.remove(SUDOERS_PATH)
            return
        tmp = SUDOERS_PATH + ".new"
        with open(tmp, "w") as f:
            f.write(op["content"])
        os.chmod(tmp, 0o440)
        res = run(["visudo", "-cf", tmp])
        if not res.ok:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ActionFailure("write_sudoers", "visudo rejected the sudoers file:\n" + res.stderr.strip())
        os.rename(tmp, SUDOERS_PATH)
        os.chmod(SUDOERS_PATH, 0o440)
    elif kind == "write_umask_profile":
        write_file_atomic(UMASK_PROFILE_PATH, op["content"], 0o644, log)
    elif kind == "delete_umask_profile":
        if os.path.exists(UMASK_PROFILE_PATH):
            os.remove(UMASK_PROFILE_PATH)
    elif kind == "write_tfc_paths_env":
        write_file_atomic(op["dest"], op["content"], op["mode"], log)
        if op.get("owner") and op.get("group"):
            exec_cmd(["chown", "{}:{}".format(op["owner"], op["group"]), op["dest"]], log, "set owner of " + op["dest"])
    elif kind == "gen_rdp_tls":
        run_as = op["user"]
        tls_dir = op["tls_dir"]
        os.makedirs(tls_dir, exist_ok=True)
        exec_cmd(["chown", run_as, tls_dir], log, "set owner of " + tls_dir)
        res = run(["sudo", "-u", run_as, "-H", "winpr-makecert", "-silent", "-rdp", "-path", tls_dir, "rdp-tls"],
                  timeout=120)
        if not res.ok:
            raise ActionFailure("gen_rdp_tls", "winpr-makecert failed:\n" + res.stderr.strip())
    elif kind == "set_rdp_tls":
        exec_cmd(_grd_cmd(op["mode"], op["user"], ["rdp", "set-tls-key", op["key"]]), log, "set RDP TLS key")
        exec_cmd(_grd_cmd(op["mode"], op["user"], ["rdp", "set-tls-cert", op["cert"]]), log, "set RDP TLS certificate")
    elif kind == "set_rdp_credentials":
        exec_cmd(_grd_cmd(op["mode"], op["user"], ["rdp", "set-credentials", op["username"], op["password"]]),
                 log, "set RDP credentials")
    elif kind == "enable_rdp":
        exec_cmd(_grd_cmd(op["mode"], op["user"], ["rdp", "enable"]), log, "enable RDP")
        exec_cmd(_grd_cmd(op["mode"], op["user"], ["rdp", "disable-view-only"]), log, "disable RDP view-only")
    elif kind == "enable_rdp_service":
        if op.get("mode") == "system":
            exec_cmd(["systemctl", "enable", "--now", op["service"]], log, "enable " + op["service"])
        else:
            if op.get("enable_linger"):
                run(["loginctl", "enable-linger", op["user"]])
            _ensure_user_runtime(op["user"])
            exec_cmd(_user_cmd(op["user"], ["systemctl", "--user", "enable", "--now", op["service"]]),
                     log, "enable " + op["service"])
    elif kind == "enable_gdm":
        exec_cmd(["systemctl", "enable", "--now", "gdm.service"], log, "enable gdm.service")
    else:
        raise SetupError("unknown op kind " + str(kind))


def verify_op(op, log):
    kind = op["kind"]
    if kind in ("create_group", "modify_group_gid"):
        if not group_exists(op["name"]):
            raise ActionFailure(kind, "group {} does not exist after apply".format(op["name"]))
        if kind == "modify_group_gid" and group_gid(op["name"]) != op["gid"]:
            raise ActionFailure(kind, "gid of {} is {}, expected {}".format(op["name"], group_gid(op["name"]), op["gid"]))
    elif kind == "delete_group":
        if group_exists(op["name"]):
            raise ActionFailure(kind, "group {} still exists".format(op["name"]))
    elif kind == "create_user":
        if not user_exists(op["name"]):
            raise ActionFailure(kind, "user {} does not exist after apply".format(op["name"]))
        if not user_groups(op["name"]) == set(op["groups"]):
            raise ActionFailure(kind, "memberships of {} are {}, expected {}".format(
                op["name"], sorted(user_groups(op["name"])), op["groups"]))
        if not os.path.isdir(op["home"]):
            raise ActionFailure(kind, "home {} of {} is missing".format(op["home"], op["name"]))
    elif kind == "set_user_groups":
        if not user_groups(op["name"]) == set(op["groups"]):
            raise ActionFailure(kind, "memberships of {} are {}, expected {}".format(
                op["name"], sorted(user_groups(op["name"])), op["groups"]))
    elif kind == "set_user_shell":
        if user_info(op["name"]).pw_shell != op["shell"]:
            raise ActionFailure(kind, "shell of {} is {}, expected {}".format(
                op["name"], user_info(op["name"]).pw_shell, op["shell"]))
    elif kind == "ensure_user_home":
        if not os.path.isdir(op["home"]):
            raise ActionFailure(kind, "home {} missing after apply".format(op["home"]))
    elif kind in ("create_dir", "chown_dir", "chmod_dir"):
        path = op["path"]
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise ActionFailure(kind, "{} is not a directory after apply".format(path))
        cur_owner = pwd.getpwuid(st.st_uid).pw_name
        cur_group = grp.getgrgid(st.st_gid).gr_name
        cur_mode = stat.S_IMODE(st.st_mode)
        if op.get("owner") is not None and cur_owner != op["owner"]:
            raise ActionFailure(kind, "owner of {} is {}, expected {}".format(path, cur_owner, op["owner"]))
        if op.get("group") is not None and cur_group != op["group"]:
            raise ActionFailure(kind, "group of {} is {}, expected {}".format(path, cur_group, op["group"]))
        if op.get("mode") is not None and cur_mode != op["mode"]:
            raise ActionFailure(kind, "mode of {} is {}, expected {}".format(path, oct(cur_mode), oct(op["mode"])))
    elif kind == "delete_dir":
        if os.path.lexists(op["path"]):
            raise ActionFailure(kind, "{} still exists".format(op["path"]))
    elif kind == "delete_user":
        if user_exists(op["name"]):
            raise ActionFailure(kind, "user {} still exists".format(op["name"]))
    elif kind == "write_sudoers":
        if not op["content"]:
            if os.path.exists(SUDOERS_PATH):
                raise ActionFailure(kind, "sudoers file still present")
            return
        with open(SUDOERS_PATH) as f:
            if f.read() != op["content"]:
                raise ActionFailure(kind, "sudoers content mismatch after apply")
    elif kind == "write_umask_profile":
        with open(UMASK_PROFILE_PATH) as f:
            if f.read() != op["content"]:
                raise ActionFailure(kind, "umask profile content mismatch after apply")
    elif kind == "delete_umask_profile":
        if os.path.exists(UMASK_PROFILE_PATH):
            raise ActionFailure(kind, "umask profile still present")
    elif kind == "write_tfc_paths_env":
        if not os.path.exists(op["dest"]):
            raise ActionFailure(kind, "{} missing after apply".format(op["dest"]))
        with open(op["dest"]) as f:
            if f.read() != op["content"]:
                raise ActionFailure(kind, "content of {} differs after apply".format(op["dest"]))
        st = os.lstat(op["dest"])
        if op.get("owner") and pwd.getpwuid(st.st_uid).pw_name != op["owner"]:
            raise ActionFailure(kind, "owner of {} is {}, expected {}".format(
                op["dest"], pwd.getpwuid(st.st_uid).pw_name, op["owner"]))
        if op.get("group") and grp.getgrgid(st.st_gid).gr_name != op["group"]:
            raise ActionFailure(kind, "group of {} is {}, expected {}".format(
                op["dest"], grp.getgrgid(st.st_gid).gr_name, op["group"]))
        if stat.S_IMODE(st.st_mode) != op["mode"]:
            raise ActionFailure(kind, "mode of {} is {}, expected {}".format(
                op["dest"], oct(stat.S_IMODE(st.st_mode)), oct(op["mode"])))
    elif kind == "gen_rdp_tls":
        for suffix in ("rdp-tls.crt", "rdp-tls.key"):
            if not os.path.exists(os.path.join(op["tls_dir"], suffix)):
                raise ActionFailure(kind, "{} missing after apply".format(os.path.join(op["tls_dir"], suffix)))
    elif kind in ("set_rdp_tls", "set_rdp_credentials"):
        res = run(_grd_cmd(op["mode"], op["user"], ["status"]))
        if not res.ok:
            raise ActionFailure(kind, "grdctl status failed:\n" + res.stderr.strip())
    elif kind == "enable_rdp":
        res = run(_grd_cmd(op["mode"], op["user"], ["status"]))
        if not (res.ok and re.search(r"Status:\s*enabled", res.stdout or "")):
            raise ActionFailure(kind, "RDP did not report enabled after apply:\n{}".format(res.stdout or res.stderr))
    elif kind == "enable_rdp_service":
        if op.get("mode") == "system":
            res = run(["systemctl", "is-active", op["service"]])
        else:
            res = run(_user_cmd(op["user"], ["systemctl", "--user", "is-active", op["service"]]))
        if res.stdout.strip() != "active":
            raise ActionFailure(kind, "{} not active after apply".format(op.get("service")))
    elif kind == "enable_gdm":
        res = run(["systemctl", "is-active", "gdm"])
        if res.stdout.strip() != "active":
            raise ActionFailure(kind, "gdm not active after apply")
    else:
        raise SetupError("unknown op kind " + str(kind))


def apply_ops(ops, snapshot_dir, log):
    _journal_write(json.dumps({"type": "header", "snapshot_dir": snapshot_dir}) + "\n")
    for i, op in enumerate(ops):
        check_interrupt()
        if is_destructive(op):
            log.info("removing: {}".format(describe(op)))
            for line in describe_children(op):
                log.info(line)
        else:
            log.detail("apply [{}/{}]: {}".format(i + 1, len(ops), describe(op)))
        undo = build_undo(op, snapshot_dir, i)
        journal_op = dict(op)
        journal_op.pop("default_password", None)
        journal_op.pop("password", None)
        _journal_append(json.dumps({"type": "op", "op": journal_op, "undo": undo}))
        execute_op(op, undo, log)
        verify_op(op, log)


def undo_op(undo, log):
    kind = undo["kind"]
    log.detail("undo: " + kind)
    if kind == "create_group":
        run(["groupdel", undo["name"]])
    elif kind == "modify_group_gid":
        run(["groupmod", "-g", str(undo["old_gid"]), undo["name"]])
    elif kind in ("delete_group", "delete_user"):
        pass
    elif kind == "create_user":
        run(["userdel", undo["name"]])
        if not undo.get("home_existed"):
            try:
                os.rmdir(undo["home"])
            except OSError:
                pass
    elif kind == "set_user_groups":
        cmd = ["usermod", "-G", ",".join(undo["old_groups"]) if undo["old_groups"] else "", undo["name"]]
        run(cmd)
    elif kind == "set_user_shell":
        run(["usermod", "-s", undo["old_shell"], undo["name"]])
    elif kind == "create_dir":
        try:
            os.rmdir(undo["path"])
        except OSError:
            pass
    elif kind == "chown_dir":
        run(["chown", "{}:{}".format(undo["old_owner"], undo["old_group"]), undo["path"]])
    elif kind == "chmod_dir":
        run(["chmod", oct(undo["old_mode"])[2:], undo["path"]])
    elif kind == "delete_dir":
        if undo.get("was_link"):
            if not os.path.lexists(undo["path"]):
                os.symlink(undo["link_target"], undo["path"])
        else:
            backup = undo["backup"]
            if backup and os.path.isdir(backup) and not os.path.lexists(undo["path"]):
                parent = os.path.dirname(undo["path"])
                os.makedirs(parent, exist_ok=True)
                shutil.copytree(backup, undo["path"])
    elif kind == "write_sudoers":
        if undo.get("old_content") is None:
            if os.path.exists(SUDOERS_PATH):
                os.remove(SUDOERS_PATH)
        else:
            with open(SUDOERS_PATH, "w") as f:
                f.write(undo["old_content"])
            os.chmod(SUDOERS_PATH, 0o440)
    elif kind == "write_umask_profile":
        if undo.get("old_content") is None:
            try:
                os.remove(UMASK_PROFILE_PATH)
            except OSError:
                pass
        else:
            write_file_atomic(UMASK_PROFILE_PATH, undo["old_content"], 0o644, log)
    elif kind == "noop":
        pass
    elif kind == "write_tfc_paths_env":
        if undo.get("old_content") is None:
            try:
                os.remove(undo["dest"])
            except OSError:
                pass
        else:
            write_file_atomic(undo["dest"], undo["old_content"], undo["mode"], log)
            if undo.get("owner") and undo.get("group"):
                run(["chown", "{}:{}".format(undo["owner"], undo["group"]), undo["dest"]])
    elif kind == "set_rdp_credentials":
        if undo.get("clear"):
            run(_grd_cmd(undo["mode"], undo.get("user"), ["rdp", "clear-credentials"]))
    elif kind == "enable_rdp":
        if undo.get("disable"):
            run(_grd_cmd(undo["mode"], undo.get("user"), ["rdp", "disable"]))
    elif kind == "enable_rdp_service":
        if undo.get("disable"):
            if undo.get("mode") == "system":
                run(["systemctl", "disable", undo["service"]])
            else:
                run(_user_cmd(undo["user"], ["systemctl", "--user", "disable", undo["service"]]))
                if undo.get("enable_linger"):
                    run(["loginctl", "disable-linger", undo["user"]])
    elif kind == "enable_gdm":
        if undo.get("disable"):
            run(["systemctl", "disable", "gdm.service"])
    else:
        log.error("unknown undo kind: " + str(kind))


def perform_rollback(log):
    entries = []
    try:
        with open(JOURNAL_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except OSError:
        log.error("no journal found; nothing to roll back")
        return

    install_force_exit_handlers()
    log.error("rolling back applied changes...")

    snapshot_dir = None
    ops = []
    for e in entries:
        if e.get("type") == "header":
            snapshot_dir = e.get("snapshot_dir")
        elif e.get("type") == "op":
            ops.append(e)

    if snapshot_dir and os.path.isdir(snapshot_dir):
        try:
            for name in ACCOUNT_FILES:
                src = os.path.join(snapshot_dir, name)
                dst = os.path.join("/etc", name)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
            log.error("account database restored from snapshot")
        except OSError as exc:
            log.error("failed to restore account database: " + str(exc))
    else:
        log.error("snapshot not found — account database was NOT restored")

    for e in reversed(ops):
        undo = e.get("undo", {})
        try:
            undo_op(undo, log)
        except Exception as exc:
            log.error("undo failed for {}: {}".format(undo.get("kind", "?"), exc))

    if snapshot_dir and os.path.isdir(snapshot_dir):
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    clear_journal()
    log.error("rollback complete — review the messages above")


def acquire_lock(log):
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SetupError("another setup run is in progress (lock: {})".format(LOCK_PATH))
    return fd


def release_lock(fd):
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(
        description="Provision the tfc-autonomous Jetson environment (idempotent, atomic, deterministic).")
    parser.add_argument("--manifest", default="manifest.json",
                        help="path to the JSON manifest (default: manifest.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and print the plan without applying anything")
    parser.add_argument("--recover", action="store_true",
                        help="roll back a previous interrupted run from its journal")
    parser.add_argument("--force", action="store_true",
                        help="allow removal of protected paths declared absent")
    parser.add_argument("--verbose", action="store_true",
                        help="print detailed per-step output")
    args = parser.parse_args()

    log = Logger(args.verbose)
    install_signal_handlers()

    try:
        manifest = load_manifest(args.manifest)
        errors = resolve_manifest(manifest)
        if errors:
            for e in errors:
                log.error("validation: " + e)
            raise ValidationError("manifest is invalid ({} problem(s))".format(len(errors)))
        validate_manifest(manifest, log)
        ops = build_plan(manifest, args.force, log)

        if args.dry_run:
            if not ops:
                log.info("no changes needed — already in desired state")
                return EXIT_OK
            log.info("planned operations: {}".format(len(ops)))
            for op in ops:
                if is_destructive(op):
                    print("  removing: " + describe(op))
                else:
                    print("  " + describe(op))
                for line in describe_children(op):
                    print(line)
            log.info("dry-run: nothing was applied")
            return EXIT_OK

        if os.geteuid() != 0:
            log.error("must run as root (e.g. sudo python3 setup_jetson.py)")
            return EXIT_ERROR

        lock_fd = acquire_lock(log)
        try:
            if os.path.exists(JOURNAL_PATH):
                if args.recover:
                    log.info("recovering previous interrupted run...")
                    perform_rollback(log)
                    log.info("recovery complete")
                    return EXIT_OK
                log.error("a previous run was interrupted and left a journal.")
                log.error("re-run with --recover to roll it back, or delete {} if the system is already correct.".format(JOURNAL_PATH))
                return EXIT_ERROR

            check_interrupt()

            if not ops:
                log.info("no changes needed — already in desired state")
                return EXIT_OK

            log.info("applying {} operation(s)...".format(len(ops)))
            snapshot_dir = create_snapshot(log)
            try:
                apply_ops(ops, snapshot_dir, log)
            except Interrupted as exc:
                log.error("interrupted during apply ({})".format(exc))
                perform_rollback(log)
                return EXIT_SIGINT
            except SetupError as exc:
                log.error("apply failed: {}".format(exc))
                perform_rollback(log)
                return EXIT_ERROR
            except Exception as exc:
                log.error("unexpected failure: {!r}".format(exc))
                perform_rollback(log)
                return EXIT_ERROR

            clear_journal()
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            log.info("done — environment converged to the manifest")
            return EXIT_OK
        finally:
            release_lock(lock_fd)
    except ValidationError as exc:
        log.error(str(exc))
        return EXIT_ERROR
    except SetupError as exc:
        log.error(str(exc))
        return EXIT_ERROR
    except Exception as exc:
        log.error("unexpected failure: {!r}".format(exc))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
