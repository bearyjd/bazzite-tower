#!/usr/bin/env python3
"""Fixed-path, refusal-based system-bridge checks; imports are test seams only."""
import configparser
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path

PINS = "/usr/share/snitchwatch/build-pins.json"
BRIDGE_VERSION = "0.1.1"
PROFILE = "/usr/share/bazzite-tower/snitchwatch-bridge-profile"
MANIFEST = "/usr/share/snitchwatch/system-bridge-manifest.json"
CONFIG = "/etc/opensnitchd/default-config.json"
REFERENCE = "/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json"
LEGACY_REFERENCE = "/usr/share/bazzite-tower/opensnitchd-default-config.json"
BINARY = "/usr/bin/snitchwatch-bridge-cli"
DAEMON = "/usr/bin/opensnitchd"
DAEMON_MANIFEST = "/usr/share/snitchwatch/system-daemon-manifest.json"
DAEMON_CANDIDATE = "/usr/share/snitchwatch/daemon/opensnitchd"
DAEMON_PATCH = "/usr/share/snitchwatch/daemon/opensnitch-shutdown-repair.patch"
SERVICE = "snitchwatch-system-bridge.service"
SOCKETS = ("snitchwatch-system-bridge-grpc.socket", "snitchwatch-system-bridge-gui.socket")
UNITS = (*SOCKETS, SERVICE, "opensnitch.service")
APP = "org.snitchwatch.Snitchwatch"
VENDOR_DROPIN = "/usr/lib/systemd/system/service.d/10-timeout-abort.conf"
DAEMON_UNIT = "/usr/lib/systemd/system/opensnitch.service"
DAEMON_DROPIN = "/usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf"
# The one opensnitchd rule Snitchwatch ships; the only manifest entry outside /usr.
FETCH_RULE = "/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json"
# The tower's always-on OS-update and resolver DNS rules; with FETCH_RULE, the
# only entries outside /usr.
OS_UPDATE_RULE = "/etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json"
DNS_RULE = "/etc/opensnitchd/rules/000-snitchwatch-tower-resolved-dns.json"
RESERVED_RULES = {FETCH_RULE: "Snitchwatch fetch rule", OS_UPDATE_RULE: "tower OS-update rule", DNS_RULE: "tower resolver DNS rule"}
# Operator deny opt-in (bazzite-tower-snitchwatch-deny). The image always ships allow.
DENY_MARKER = "/etc/bazzite-tower/snitchwatch-deny-optin"
DENY_GUARD_DROPIN = "/usr/lib/systemd/system/opensnitch.service.d/bazzite-tower-snitchwatch-deny-guard.conf"
DENY_GUARD_DROPIN_SHA256 = "b46aa696d4d8da32c2b510425eb04df03a4b19e243fdf8e6cf9158b80c5d2d43"
DENY_STATE = "/var/lib/bazzite-tower/snitchwatch-deny/state.json"
BASE_GLOBAL_OVERRIDE = "/var/lib/flatpak/overrides/global"
BASE_GLOBAL_OVERRIDE_SHA256 = "85e2bf73515c8da8950f1afbbfaedfeca453777eeb0be0ea869f4db6c779bd80"
VENDOR_DROPIN_SHA256 = "ae6b234f92bc22f1201a7572b59b454c9809f33c80d13f361b9674e1801acc37"
TYPED_EMPTY_PROPERTIES = {"EnvironmentFiles": "a(sb)", **{name: "a(sasbttttuii)" for name in ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecCondition")}}


# Single table of every helper the privileged tools may run, by absolute path
# (all verified in /usr/bin on the Bazzite host; /bin and /usr/sbin are symlinks
# or empty merges there). Resolution never consults the caller's PATH.
COMMANDS = {name: "/usr/bin/"+name for name in ("flatpak", "systemctl", "ss", "busctl", "getent", "python3", "systemd-run")}
# The only environment a helper sees: no inherited PATH, LD_*, PYTHON* or locale.
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin", "LC_ALL": "C"}
# Real Flatpak metadata is well under 2 KiB; the store is user-writable.
METADATA_MAX_BYTES = 64*1024


class Refusal(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


class Context:
    def __init__(self, root=Path("/"), runner=None):
        self.root = Path(root)
        self.runner = runner or self.spawn

    @staticmethod
    def spawn(argv, **kwargs):
        # Default runner. Injected test runners see the logical argv unchanged.
        program = COMMANDS.get(argv[0], argv[0])
        require(program.startswith("/"), "not a trusted command: "+argv[0])
        require(os.access(program, os.X_OK), "required command missing: "+program)
        return subprocess.run([program, *argv[1:]], env=dict(COMMAND_ENV), **kwargs)

    def path(self, absolute):
        require(absolute.startswith("/") and ".." not in Path(absolute).parts, "unsafe internal path")
        return self.root / absolute.lstrip("/")

    def command(self, argv, okay=(0,), timeout=5):
        result = self.runner(argv, capture_output=True, text=True, timeout=timeout)
        require(result.returncode in okay, "command failed: "+shlex.join(argv)+": "+result.stderr.strip())
        return result

    def props(self, unit, names):
        output = self.command(["systemctl", "show", unit, "--no-pager", "--all", "--property="+",".join(names)]).stdout
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        missing = [name for name in names if name not in values]
        if missing:
            require(unit == SERVICE and all(name in TYPED_EMPTY_PROPERTIES for name in missing), "missing effective systemd properties for "+unit)
            # systemctl omits these empty structured arrays even with --all.
            # Never infer absence: ask the typed, read-only system D-Bus API.
            bus = ["busctl", "--system", "--json=short"]
            def reply(argv):
                try:
                    value = json.loads(self.command(argv).stdout)
                except (ValueError, TypeError) as error:
                    raise Refusal("invalid typed systemd property reply") from error
                require(isinstance(value, dict) and set(value) == {"type", "data"}, "invalid typed systemd property reply shape")
                return value
            loaded = reply(bus+["call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "GetUnit", "s", unit])
            require(loaded["type"] == "o" and isinstance(loaded["data"], list) and len(loaded["data"]) == 1
                    and isinstance(loaded["data"][0], str) and re.fullmatch(r"/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+", loaded["data"][0]), "invalid typed GetUnit object path")
            for name in missing:
                actual = reply(bus+["get-property", "org.freedesktop.systemd1", loaded["data"][0], "org.freedesktop.systemd1.Service", name])
                require(actual["type"] == TYPED_EMPTY_PROPERTIES[name] and actual["data"] == [], "effective structured property must be typed empty: "+name)
                values[name] = ""
        require(all(name in values for name in names), "missing effective systemd properties for "+unit)
        return values

    def stat(self, absolute):
        return self.path(absolute).lstat()

    def lookup(self, absolute):
        # One lstat decides. Absent only when the kernel says so; a path
        # readiness cannot inspect (EACCES, ELOOP, EIO) may hold an override,
        # so it is refused.
        try:
            return self.stat(absolute)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as error:
            raise Refusal("cannot inspect "+absolute+": "+(error.strerror or str(error))) from error

    def present(self, absolute):
        return self.lookup(absolute) is not None

    def listdir(self, absolute):
        # Follows a symlinked directory, as systemd does; absent or not a
        # directory lists as empty.
        try:
            return sorted(os.listdir(self.path(absolute)))
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError as error:
            raise Refusal("cannot inspect "+absolute+": "+(error.strerror or str(error))) from error

    def homes(self):
        records = self.command(["getent", "passwd"]).stdout.splitlines()
        return sorted({record.split(":")[5] for record in records if len(record.split(":")) == 7 and record.split(":")[5].startswith("/")})


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable(ctx):
    require(ctx.path(PROFILE).read_text().strip() == "system", "image profile is not system")
    ctx.command(["python3", "/usr/libexec/snitchwatch/verify-system-manifest.py"])
    metadata = ctx.stat(MANIFEST)
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022, "system manifest must be a root-owned, non-writable regular file")
    manifest = json.loads(ctx.path(MANIFEST).read_text())
    require(manifest.get("schemaVersion") == 1 and manifest.get("profile") == "system", "invalid system manifest schema/profile")
    pins = json.loads(ctx.path(PINS).read_text())
    source = manifest.get("source", {})
    require(source.get("commit") == pins.get("sourceCommit") and source.get("submoduleCommit") == pins.get("submoduleCommit")
            and re.fullmatch(r"[0-9a-f]{40}", source.get("commit", "")) and re.fullmatch(r"[0-9a-f]{40}", source.get("submoduleCommit", "")), "unexpected source provenance")
    require(manifest.get("binary", {}).get("path") == BINARY, "unexpected manifest executable path")
    files = manifest.get("files", {})
    mandatory = {BINARY, REFERENCE, LEGACY_REFERENCE, PINS, FETCH_RULE, OS_UPDATE_RULE, DNS_RULE, "/usr/lib/sysusers.d/snitchwatch.conf", "/usr/lib/tmpfiles.d/snitchwatch.conf", DAEMON_DROPIN}
    mandatory.update("/usr/lib/systemd/system/"+unit for unit in (SERVICE, *SOCKETS))
    require(isinstance(files, dict) and mandatory <= files.keys(), "system manifest lacks required installed assets")
    for name, expected in files.items():
        require((name.startswith("/usr/") or name in RESERVED_RULES) and ".." not in Path(name).parts and re.fullmatch(r"[0-9a-f]{64}", expected or ""), "unsafe system manifest entry")
        if name in RESERVED_RULES:
            # /etc is mutable: no directory on the way may be a symlink either.
            require(not any(stat.S_ISLNK(ctx.stat(str(parent)).st_mode) for parent in Path(name).parents if str(parent) != "/"),
                    "symlink in the path of the shipped "+RESERVED_RULES[name])
        info = ctx.stat(name)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "mutable/non-root installed asset: "+name)
        require(digest(ctx.path(name)) == expected, "installed asset hash mismatch: "+name)
    require(manifest["binary"].get("sha256") == files[BINARY], "binary provenance disagrees with installed asset hash")
    require(manifest["binary"].get("version") == BRIDGE_VERSION and pins.get("bridgeVersion") == BRIDGE_VERSION, "manifest/pins must declare bridge version "+BRIDGE_VERSION)
    fp = manifest.get("flatpak", {})
    require(fp.get("appId") == APP and fp.get("profile") == "system" and fp.get("sourceCommit") == source["commit"]
            and re.fullmatch(r"[0-9a-f]{64}", fp.get("manifestSha256", "")), "missing system GUI profile provenance")
    version = ctx.command([BINARY, "--version"]).stdout.strip()
    require(version == "snitchwatch-bridge-cli "+BRIDGE_VERSION, "installed bridge version must be "+BRIDGE_VERSION+" (got: "+version+")")
    immutable_daemon(ctx, pins)
    return manifest


def immutable_daemon(ctx, pins):
    proof = json.loads(ctx.command(["python3", "/usr/libexec/snitchwatch/verify-system-daemon.py"]).stdout)
    info = ctx.stat(DAEMON_MANIFEST)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "daemon manifest must be a root-owned non-writable regular file")
    manifest = json.loads(ctx.path(DAEMON_MANIFEST).read_text())
    require(manifest.get("schemaVersion") == 1 and manifest.get("profile") == "system", "invalid patched daemon manifest profile/schema")
    require(manifest.get("source", {}).get("commit") == pins.get("submoduleCommit"), "patched daemon upstream differs from reviewed bridge submodule")
    binary, patch = manifest.get("binary", {}), manifest.get("patch", {})
    require(binary.get("path") == DAEMON and binary.get("candidatePath") == DAEMON_CANDIDATE and binary.get("version") == "1.8.0", "patched daemon binary path/version drift")
    require(patch.get("path") == DAEMON_PATCH, "patched daemon repair patch path drift")
    for name, expected in ((DAEMON, binary.get("sha256")), (DAEMON_CANDIDATE, binary.get("sha256")), (DAEMON_PATCH, patch.get("sha256"))):
        require(re.fullmatch(r"[0-9a-f]{64}", expected or ""), "missing patched daemon artifact identity")
        metadata = ctx.stat(name)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022, "mutable/non-root patched daemon artifact: "+name)
        require(digest(ctx.path(name)) == expected, "patched daemon artifact hash mismatch: "+name)
    require(proof.get("profile") == "system" and proof.get("sourceCommit") == manifest["source"]["commit"] and proof.get("patchSha256") == patch["sha256"]
            and proof.get("binaryPath") == DAEMON and proof.get("binarySha256") == binary["sha256"] and proof.get("installedBinaryChecked") is True,
            "daemon verifier did not establish installed patched binary identity")
    require(ctx.command([DAEMON, "-version"]).stdout.strip() == "1.8.0", "installed patched daemon version drift")
    return manifest


def account_ids(ctx):
    passwd = ctx.command(["getent", "passwd", "snitchwatch"]).stdout.strip().split(":")
    group = ctx.command(["getent", "group", "snitchwatch"]).stdout.strip().split(":")
    ui = ctx.command(["getent", "group", "snitchwatch-ui"]).stdout.strip().split(":")
    require(len(passwd) == 7 and len(group) == 4 and len(ui) == 4, "named bridge/UI accounts unavailable")
    uid, gid, ui_gid = int(passwd[2]), int(group[2]), int(ui[2])
    require(uid != 0 and gid != 0 and ui_gid != 0 and gid != ui_gid and int(passwd[3]) == gid, "invalid bridge/UI account separation")
    require(passwd[5] == "/var/lib/snitchwatch" and passwd[6] in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false"), "bridge account must be non-login with private state home")
    return uid, gid, ui_gid, ui[3]


def unit_contract(ctx):
    expected = dict(User="snitchwatch", Group="snitchwatch", NoNewPrivileges="yes", CapabilityBoundingSet="", AmbientCapabilities="",
                    ProtectSystem="strict", ProtectHome="yes", PrivateTmp="yes", PrivateDevices="yes", ProtectKernelTunables="yes",
                    ProtectKernelModules="yes", ProtectControlGroups="yes", ReadWritePaths="/run/snitchwatch-auth", StateDirectory="snitchwatch",
                    StateDirectoryMode="0700", SupplementaryGroups="", EnvironmentFiles="", RootDirectory="", RootImage="",
                    BindPaths="", BindReadOnlyPaths="", FragmentPath="/usr/lib/systemd/system/"+SERVICE, DropInPaths="", ExecStartPre="", ExecStartPost="", ExecStop="", ExecStopPost="", ExecCondition="")
    names = [*expected, "ExecStart", "Environment", "TriggeredBy", "RestrictAddressFamilies", "MainPID", "NeedDaemonReload"]
    actual = ctx.props(SERVICE, names)
    loaded_current(SERVICE, actual)
    vendor_dropin_contract(ctx, actual["DropInPaths"])
    for name, value in expected.items():
        if name == "DropInPaths":
            continue
        require(actual[name] == value, "effective bridge property drift: "+name)
    require(re.findall(r"(?:^|[ {])path=([^ ;}]+)", actual["ExecStart"]) == [BINARY]
            and re.findall(r"argv\[\]=([^;]+)", actual["ExecStart"]) == [BINARY+" "], "effective bridge ExecStart must be only "+BINARY)
    env = dict(item.split("=", 1) for item in shlex.split(actual["Environment"]) if "=" in item)
    expected_env = dict(SNITCHWATCH_SYSTEM_BRIDGE="1", SNITCHWATCH_WS_SOCKET="/run/snitchwatch/bridge.sock", SNITCHWATCH_WS_TOKEN_PATH="/run/snitchwatch-auth/token", HOME="/var/lib/snitchwatch", XDG_STATE_HOME="/var/lib")
    require(env == expected_env, "effective bridge environment drift")
    require(set(actual["TriggeredBy"].split()) == set(SOCKETS), "effective bridge socket association drift")
    require(set(actual["RestrictAddressFamilies"].split()) == {"AF_UNIX", "AF_INET", "AF_INET6"}, "effective bridge address-family restriction drift")
    for unit, path, group, mode in ((SOCKETS[0], "/run/snitchwatch/opensnitchd.sock", "root", "0600"), (SOCKETS[1], "/run/snitchwatch/bridge.sock", "snitchwatch-ui", "0660")):
        props = ctx.props(unit, ["Listen", "SocketUser", "SocketGroup", "SocketMode", "Accept", "Triggers", "FragmentPath", "DropInPaths", "NeedDaemonReload"])
        loaded_current(unit, props)
        require({k: v for k, v in props.items() if k != "NeedDaemonReload"} == dict(Listen=path+" (Stream)", SocketUser="root", SocketGroup=group, SocketMode=mode, Accept="no", Triggers=SERVICE, FragmentPath="/usr/lib/systemd/system/"+unit, DropInPaths=""), "effective socket contract drift: "+unit)
    daemon = ctx.props("opensnitch.service", ["WorkingDirectory", "Wants", "After", "Requires", "Requisite", "BindsTo", "PartOf", "ExecStart", "FragmentPath", "DropInPaths", "NeedDaemonReload"])
    loaded_current("opensnitch.service", daemon)
    daemon_unit_contract(ctx, daemon)
    daemon_exec_contract(daemon["ExecStart"])
    # The drop-in's WorkingDirectory=-/run/snitchwatch (missing is OK, so a
    # failed socket never stops the daemon) shows as "!/run/...".
    require(daemon["WorkingDirectory"] == "!/run/snitchwatch" and SOCKETS[0] in daemon["Wants"].split() and SOCKETS[0] in daemon["After"].split(), "OpenSnitch effective Unix socket CWD/dependencies drift")
    # A stop of the gRPC socket must never take the firewall down with it.
    require(not any(SOCKETS[0] in daemon[key].split() for key in ("Requires", "Requisite", "BindsTo", "PartOf")), "OpenSnitch must only Want the bridge gRPC socket: a socket stop would stop the firewall")
    return actual


def loaded_current(unit, props):
    # systemd reports yes when a unit file or drop-in was added, changed or
    # deleted on disk since it was loaded, including in directories the
    # local-override scan does not list.
    require(props["NeedDaemonReload"] == "no", unit+" changed on disk since systemd loaded it; review it, then run systemctl daemon-reload")


def daemon_unit_contract(ctx, props):
    # The image's unit file plus its system-bridge drop-in, and at most
    # Fedora's global service.d timeout drop-in; nothing else may be loaded.
    dropins = props["DropInPaths"].split()
    require(props["FragmentPath"] == DAEMON_UNIT and DAEMON_DROPIN in dropins and len(dropins) == len(set(dropins))
            and set(dropins) <= {DAEMON_DROPIN, VENDOR_DROPIN, DENY_GUARD_DROPIN}, "OpenSnitch effective unit file/drop-in drift: "+props["FragmentPath"]+" + "+props["DropInPaths"])
    if VENDOR_DROPIN in dropins:
        vendor_dropin_contract(ctx, VENDOR_DROPIN)
    if DENY_GUARD_DROPIN in dropins:
        info = ctx.stat(DENY_GUARD_DROPIN)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022 and digest(ctx.path(DENY_GUARD_DROPIN)) == DENY_GUARD_DROPIN_SHA256,
                "deny-guard drop-in must be the root-owned immutable image file (byte identity drift)")


def vendor_dropin_contract(ctx, value):
    require(value in ("", VENDOR_DROPIN), "effective bridge property drift: DropInPaths")
    if not value:
        return
    path = ctx.path(VENDOR_DROPIN)
    require(path.resolve() == ctx.root.resolve()/VENDOR_DROPIN.lstrip("/"), "vendor drop-in must be canonical without symlinks")
    info = ctx.stat(VENDOR_DROPIN)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and not info.st_mode & 0o022,
            "vendor drop-in must be root-owned immutable regular file")
    require(digest(path) == VENDOR_DROPIN_SHA256, "vendor drop-in byte identity drift")


def daemon_exec_contract(value):
    require(re.findall(r"(?:^|[ {])path=([^ ;}]+)", value) == [DAEMON], "effective daemon ExecStart binary mismatch")
    require(re.findall(r"argv\[\]=([^;]+)", value) == [DAEMON+" "], "effective daemon ExecStart must be only "+DAEMON+"; CLI config/IPC/policy overrides are refused")


def regular_root_json(ctx, name):
    # None unless a root-owned, non-writable, non-symlink regular file with a JSON object.
    info = ctx.lookup(name)
    if info is None or not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022):
        return None
    try:
        value = json.loads(ctx.path(name).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def deny_opt_in(ctx):
    marker = regular_root_json(ctx, DENY_MARKER)
    if not marker or marker.get("schema") != 1 or not isinstance(marker.get("confirmedAt"), str) or not isinstance(marker.get("id"), str):
        return False
    if ctx.present(DENY_STATE):
        state = regular_root_json(ctx, DENY_STATE)
        return bool(state) and state.get("pending") is False and state.get("id") == marker["id"]
    return True


def config_contract(ctx, address="unix:opensnitchd.sock"):
    config = json.loads(ctx.path(CONFIG).read_text())
    require(config.get("Server", {}).get("Address") == address, "live /etc Server.Address drift; use the explicit migration helper")
    action = config.get("DefaultAction")
    if action == "deny":
        require(deny_opt_in(ctx), "DefaultAction drift: deny is accepted only with a confirmed operator opt-in marker and no pending deny trial")
    else:
        require(action == "allow", "DefaultAction drift: system readiness requires allow; deny is an explicit local opt-in")
    require(config.get("ProcMonitorMethod") == "proc", "ProcMonitorMethod drift: system profile requires proc")
    return config


def inert_dropin_directory(ctx, name):
    # systemd loads nothing from an empty drop-in directory, so under a
    # root-only unit directory it is not an override. One lstat decides: a real
    # directory, root:root, not group/world-writable, with no entries.
    info = ctx.lookup(name)
    if info is None:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
            and not info.st_mode & 0o022 and not ctx.listdir(name))


def local_conflicts(ctx):
    # Include control/transient/generator and default XDG search paths; an
    # immutable /usr/lib mask alone cannot defeat higher-priority user units.
    system_dirs = ("/etc/systemd/system.control", "/run/systemd/system.control", "/run/systemd/transient",
                   "/run/systemd/generator.early", "/etc/systemd/system", "/run/systemd/system",
                   "/run/systemd/generator", "/usr/local/lib/systemd/system", "/run/systemd/generator.late")
    paths = [directory+"/"+unit+suffix for directory in system_dirs for unit in UNITS for suffix in ("", ".d")]
    paths.extend(directory+"/service.d" for directory in system_dirs)
    # Only root can add or replace entries here. User-unit drop-in directories
    # are never exempt: under a home directory the user owns the parent, so an
    # empty one there proves nothing.
    system_dropins = {name for name in paths if name.endswith(".d")}
    user_dirs = {"/etc/systemd/user", "/run/systemd/user", "/etc/xdg/systemd/user",
                 "/usr/local/lib/systemd/user", "/usr/local/share/systemd/user", "/usr/share/systemd/user"}
    records = ctx.command(["getent", "passwd"]).stdout.splitlines()
    for record in records:
        fields = record.split(":")
        require(len(fields) == 7 and fields[2].isdigit(), "invalid all-UID account inventory")
        uid, home = fields[2], fields[5]
        if home.startswith("/"):
            user_dirs.update((home+"/.config/systemd/user.control", home+"/.config/systemd/user", home+"/.local/share/systemd/user"))
        runtime = "/run/user/"+uid+"/systemd/"
        user_dirs.update(runtime+suffix for suffix in ("user.control", "transient", "generator.early", "user", "generator", "generator.late"))
    for directory in user_dirs:
        paths.extend(directory+"/snitchwatch-bridge.service"+suffix for suffix in ("", ".d"))
        paths.extend(directory+"/"+entry+"/snitchwatch-bridge.service" for entry in ctx.listdir(directory)
                     if entry.rsplit(".", 1)[-1] in ("wants", "requires", "upholds"))
    for name in paths:
        if name in system_dropins and inert_dropin_directory(ctx, name):
            continue
        require(not ctx.present(name), "local unit override/enabled legacy user service: "+name)
    global_state = ctx.command(["systemctl", "--global", "is-enabled", "snitchwatch-bridge.service"], okay=(0, 1, 3, 4)).stdout.strip()
    require(global_state in ("disabled", "masked", "not-found"), "legacy global user service remains enabled: "+global_state)


def legacy_absent(ctx, system_pid=0):
    for proc in ctx.path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == system_pid:
            continue
        try:
            exe = os.readlink(proc/"exe")
            command = (proc/"cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
        except FileNotFoundError:
            continue
        except PermissionError as error:
            raise Refusal("cannot inspect all-UID process identity; invoke the read-only helper explicitly as root") from error
        if Path(exe.removesuffix(" (deleted)")).name in ("snitchwatch-bridge-cli", "snitchwatch-bridge") or Path(command).name in ("snitchwatch-bridge-cli", "snitchwatch-bridge"):
            raise Refusal("legacy/competing bridge process remains: PID"+proc.name)
    tcp = ctx.command(["ss", "-H", "-ltnp", "sport = :50051"]).stdout.strip()
    require(not tcp, "legacy TCP listener remains on port50051")
    unix = ctx.command(["ss", "-H", "-lxnp"]).stdout
    require(not re.search(r"/run/user/[0-9]+/snitchwatch/bridge\.sock|/tmp/[^ ]*snitchwatch[^ ]*\.sock", unix), "legacy per-user bridge Unix listener remains")


def flatpak_sockets_match(ctx, sockets):
    normative = {"wayland", "fallback-x11"}
    if sockets == normative:
        return True
    if sockets != normative | {"x11"}:
        return False
    # Flatpak's compute_allowed_sockets replaces X11 with !has-wayland when
    # fallback-x11 is allowed, including serialized redundant X11. Upstream
    # tags 1.18.1 through 1.18.4 were diffed and that evaluator is unchanged
    # (1.18.2 only swaps the permissions-table constructor). Only these
    # reviewed versions support the canonical exception; widen after review.
    try:
        version = ctx.command(["flatpak", "--version"]).stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refusal("cannot detect Flatpak version for canonical sockets") from error
    require(re.fullmatch(r"Flatpak 1\.18\.[1-4]\n?", version) is not None,
            "unreviewed Flatpak version for canonical sockets")
    return True


def read_user_metadata(path):
    # User-writable store: never block on a FIFO, follow a symlink, or read unboundedly.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise Refusal("unreadable Flatpak metadata "+str(path)+": "+str(error)) from error
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refusal("unreadable Flatpak metadata "+str(path)+": not a regular file")
        handle = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        data = handle.read(METADATA_MAX_BYTES+1)
    require(len(data) <= METADATA_MAX_BYTES, "unreadable Flatpak metadata "+str(path)+": larger than "+str(METADATA_MAX_BYTES)+" bytes")
    try:
        return data.decode()
    except UnicodeDecodeError as error:
        raise Refusal("unreadable Flatpak metadata "+str(path)+": "+str(error)) from error


# Only the [Context] keys readiness reviews below; features=, unset-environment= etc. must not pass unchecked.
CONTEXT_KEYS = frozenset({"shared", "sockets", "devices", "filesystems", "persistent"})


def flatpak_profiles(ctx):
    system_stores = ctx.command(["flatpak", "--installations"]).stdout.splitlines()
    require(system_stores and all(name.startswith("/") and ".." not in Path(name).parts for name in system_stores), "cannot enumerate system Flatpak installations")
    stores = sorted(set(system_stores+[home+"/.local/share/flatpak" for home in ctx.homes()]))
    expected_flatpak = json.loads(ctx.path(MANIFEST).read_text()).get("flatpak", {})
    expected_runtime = expected_flatpak.get("runtimeRef", "")
    require(re.fullmatch(r"org\.kde\.Platform/x86_64/[0-9]+\.[0-9]+", expected_runtime), "missing verified Flatpak runtime reference")
    finish_args = expected_flatpak.get("finishArgs", [])
    require(isinstance(finish_args, list) and all(isinstance(arg, str) for arg in finish_args), "malformed verified Flatpak finish args")
    # flatpak build-finish serializes --filesystem=X verbatim as X in [Context]
    # filesystems=; exact members only, so a :rw/:create variant or a parent path
    # is not covered by a pinned :ro grant.
    pinned_filesystems = {arg[len("--filesystem="):] for arg in finish_args if arg.startswith("--filesystem=")}
    for store in stores:
        for override in ("global", APP):
            path = ctx.path(store+"/overrides/"+override)
            if not path.exists() and not path.is_symlink():
                continue
            require(store+"/overrides/"+override == BASE_GLOBAL_OVERRIDE, "same-ID/global Flatpak overrides require explicit review: "+str(path))
            require(path.resolve() == ctx.root.resolve()/BASE_GLOBAL_OVERRIDE.lstrip("/"), "base global Flatpak override must be canonical without symlinks")
            info = ctx.stat(BASE_GLOBAL_OVERRIDE)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and not info.st_mode & 0o022,
                    "base global Flatpak override must be root-owned non-writable regular file")
            require(digest(path) == BASE_GLOBAL_OVERRIDE_SHA256, "base global Flatpak override byte identity drift")
        appdir = ctx.path(store+"/app/"+APP)
        if not appdir.exists():
            continue
        deployments = list(appdir.glob("*/*/active/metadata"))
        require(deployments, "cannot prove installed same-ID Flatpak profile: "+store)
        for path in deployments:
            meta = configparser.ConfigParser(interpolation=None)
            meta.optionxform = str
            text = read_user_metadata(path)
            # configparser folds an indented line into the previous value but GLib's
            # GKeyFile (what Flatpak uses) reads it as a new key, so refuse the ambiguity.
            require(not any(line.strip() and line[0].isspace() for line in text.splitlines()), "unreadable Flatpak metadata "+str(path)+": indented line")
            try:
                meta.read_string(text, str(path))
            except configparser.Error as error:
                raise Refusal("unreadable Flatpak metadata "+str(path)+": "+str(error)) from error
            context = meta["Context"] if meta.has_section("Context") else {}
            require(set(context) <= CONTEXT_KEYS, "same-ID Flatpak has unreviewed [Context] key: "+", ".join(sorted(set(context)-CONTEXT_KEYS)))
            def values(key):
                return set(context.get(key, "").strip(";").split(";"))-{ "" }
            require(meta.get("Environment", "SNITCHWATCH_SYSTEM_BRIDGE", fallback="") == "1", "same-ID Flatpak uses a legacy/unknown bridge profile")
            for name, expected in (("SNITCHWATCH_WS_SOCKET", "/run/snitchwatch/bridge.sock"), ("SNITCHWATCH_WS_TOKEN_PATH", "/run/snitchwatch-auth/token")):
                require(not meta.has_option("Environment", name) or meta.get("Environment", name) == expected, "same-ID Flatpak redirects protected system IPC: "+name)
            require(meta.get("Application", "name", fallback="") == APP and meta.get("Application", "runtime", fallback="") == expected_runtime, "same-ID Flatpak runtime/application drift")
            require(values("shared") == {"ipc"} and flatpak_sockets_match(ctx, values("sockets")) and values("devices") == {"dri"}, "same-ID Flatpak grants unexpected network/socket/device authority")
            required = {"/run/snitchwatch:ro", "/run/snitchwatch-auth:ro", "xdg-config/snitchwatch:create"}
            require(required <= values("filesystems") <= required|{"xdg-config/kdeglobals:ro"}|pinned_filesystems, "same-ID Flatpak filesystem profile mismatch")
            require(values("persistent") == {".local/share/snitchwatch", ".local/state/snitchwatch"}, "same-ID Flatpak app-state persistence mismatch")
            require(not meta.has_section("System Bus Policy") or not dict(meta["System Bus Policy"]), "same-ID Flatpak has system bus authority")
            policies = dict(meta["Session Bus Policy"]) if meta.has_section("Session Bus Policy") else {}
            allowed_names = {"org.freedesktop.Notifications", "org.kde.StatusNotifierWatcher", "com.canonical.AppMenu.Registrar", "org.kde.kconfig.notify", "org.kde.KGlobalSettings", "org.kde.kdeconnect"}
            if "--talk-name=org.snitchwatch.Scanner" in finish_args:
                allowed_names.add("org.snitchwatch.Scanner")
            require(set(policies) <= allowed_names and all(value == "talk" for value in policies.values()), "same-ID Flatpak session bus grants mismatch")
    return stores


def service_state(ctx):
    result = {}
    for unit in UNITS:
        enabled = ctx.command(["systemctl", "is-enabled", unit], okay=(0, 1, 3, 4)).stdout.strip()
        active = ctx.command(["systemctl", "is-active", unit], okay=(0, 1, 3, 4)).stdout.strip()
        require(enabled in ("enabled", "enabled-runtime", "disabled", "static", "masked", "masked-runtime", "indirect"), "unknown enabled state: "+unit)
        require(active in ("active", "inactive", "failed"), "unit is transitioning: "+unit)
        result[unit] = dict(enabled=enabled, active=active)
    return result


def runtime_readiness(ctx):
    immutable(ctx)
    uid, gid, ui_gid, members = account_ids(ctx)
    actual = unit_contract(ctx)
    config = config_contract(ctx)
    local_conflicts(ctx)
    flatpak_profiles(ctx)
    pid = int(actual["MainPID"])
    require(pid > 0, "bridge not active: no MainPID/token yet; cold socket activation is not a completed live preflight")
    require(os.readlink(ctx.path("/proc/"+str(pid)+"/exe")) == BINARY, "running bridge executable path mismatch")
    require(digest(ctx.path("/proc/"+str(pid)+"/exe")) == digest(ctx.path(BINARY)), "running bridge executable hash mismatch")
    process_status = dict(line.split(":", 1) for line in ctx.path("/proc/"+str(pid)+"/status").read_text().splitlines() if ":" in line)
    require(process_status.get("Uid", "").split() == [str(uid)]*4 and process_status.get("Gid", "").split() == [str(gid)]*4,
            "running bridge UID/GID differs from named service account")
    require(set(process_status.get("Groups", "").split()) <= {str(gid)} and int(process_status.get("CapEff", "-1"), 16) == 0 and process_status.get("NoNewPrivs", "").strip() == "1",
            "running bridge capabilities/groups/no-new-privileges drift")
    legacy_absent(ctx, pid)
    daemon = json.loads(ctx.path(DAEMON_MANIFEST).read_text())
    daemon_props = ctx.props("opensnitch.service", ["MainPID", "ExecStart"])
    daemon_pid = int(daemon_props["MainPID"])
    require(daemon_pid > 0, "patched daemon not active: no MainPID")
    daemon_exec_contract(daemon_props["ExecStart"])
    require(os.readlink(ctx.path("/proc/"+str(daemon_pid)+"/exe")) == DAEMON, "running patched daemon executable path mismatch")
    require(digest(ctx.path("/proc/"+str(daemon_pid)+"/exe")) == daemon["binary"]["sha256"], "running patched daemon executable hash mismatch")
    require(ctx.path("/proc/"+str(daemon_pid)+"/cmdline").read_bytes() == DAEMON.encode()+b"\0", "running patched daemon command-line config/IPC/policy override")
    daemon_status = dict(line.split(":", 1) for line in ctx.path("/proc/"+str(daemon_pid)+"/status").read_text().splitlines() if ":" in line)
    require(daemon_status.get("Uid", "").split() == ["0"]*4, "running patched daemon must use the root NFQUEUE account")
    states = service_state(ctx)
    require(states[SERVICE]["active"] == "active" and states["opensnitch.service"]["active"] == "active", "bridge/OpenSnitch service not active")
    require(all(states[unit]["active"] == "active" and states[unit]["enabled"] == "enabled" for unit in SOCKETS), "system socket units must be persistently enabled and active")
    paths = (("/run/snitchwatch", stat.S_ISDIR, 0, 0, 0o711),
             ("/run/snitchwatch/opensnitchd.sock", stat.S_ISSOCK, 0, 0, 0o600),
             ("/run/snitchwatch/bridge.sock", stat.S_ISSOCK, 0, ui_gid, 0o660),
             ("/run/snitchwatch-auth", stat.S_ISDIR, uid, ui_gid, 0o2750),
             ("/run/snitchwatch-auth/token", stat.S_ISREG, uid, ui_gid, 0o640))
    for path, kind, owner, group, mode in paths:
        info = ctx.stat(path)
        require(kind(info.st_mode) and info.st_uid == owner and info.st_gid == group and stat.S_IMODE(info.st_mode) == mode, "runtime DAC/type mismatch: "+path)
        require(not {"system.posix_acl_access", "system.posix_acl_default"}&set(os.listxattr(ctx.path(path))), "unexpected runtime ACL grant: "+path)
    return dict(profile="system", bridgePid=pid, daemonPid=daemon_pid, daemonPatchSha256=daemon["patch"]["sha256"], uiGroup="snitchwatch-ui", explicitUiMembers=members.split(",") if members else [], defaultAction="deny (operator opt-in)" if config["DefaultAction"] == "deny" else "allow", procMonitorMethod="proc", graphicalRoundTrip="not checked")
