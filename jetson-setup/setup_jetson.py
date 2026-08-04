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

ACCOUNT_FILES = ["passwd", "shadow", "group", "gshadow"]

NAME_RE = re.compile(r"^[a-z_][a-z0-9._-]*$")
MODE_RE = re.compile(r"^[0-7]{4}$")

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


def run(cmd, timeout=60):
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return CommandResult(127, "", "failed to spawn {}: {}".format(cmd[0], exc))
    try:
        out, err = proc.communicate(timeout=timeout)
        return CommandResult(proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
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

    for rule in sudoers:
        if not isinstance(rule, str) or not rule.strip():
            _fail(errors, "sudoers: rules must be non-empty strings")

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
            return [{"kind": "create_user", "name": name, "groups": sorted(supp), "shell": shell, "home": home}]
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
        return [{"kind": "delete_dir", "path": path}]
    return []


def sudoers_ops(rules, log):
    desired = "".join(r + "\n" for r in rules)
    current = None
    if os.path.exists(SUDOERS_PATH):
        with open(SUDOERS_PATH) as f:
            current = f.read()
    if current == desired:
        return []
    if current is None and desired == "":
        return []
    return [{"kind": "write_sudoers", "old_content": current, "content": desired}]


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
    for op in sudoers_ops(manifest.get("sudoers", []), log):
        (present if not is_destructive(op) else removals).append(op)

    removal_order = {"delete_dir": 0, "delete_user": 1, "delete_group": 2}
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
        return "create user {} (groups: {}, shell: {})".format(op["name"], ",".join(op["groups"]) or "none", op["shell"])
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
    return kind


def is_destructive(op):
    kind = op["kind"]
    if kind in ("delete_group", "delete_user", "delete_dir"):
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
    else:
        raise SetupError("unknown op kind " + str(kind))


def apply_ops(ops, snapshot_dir, log):
    _journal_write(json.dumps({"type": "header", "snapshot_dir": snapshot_dir}) + "\n")
    for i, op in enumerate(ops):
        check_interrupt()
        if is_destructive(op):
            log.info("removing: {}".format(describe(op)))
        else:
            log.detail("apply [{}/{}]: {}".format(i + 1, len(ops), describe(op)))
        undo = build_undo(op, snapshot_dir, i)
        _journal_append(json.dumps({"type": "op", "op": op, "undo": undo}))
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
