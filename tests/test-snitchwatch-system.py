#!/usr/bin/env python3
"""Behavioral fixtures for fixed-path runtime checks and operator transactions."""
import copy
import importlib.util
import importlib.machinery
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
LIBEXEC = REPO/"system_files/usr/libexec"


def load(name, path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load("common_test", LIBEXEC/"bazzite-tower-snitchwatch-common.py")
migration = load("migration_test", LIBEXEC/"bazzite-tower-snitchwatch-migrate")
migration.common = common
BASE_GLOBAL_CONTENT = '[Context]\nfilesystems=xdg-config/gtk-3.0:ro;xdg-config/gtk-4.0:ro;xdg-config/MangoHud:create;xdg-config/vkBasalt:create;\n'
REAL_READLINK = os.readlink
VENDOR_CONTENT = '# This file is part of the systemd package.\n# See https://fedoraproject.org/wiki/Changes/Shorter_Shutdown_Timer.\n#\n# To facilitate debugging when a service fails to stop cleanly,\n# TimeoutStopFailureMode=abort is set to "crash" services that fail to stop in\n# the time allotted. This will cause the service to be terminated with SIGABRT\n# and a coredump to be generated.\n#\n# To undo this configuration change, create a mask file:\n#   sudo mkdir -p /etc/systemd/system/service.d\n#   sudo ln -sv /dev/null /etc/systemd/system/service.d/10-timeout-abort.conf\n\n[Service]\nTimeoutStopFailureMode=abort\n'


class Fixture(common.Context):
    def __init__(self, root):
        super().__init__(root, self.run)
        self.commands = []
        self.uid, self.gid, self.ui_gid = 731, 732, 884
        self.owners = {}
        self.fail_start = None
        self.drift_on_start = False
        self.tcp = ""
        self.unix = ""
        self.global_legacy = "masked"
        self.daemon_proof_failure = False
        self.daemon_version = "1.8.0"
        self.daemon_process_path = common.DAEMON
        self.socket_types = {}
        self.states = {unit: dict(enabled="enabled" if unit in common.SOCKETS or unit == "opensnitch.service" else "static", active="active" if unit in common.SOCKETS or unit == "opensnitch.service" else "inactive") for unit in common.UNITS}
        self.properties = {
            common.SERVICE: dict(User="snitchwatch", Group="snitchwatch", NoNewPrivileges="yes", CapabilityBoundingSet="", AmbientCapabilities="",
                ProtectSystem="strict", ProtectHome="yes", PrivateTmp="yes", PrivateDevices="yes", ProtectKernelTunables="yes",
                ProtectKernelModules="yes", ProtectControlGroups="yes", ReadWritePaths="/run/snitchwatch-auth", StateDirectory="snitchwatch",
                StateDirectoryMode="0700", SupplementaryGroups="", EnvironmentFiles="", RootDirectory="", RootImage="", BindPaths="", BindReadOnlyPaths="",
                FragmentPath="/usr/lib/systemd/system/"+common.SERVICE, DropInPaths="", ExecStartPre="", ExecStartPost="", ExecStop="", ExecStopPost="", ExecCondition="",
                ExecStart="{ path=/usr/bin/snitchwatch-bridge-cli ; argv[]=/usr/bin/snitchwatch-bridge-cli ; ignore_errors=no ; }",
                Environment="SNITCHWATCH_SYSTEM_BRIDGE=1 SNITCHWATCH_WS_SOCKET=/run/snitchwatch/bridge.sock SNITCHWATCH_WS_TOKEN_PATH=/run/snitchwatch-auth/token HOME=/var/lib/snitchwatch XDG_STATE_HOME=/var/lib",
                TriggeredBy=" ".join(common.SOCKETS), RestrictAddressFamilies="AF_UNIX AF_INET AF_INET6", MainPID="0"),
            "opensnitch.service": dict(MainPID="501", ExecStart="{ path=/usr/bin/opensnitchd ; argv[]=/usr/bin/opensnitchd ; ignore_errors=no ; }", WorkingDirectory="/run/snitchwatch", Requires=common.SOCKETS[0]+" network.target", After=common.SOCKETS[0]+" network.target")}
        for unit, path, group, mode in ((common.SOCKETS[0], "/run/snitchwatch/opensnitchd.sock", "root", "0600"), (common.SOCKETS[1], "/run/snitchwatch/bridge.sock", "snitchwatch-ui", "0660")):
            self.properties[unit] = dict(Listen=path+" (Stream)", SocketUser="root", SocketGroup=group, SocketMode=mode, Accept="no", Triggers=common.SERVICE, FragmentPath="/usr/lib/systemd/system/"+unit, DropInPaths="")
        self.write(common.PROFILE, "system\n")
        self.write(common.BINARY, "reviewed released bridge executable\n", 0o755)
        mandatory = [common.BINARY, common.REFERENCE, "/usr/lib/sysusers.d/snitchwatch.conf", "/usr/lib/tmpfiles.d/snitchwatch.conf", "/usr/lib/systemd/system/opensnitch.service.d/20-system-bridge.conf"]
        mandatory += ["/usr/lib/systemd/system/"+unit for unit in (common.SERVICE, *common.SOCKETS)]
        for name in mandatory:
            if name != common.BINARY:
                self.write(name, "installed reviewed asset\n")
        legacy_text = (REPO/"system_files/usr/share/bazzite-tower/opensnitchd-default-config.json").read_text()
        self.write(common.LEGACY_REFERENCE, legacy_text)
        system = json.loads(legacy_text);system["Server"]["Address"] = "unix:opensnitchd.sock"
        self.write(common.REFERENCE, json.dumps(system, indent=4)+"\n")
        self.write(common.CONFIG, legacy_text)
        self.write(common.PINS, json.dumps(dict(sourceCommit="d09defc8ea3d55b335c746248a4d90743ae014e3", submoduleCommit="b404c4c6316760fa7bc415509d3f8d747f7dc9cc", bridgeVersion="0.1.1")))
        mandatory.extend((common.PINS, common.LEGACY_REFERENCE))
        self.refresh_manifest(mandatory)
        self.write(common.DAEMON, "reviewed patched GPL daemon executable\n", 0o755)
        self.write(common.DAEMON_CANDIDATE, self.path(common.DAEMON).read_text(), 0o755)
        self.write(common.DAEMON_PATCH, "reviewed14file repair patch\n")
        self.daemon_manifest = dict(schemaVersion=1, profile="system", source=dict(commit=self.manifest["source"]["submoduleCommit"]),
            binary=dict(path=common.DAEMON, candidatePath=common.DAEMON_CANDIDATE, sha256=common.digest(self.path(common.DAEMON)), version="1.8.0"),
            patch=dict(path=common.DAEMON_PATCH, sha256=common.digest(self.path(common.DAEMON_PATCH))))
        self.write(common.DAEMON_MANIFEST, json.dumps(self.daemon_manifest))
        self.daemon_proof = dict(profile="system", sourceCommit=self.daemon_manifest["source"]["commit"], patchSha256=self.daemon_manifest["patch"]["sha256"],
            binaryPath=common.DAEMON, binarySha256=self.daemon_manifest["binary"]["sha256"], installedBinaryChecked=True, verifiedFiles=8)
        self.path("/proc").mkdir()
        proc = self.path("/proc/501");proc.mkdir();(proc/"exe").symlink_to("../../usr/bin/opensnitchd")
        (proc/"cmdline").write_bytes(common.DAEMON.encode()+b"\0")
        (proc/"status").write_text("Uid:\t0\t0\t0\t0\n")
        self.path("/var/home/gate").mkdir(parents=True)
        self.path("/run/snitchwatch").mkdir(parents=True);self.path("/run/snitchwatch").chmod(0o711)
        self.path("/run/snitchwatch-auth").mkdir();self.path("/run/snitchwatch-auth").chmod(0o2750)
        self.owners["/run/snitchwatch-auth"] = (self.uid, self.ui_gid)
        self.write("/run/snitchwatch-auth/token", "never logged secret\n", 0o640)
        self.owners["/run/snitchwatch-auth/token"] = (self.uid, self.ui_gid)
        for name, group, mode in (("opensnitchd.sock", 0, 0o600), ("bridge.sock", self.ui_gid, 0o660)):
            path = "/run/snitchwatch/"+name
            self.write(path, "mock socket metadata\n", mode)
            self.socket_types[path] = stat.S_IFSOCK
            self.owners[path] = (0, group)

    def close(self):
        pass

    def write(self, name, text, mode=0o644):
        path = self.path(name);path.parent.mkdir(parents=True, exist_ok=True);path.write_text(text);path.chmod(mode)
        return path

    def refresh_manifest(self, files=None):
        if files is None:
            files = self.manifest["files"]
        self.manifest = dict(schemaVersion=1, profile="system", source=dict(commit="d09defc8ea3d55b335c746248a4d90743ae014e3", submoduleCommit="b404c4c6316760fa7bc415509d3f8d747f7dc9cc"),
            binary=dict(path=common.BINARY, sha256=common.digest(self.path(common.BINARY)), version="0.1.1"), files={path:common.digest(self.path(path)) for path in files},
            flatpak=dict(appId=common.APP, profile="system", sourceCommit="d09defc8ea3d55b335c746248a4d90743ae014e3", runtimeRef="org.kde.Platform/x86_64/6.9", manifestSha256="a"*64))
        self.write(common.MANIFEST, json.dumps(self.manifest))

    def stat(self, name):
        info = super().stat(name)
        uid, gid = self.owners.get(name, (0, 0))
        mode = stat.S_IMODE(info.st_mode)|self.socket_types[name] if name in self.socket_types else info.st_mode
        return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)

    def bridge(self, active):
        self.states[common.SERVICE]["active"] = "active" if active else "inactive"
        self.properties[common.SERVICE]["MainPID"] = "420" if active else "0"
        proc = self.path("/proc/420")
        if active and not proc.exists():
            proc.mkdir();(proc/"exe").symlink_to("../../usr/bin/snitchwatch-bridge-cli");(proc/"cmdline").write_bytes(common.BINARY.encode()+b"\0")
        if active:
            (proc/"status").write_text(f"Uid:\t{self.uid}\t{self.uid}\t{self.uid}\t{self.uid}\nGid:\t{self.gid}\t{self.gid}\t{self.gid}\t{self.gid}\nGroups:\t{self.gid}\nCapEff:\t0000000000000000\nNoNewPrivs:\t1\n")
        elif proc.exists():
            (proc/"exe").unlink();(proc/"cmdline").unlink();(proc/"status").unlink();proc.rmdir()

    def link(self, path, *args, **kwargs):
        if Path(path) == self.path("/proc/501/exe"):
            return self.daemon_process_path
        if Path(path) == self.path("/proc/420/exe"):
            return common.BINARY
        return REAL_READLINK(path, *args, **kwargs)

    def system_config(self):
        self.write(common.CONFIG, self.path(common.REFERENCE).read_text());self.bridge(True)

    def flatpak(self, metadata=None):
        text = metadata or """[Application]
name=org.snitchwatch.Snitchwatch
runtime=org.kde.Platform/x86_64/6.9
[Context]
shared=ipc;
sockets=wayland;fallback-x11;
devices=dri;
filesystems=/run/snitchwatch:ro;/run/snitchwatch-auth:ro;xdg-config/snitchwatch:create;xdg-config/kdeglobals:ro;
persistent=.local/share/snitchwatch;.local/state/snitchwatch;
[Environment]
SNITCHWATCH_SYSTEM_BRIDGE=1
[Session Bus Policy]
org.freedesktop.Notifications=talk
org.kde.StatusNotifierWatcher=talk
"""
        return self.write("/var/home/gate/.local/share/flatpak/app/"+common.APP+"/x86_64/master/active/metadata", text)

    def run(self, argv, **_kwargs):
        self.commands.append(argv)
        out = "";code = 0;err = ""
        if argv == [common.BINARY, "--version"]:
            out = getattr(self, "version", "snitchwatch-bridge-cli 0.1.1")+"\n"
        elif argv[:2] == ["python3", "/usr/libexec/snitchwatch/verify-system-manifest.py"]:
            out = "{}\n"
        elif argv == ["python3", "/usr/libexec/snitchwatch/verify-system-daemon.py"]:
            out = json.dumps(self.daemon_proof)+"\n"
            if self.daemon_proof_failure:
                code=1;error="installed daemon provenance mismatch"
        elif argv == [common.DAEMON, "-version"]:
            out = self.daemon_version+"\n"
        elif argv == ["flatpak", "--installations"]:
            out = "/var/lib/flatpak\n"
        elif argv[:2] == ["getent", "passwd"]:
            service = f"snitchwatch:x:{self.uid}:{self.gid}:Bridge:/var/lib/snitchwatch:/usr/sbin/nologin"
            out = service+"\n" if len(argv)==3 else service+"\nroot:x:0:0:Root:/root:/bin/bash\ngate:x:1000:1000:Gate:/var/home/gate:/bin/bash\n"
        elif argv[:2] == ["getent", "group"]:
            out = f"snitchwatch:x:{self.gid}:\n" if argv[2] == "snitchwatch" else f"snitchwatch-ui:x:{self.ui_gid}:gate\n"
        elif argv[:2] == ["systemctl", "show"]:
            names = argv[-1].split("=",1)[1].split(",")
            out = "".join(name+"="+self.properties[argv[2]][name]+"\n" for name in names
                          if name in self.properties[argv[2]] and ("--all" in argv or self.properties[argv[2]][name] != ""))
        elif argv[:3] == ["systemctl", "--global", "is-enabled"]:
            out = self.global_legacy+"\n";code = 1 if self.global_legacy == "masked" else 0
        elif argv[:2] == ["systemctl", "is-enabled"]:
            out = self.states[argv[2]]["enabled"]+"\n"
        elif argv[:2] == ["systemctl", "is-active"]:
            out = self.states[argv[2]]["active"]+"\n";code = 0 if out.strip()=="active" else 3
        elif argv[:2] in (["systemctl", "start"], ["systemctl", "stop"]):
            action = argv[1]
            if action == "start" and self.fail_start in argv[2:]:
                self.fail_start = None;code = 1;err = "injected activation failure"
                if self.drift_on_start:
                    value = json.loads(self.path(common.CONFIG).read_text());value["DefaultAction"]="deny";self.write(common.CONFIG,json.dumps(value))
            else:
                for unit in argv[2:]:
                    self.states[unit]["active"] = "active" if action=="start" else "inactive"
                    if unit == common.SERVICE:
                        self.bridge(action=="start")
        elif argv[:3] == ["ss", "-H", "-ltnp"]:
            out = self.tcp
        elif argv[:3] == ["ss", "-H", "-lxnp"]:
            out = self.unix
        else:
            raise AssertionError("unexpected command "+repr(argv))
        return subprocess.CompletedProcess(argv, code, out, err)


class SystemBehavior(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sw-fixture-")
        self.ctx = Fixture(Path(self.temp.name))
        self.link = mock.patch.object(common.os, "readlink", side_effect=self.ctx.link);self.link.start()
        self.chown = mock.patch.object(migration.os, "fchown");self.chown.start()

    def tearDown(self):
        self.chown.stop();self.link.stop();self.ctx.close();self.temp.cleanup()

    def refuse_readiness(self, text):
        with self.assertRaisesRegex(common.Refusal, text):
            common.runtime_readiness(self.ctx)
        self.assertFalse(any(cmd[:2] in (["systemctl","start"], ["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_known_missing_structured_arrays_require_typed_empty_dbus(self):
        for name, signature in common.TYPED_EMPTY_PROPERTIES.items():
            commands = []
            def runner(argv, **kwargs):
                commands.append(argv)
                if argv[0] == "systemctl":
                    output = ""
                elif "GetUnit" in argv:
                    output = json.dumps(dict(type="o", data=["/org/freedesktop/systemd1/unit/bridge"]))
                else:
                    self.assertEqual(argv[-1], name)
                    output = json.dumps(dict(type=signature, data=[]))
                return SimpleNamespace(returncode=0, stderr="", stdout=output)
            with self.subTest(name=name):
                ctx = common.Context(runner=runner)
                self.assertEqual(ctx.props(common.SERVICE, [name]), {name: ""})
                self.assertIn("--all", commands[0])
                self.assertEqual(commands[1][-3:], ["GetUnit", "s", common.SERVICE])
                self.assertIn("--system", commands[2])

    def test_structured_fallback_wrong_type_nonempty_malformed_or_error_refuses(self):
        for name, signature in common.TYPED_EMPTY_PROPERTIES.items():
            for invalid in [dict(type="as", data=[]), dict(type=signature, data=[["/tmp/unsafe"]]),
                            dict(type=signature, data=None), {}, dict(type=signature, data=[], extra=True), "invalid-json", "bus-error"]:
                def runner(argv, **kwargs):
                    if argv[0] == "systemctl":
                        value = ""
                    elif "GetUnit" in argv:
                        value = json.dumps(dict(type="o", data=["/org/freedesktop/systemd1/unit/bridge"]))
                    else:
                        value = invalid if isinstance(invalid, str) else json.dumps(invalid)
                    return SimpleNamespace(returncode=1 if value == "bus-error" else 0, stderr="failed" if value == "bus-error" else "", stdout=value)
                with self.subTest(name=name, invalid=invalid):
                    with self.assertRaises(common.Refusal):
                        common.Context(runner=runner).props(common.SERVICE, [name])

    def test_typed_getunit_invalid_identity_or_error_refuses(self):
        for invalid in [dict(type="s", data=["/org/freedesktop/systemd1/unit/bridge"]), dict(type="o", data="/org/freedesktop/systemd1/unit/bridge"),
                        dict(type="o", data=[]), dict(type="o", data=["/wrong/object"]), dict(type="o", data=["/org/freedesktop/systemd1/unit/a", "/org/freedesktop/systemd1/unit/b"]), "invalid-json", "bus-error"]:
            def runner(argv, **kwargs):
                value = "" if argv[0] == "systemctl" else invalid if isinstance(invalid, str) else json.dumps(invalid)
                return SimpleNamespace(returncode=1 if value == "bus-error" else 0, stderr="failure" if value == "bus-error" else "", stdout=value)
            with self.subTest(invalid=invalid):
                with self.assertRaises(common.Refusal):
                    common.Context(runner=runner).props(common.SERVICE, ["ExecCondition"])

    def test_unknown_missing_property_never_gets_typed_empty_fallback(self):
        commands = []
        def runner(argv, **kwargs):
            commands.append(argv)
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        for unit, names in [(common.SERVICE, ["CapabilityBoundingSet"]), (common.SERVICE, ["ExecStartPre", "User"]), ("unreviewed.service", ["ExecStop"])]:
            with self.subTest(unit=unit, names=names), self.assertRaises(common.Refusal):
                common.Context(runner=runner).props(unit, names)
        self.assertTrue(all(argv[0] == "systemctl" for argv in commands))

    def test_pinned_fedora_vendor_dropin_accepted(self):
        self.ctx.write(common.VENDOR_DROPIN, VENDOR_CONTENT)
        self.assertEqual(common.digest(self.ctx.path(common.VENDOR_DROPIN)), common.VENDOR_DROPIN_SHA256)
        self.ctx.properties[common.SERVICE]["DropInPaths"] = common.VENDOR_DROPIN
        common.unit_contract(self.ctx)

    def test_vendor_dropin_mutable_bytes_owner_symlink_and_extra_paths_refuse(self):
        for fault in ["bytes", "owner", "group", "writable", "symlink", "duplicate", "extra", "different"]:
            with self.subTest(fault=fault):
                path = self.ctx.write(common.VENDOR_DROPIN, VENDOR_CONTENT)
                self.ctx.owners[common.VENDOR_DROPIN] = (0, 0)
                self.ctx.properties[common.SERVICE]["DropInPaths"] = common.VENDOR_DROPIN
                if fault == "bytes": path.write_text(VENDOR_CONTENT+"# changed\n")
                elif fault == "owner": self.ctx.owners[common.VENDOR_DROPIN] = (1000, 0)
                elif fault == "group": self.ctx.owners[common.VENDOR_DROPIN] = (0, 1000)
                elif fault == "writable": path.chmod(0o664)
                elif fault == "symlink":
                    other = self.ctx.write("/tmp/vendor-copy", VENDOR_CONTENT);path.unlink();path.symlink_to(other)
                elif fault == "duplicate": self.ctx.properties[common.SERVICE]["DropInPaths"] += " "+common.VENDOR_DROPIN
                elif fault == "extra": self.ctx.properties[common.SERVICE]["DropInPaths"] += " /etc/systemd/system/service.d/unsafe.conf"
                elif fault == "different": self.ctx.properties[common.SERVICE]["DropInPaths"] = "/usr/lib/systemd/system/service.d/unreviewed.conf"
                with self.assertRaises(common.Refusal): common.unit_contract(self.ctx)
                if path.is_symlink():path.unlink()

    def test_typewide_local_service_overrides_refuse(self):
        for directory in ["/etc/systemd/system/service.d", "/run/systemd/system/service.d", "/usr/local/lib/systemd/system/service.d"]:
            with self.subTest(directory=directory):
                self.ctx.path(directory).mkdir(parents=True)
                with self.assertRaises(common.Refusal):common.local_conflicts(self.ctx)
                self.ctx.path(directory).rmdir()

    def test_native_property_request_keeps_explicit_empty_values(self):
        commands = []
        def actual_serialization(argv, **kwargs):
            commands.append(argv)
            return SimpleNamespace(returncode=0, stderr="", stdout="CapabilityBoundingSet=\n" if "--all" in argv else "")
        ctx = common.Context(runner=actual_serialization)
        self.assertEqual(ctx.props(common.SERVICE, ["CapabilityBoundingSet"]), {"CapabilityBoundingSet": ""})
        self.assertIn("--all", commands[0])

    def test_missing_property_still_refuses_even_with_all(self):
        ctx = common.Context(runner=lambda argv, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""))
        with self.assertRaisesRegex(common.Refusal, "missing effective systemd properties"):
            ctx.props(common.SERVICE, ["CapabilityBoundingSet"])

    def test_valid_live_state_is_read_only_and_named_ids(self):
        self.ctx.system_config();self.ctx.flatpak()
        report = common.runtime_readiness(self.ctx)
        self.assertEqual(report["explicitUiMembers"], ["gate"])
        self.assertEqual(report["defaultAction"], "allow")
        self.assertFalse(any(cmd[:2] in (["systemctl","start"], ["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_different_named_ids_not_hardcoded(self):
        self.ctx.system_config();self.ctx.uid,self.ctx.gid,self.ctx.ui_gid=901,902,777
        self.ctx.bridge(True)
        self.ctx.owners["/run/snitchwatch/bridge.sock"]=(0,777)
        for path in ("/run/snitchwatch-auth", "/run/snitchwatch-auth/token"):
            self.ctx.owners[path]=(901,777)
        self.assertEqual(common.runtime_readiness(self.ctx)["bridgePid"],420)

    def test_installed_version_0_1_0_rejected(self):
        self.ctx.system_config();self.ctx.version="snitchwatch-bridge-cli 0.1.0"
        self.refuse_readiness("version must be 0.1.1")

    def test_running_uid_and_capability_drift(self):
        self.ctx.system_config();path=self.ctx.path("/proc/420/status");text=path.read_text()
        path.write_text(text.replace("Uid:\t731", "Uid:\t0"));self.refuse_readiness("UID/GID")
        path.write_text(text.replace("0000000000000000", "0000000000000001"));self.refuse_readiness("capabilities")

    def test_binary_hash_drift(self):
        self.ctx.system_config();self.ctx.path(common.BINARY).write_text("foreign binary")
        self.refuse_readiness("hash mismatch")

    def test_source_profile_wrong(self):
        self.ctx.system_config();self.ctx.write(common.PROFILE,"legacy\n")
        self.refuse_readiness("profile is not system")

    def test_effective_exec_override(self):
        self.ctx.system_config();self.ctx.properties[common.SERVICE]["ExecStart"] += "{ path=/tmp/bridge ; argv[]=/tmp/bridge ; }"
        self.refuse_readiness("ExecStart")

    def test_hardening_and_socket_properties(self):
        self.ctx.system_config()
        for property,value in (("CapabilityBoundingSet","cap_net_admin"),("AmbientCapabilities","cap_net_raw"),("NoNewPrivileges","no"),("ProtectSystem","full"),("ReadWritePaths","/run /etc"),("EnvironmentFiles","/tmp/override"),("DropInPaths","/run/systemd/system/unsafe.conf")):
            saved = self.ctx.properties[common.SERVICE][property]
            self.ctx.properties[common.SERVICE][property]=value
            self.refuse_readiness("property drift")
            self.ctx.properties[common.SERVICE][property]=saved
        self.ctx.properties[common.SOCKETS[1]]["SocketMode"]="0666"
        self.refuse_readiness("socket contract drift")

    def test_dac_wrong_owner_mode_and_symlink(self):
        self.ctx.system_config()
        path="/run/snitchwatch-auth/token";self.ctx.owners[path]=(0,self.ctx.ui_gid)
        self.refuse_readiness("DAC/type mismatch")
        self.ctx.owners[path]=(self.ctx.uid,self.ctx.ui_gid);self.ctx.path(path).chmod(0o644)
        self.refuse_readiness("DAC/type mismatch")
        self.ctx.path(path).unlink();self.ctx.path(path).symlink_to(self.ctx.path(common.BINARY))
        self.refuse_readiness("DAC/type mismatch")

    def test_socket_is_not_regular_file(self):
        self.ctx.system_config();self.ctx.socket_types["/run/snitchwatch/bridge.sock"] = stat.S_IFREG
        self.refuse_readiness("DAC/type mismatch")

    def test_config_address_and_policy_drift(self):
        self.ctx.system_config()
        for key,value in (("DefaultAction","deny"),("ProcMonitorMethod","ebpf")):
            config=json.loads(self.ctx.path(common.REFERENCE).read_text());config[key]=value;self.ctx.write(common.CONFIG,json.dumps(config))
            self.refuse_readiness("drift")
        self.ctx.write(common.CONFIG,self.ctx.path(common.LEGACY_REFERENCE).read_text())
        self.refuse_readiness("Server.Address drift")

    def test_listener_and_legacy_process_conflicts(self):
        self.ctx.system_config();self.ctx.tcp='LISTEN 0 128 127.0.0.1:50051 0.0.0.0:* users:(("legacy",pid=999,fd=3))\n'
        self.refuse_readiness("legacy TCP listener")
        self.ctx.tcp="";self.ctx.unix='u_str LISTEN /run/user/1000/snitchwatch/bridge.sock\n'
        self.refuse_readiness("legacy per-user")
        self.ctx.unix="";proc=self.ctx.path("/proc/999");proc.mkdir();(proc/"exe").symlink_to("../../usr/bin/snitchwatch-bridge-cli");(proc/"cmdline").write_bytes(b'/home/gate/.local/bin/snitchwatch-bridge-cli\0')
        self.refuse_readiness("competing bridge process")

    def test_legacy_enabled_and_local_override(self):
        self.ctx.system_config();self.ctx.global_legacy="enabled"
        self.refuse_readiness("legacy global")
        self.ctx.global_legacy="masked"
        self.ctx.write("/var/home/gate/.config/systemd/user/default.target.wants/snitchwatch-bridge.service", "enabled")
        self.refuse_readiness("enabled legacy")

    def test_all_default_user_unit_load_paths_refuse(self):
        self.ctx.system_config()
        paths = ["/var/home/gate/.local/share/systemd/user", "/var/home/gate/.config/systemd/user.control",
                 "/run/user/1000/systemd/user", "/run/user/1000/systemd/user.control", "/run/user/1000/systemd/transient",
                 "/run/user/1000/systemd/generator.early", "/run/user/1000/systemd/generator", "/run/user/1000/systemd/generator.late",
                 "/usr/local/lib/systemd/user", "/usr/local/share/systemd/user", "/etc/xdg/systemd/user", "/run/systemd/user", "/usr/share/systemd/user"]
        for directory in paths:
            with self.subTest(directory=directory):
                planted = self.ctx.write(directory+"/snitchwatch-bridge.service", "[Service]\nExecStart=/legacy\n")
                self.refuse_readiness("local unit override")
                planted.unlink()
        planted = self.ctx.write("/run/user/1000/systemd/user/default.target.requires/snitchwatch-bridge.service", "enabled")
        self.refuse_readiness("enabled legacy")
        planted.unlink()

    def test_system_control_and_generator_overrides_refuse(self):
        self.ctx.system_config()
        for directory in ("/etc/systemd/system.control", "/run/systemd/system.control", "/run/systemd/transient", "/run/systemd/generator.early", "/run/systemd/generator", "/run/systemd/generator.late", "/usr/local/lib/systemd/system"):
            with self.subTest(directory=directory):
                planted = self.ctx.write(directory+"/"+common.SERVICE, "[Service]\nExecStart=/unsafe\n")
                self.refuse_readiness("local unit override")
                planted.unlink()

    def test_exact_bazzite_base_global_override_without_or_with_gui(self):
        path = self.ctx.write(common.BASE_GLOBAL_OVERRIDE, BASE_GLOBAL_CONTENT)
        self.assertEqual(common.digest(path), common.BASE_GLOBAL_OVERRIDE_SHA256)
        common.flatpak_profiles(self.ctx)
        self.ctx.system_config();self.ctx.flatpak()
        common.runtime_readiness(self.ctx)

    def test_base_global_override_drift_symlink_owner_mode_and_scope_refuse(self):
        for fault in ("network", "socket", "token-env", "bytes", "symlink", "writable", "owner", "group", "user-global", "app", "other-store", "empty-user"):
            with self.subTest(fault=fault):
                path = self.ctx.write(common.BASE_GLOBAL_OVERRIDE, BASE_GLOBAL_CONTENT)
                self.ctx.owners[common.BASE_GLOBAL_OVERRIDE] = (0, 0)
                other = None;original_runner=self.ctx.runner
                if fault == "network":path.write_text(BASE_GLOBAL_CONTENT+"shared=network;\n")
                elif fault == "socket":path.write_text(BASE_GLOBAL_CONTENT+"filesystems=/run/snitchwatch:rw;\n")
                elif fault == "token-env":path.write_text(BASE_GLOBAL_CONTENT+"\n[Environment]\nSNITCHWATCH_WS_TOKEN_PATH=/tmp/token\n")
                elif fault == "bytes":path.write_text(BASE_GLOBAL_CONTENT+"\n")
                elif fault == "symlink":
                    other=self.ctx.write("/tmp/base-global-copy", BASE_GLOBAL_CONTENT);path.unlink();path.symlink_to(other)
                elif fault == "writable":path.chmod(0o666)
                elif fault == "owner":self.ctx.owners[common.BASE_GLOBAL_OVERRIDE]=(1000,0)
                elif fault == "group":self.ctx.owners[common.BASE_GLOBAL_OVERRIDE]=(0,1000)
                elif fault in ("user-global", "empty-user"):
                    other=self.ctx.write("/var/home/gate/.local/share/flatpak/overrides/global", "" if fault=="empty-user" else BASE_GLOBAL_CONTENT)
                elif fault == "app":other=self.ctx.write("/var/lib/flatpak/overrides/"+common.APP, BASE_GLOBAL_CONTENT)
                elif fault == "other-store":
                    other=self.ctx.write("/srv/extra-flatpak/overrides/global", BASE_GLOBAL_CONTENT)
                    def installations(argv, **kwargs):
                        if argv == ["flatpak", "--installations"]:
                            return SimpleNamespace(returncode=0, stderr="", stdout="/var/lib/flatpak\n/srv/extra-flatpak\n")
                        return original_runner(argv, **kwargs)
                    self.ctx.runner=installations
                with self.assertRaises(common.Refusal):common.flatpak_profiles(self.ctx)
                self.ctx.runner=original_runner
                if other is not None:other.unlink()
                if path.is_symlink():path.unlink()

    def test_flatpak_mismatch_and_overrides(self):
        self.ctx.system_config();path=self.ctx.flatpak()
        text=path.read_text()
        for changed in (text.replace("SNITCHWATCH_SYSTEM_BRIDGE=1","SNITCHWATCH_SYSTEM_BRIDGE=0"), text.replace("shared=ipc;","shared=ipc;network;"), text.replace("/run/snitchwatch:ro","/run/snitchwatch:rw"), text+"\n[System Bus Policy]\norg.freedesktop.systemd1=talk\n"):
            path.write_text(changed);self.refuse_readiness("same-ID Flatpak")
        path.write_text(text);self.ctx.write("/var/home/gate/.local/share/flatpak/overrides/"+common.APP,"[Context]\nshared=network;\n")
        self.refuse_readiness("Flatpak overrides")

    def test_flatpak_environment_cannot_redirect_system_ipc(self):
        self.ctx.system_config();path=self.ctx.flatpak();original=path.read_text()
        for name, expected, bad in (("SNITCHWATCH_WS_SOCKET", "/run/snitchwatch/bridge.sock", "/run/user/1000/snitchwatch/bridge.sock"), ("SNITCHWATCH_WS_TOKEN_PATH", "/run/snitchwatch-auth/token", "/tmp/legacy-token")):
            for value in (bad, ""):
                with self.subTest(name=name,value=value):
                    path.write_text(original.replace("SNITCHWATCH_SYSTEM_BRIDGE=1", "SNITCHWATCH_SYSTEM_BRIDGE=1\n"+name+"="+value))
                    self.refuse_readiness("redirects protected system IPC")
            path.write_text(original.replace("SNITCHWATCH_SYSTEM_BRIDGE=1", "SNITCHWATCH_SYSTEM_BRIDGE=1\n"+name+"="+expected+"\nQT_QUICK_CONTROLS_STYLE=Basic"))
            common.runtime_readiness(self.ctx)
        path.write_text(original)

    def test_extended_runtime_acl_refuses_outsider_grants(self):
        self.ctx.system_config()
        def xattrs(path):
            return ["system.posix_acl_access"] if Path(path)==self.ctx.path("/run/snitchwatch-auth") else []
        with mock.patch.object(common.os,"listxattr",side_effect=xattrs):
            self.refuse_readiness("runtime ACL")

    def test_migration_conflicts_refuse_before_any_mutation(self):
        cases=("enabled", "override", "flatpak", "listener", "user-unit")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="sw-conflict-") as root:
                ctx=Fixture(Path(root))
                try:
                    if case=="enabled":
                        ctx.global_legacy="enabled"
                    elif case=="override":
                        ctx.write("/run/systemd/system/opensnitch.service.d/operator.conf","[Service]\nEnvironment=DRIFT=1\n")
                    elif case=="flatpak":
                        path=ctx.flatpak();path.write_text(path.read_text().replace("SNITCHWATCH_SYSTEM_BRIDGE=1","SNITCHWATCH_SYSTEM_BRIDGE=0"))
                    elif case=="listener":
                        ctx.tcp="LISTEN 127.0.0.1:50051"
                    else:
                        ctx.write("/var/home/gate/.config/systemd/user/snitchwatch-bridge.service","[Service]\nExecStart=/legacy\n")
                    with self.assertRaises(common.Refusal):
                        migration.Migration(ctx).apply()
                    self.assertFalse(ctx.path(migration.STATE).exists())
                    self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in ctx.commands))
                finally:
                    ctx.close()

    def test_interrupted_prepared_journal_restores_known_baseline(self):
        operator=migration.Migration(self.ctx);original=self.ctx.path(common.CONFIG).read_bytes();states=copy.deepcopy(self.ctx.states)
        record=operator.apply();record["status"]="prepared";operator.save(record)
        self.assertEqual(operator.rollback()["status"],"rolled-back")
        self.assertEqual(self.ctx.path(common.CONFIG).read_bytes(),original);self.assertEqual(self.ctx.states,states)

    def test_corrupt_recorded_config_refuses_recovery(self):
        operator=migration.Migration(self.ctx);record=operator.apply();record["configSha256"]="0"*64;operator.save(record)
        count=len(self.ctx.commands)
        with self.assertRaisesRegex(common.Refusal,"corrupted"):
            operator.rollback()
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands[count:]))

    def test_missing_privileged_proc_visibility_refuses(self):
        self.ctx.system_config();proc=self.ctx.path("/proc/999");proc.mkdir();(proc/"exe").symlink_to("../../usr/bin/snitchwatch-bridge-cli")
        with mock.patch.object(common.os,"readlink",side_effect=PermissionError("no permission")):
            with self.assertRaises((PermissionError, common.Refusal)):
                common.runtime_readiness(self.ctx)

    def test_apply_and_exact_rollback_preserve_policy_and_service_baseline(self):
        original=self.ctx.path(common.CONFIG).read_bytes();states=copy.deepcopy(self.ctx.states)
        operator=migration.Migration(self.ctx)
        applied=operator.apply()
        self.assertEqual(applied["status"],"applied")
        self.assertEqual(json.loads(self.ctx.path(common.CONFIG).read_text())["DefaultAction"],"allow")
        self.assertEqual(json.loads(self.ctx.path(common.CONFIG).read_text())["ProcMonitorMethod"],"proc")
        rolled=operator.rollback();self.assertEqual(rolled["status"],"rolled-back")
        self.assertEqual(self.ctx.path(common.CONFIG).read_bytes(), original)
        self.assertEqual(self.ctx.states, states)
        self.assertEqual(stat.S_IMODE(operator.current.stat().st_mode),0o600)
        self.assertFalse(any(cmd[0] in ("usermod","groupmod","systemd-sysusers") for cmd in self.ctx.commands))

    def test_read_only_migration_check_no_journal_or_unit_mutation(self):
        migration.Migration(self.ctx).check()
        self.assertFalse(self.ctx.path(migration.STATE).exists())
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_activation_failure_restores_exact_baseline(self):
        original=self.ctx.path(common.CONFIG).read_bytes();states=copy.deepcopy(self.ctx.states)
        self.ctx.fail_start=common.SERVICE
        operator=migration.Migration(self.ctx)
        with self.assertRaisesRegex(common.Refusal,"failed-restored"):
            operator.apply()
        self.assertEqual(self.ctx.path(common.CONFIG).read_bytes(),original)
        self.assertEqual(self.ctx.states,states)
        self.assertEqual(json.loads(operator.current.read_text())["status"],"failed-restored")

    def test_concurrent_policy_edit_is_not_overwritten_on_failure(self):
        self.ctx.fail_start=common.SERVICE;self.ctx.drift_on_start=True
        operator=migration.Migration(self.ctx)
        with self.assertRaisesRegex(common.Refusal,"recovery-required"):
            operator.apply()
        self.assertEqual(json.loads(self.ctx.path(common.CONFIG).read_text())["DefaultAction"],"deny")
        with self.assertRaisesRegex(common.Refusal,"rollback refuses"):
            operator.rollback()

    def publication_edit(self, edit):
        real = migration.exchange_files
        fired = False
        def exchange(left, right):
            nonlocal fired
            if not fired and Path(right) == self.ctx.path(common.CONFIG):
                fired = True
                edit()
            return real(left, right)
        return mock.patch.object(migration, "exchange_files", side_effect=exchange)

    def assert_retained_policy(self, operator, expected):
        record = json.loads(operator.current.read_text())
        self.assertEqual(record["status"], "recovery-required")
        retained = Path(record["recoveryFile"])
        self.assertEqual(retained.read_bytes(), expected)
        self.assertEqual(retained.parent, operator.directory)
        self.assertEqual(self.ctx.states["opensnitch.service"]["active"], "inactive")
        with self.assertRaisesRegex(common.Refusal, "manual recovery"):
            operator.rollback()
        return retained

    def test_patched_daemon_immutable_artifact_tampering_refuses(self):
        self.ctx.system_config()
        for name in (common.DAEMON,common.DAEMON_CANDIDATE,common.DAEMON_PATCH):
            with self.subTest(name=name):
                original=self.ctx.path(name).read_bytes();self.ctx.path(name).write_bytes(b"unreviewed artifact")
                self.refuse_readiness("patched daemon artifact hash")
                self.ctx.path(name).write_bytes(original)
        self.ctx.daemon_proof_failure=True
        self.refuse_readiness("command failed")

    def test_patched_daemon_wrong_manifest_or_staging_only_proof_refuses(self):
        self.ctx.system_config();original=copy.deepcopy(self.ctx.daemon_manifest)
        variants=[]
        for mutate in (lambda m:m["source"].update(commit="f"*40),lambda m:m["binary"].update(version="1.9.0"),lambda m:m["binary"].update(path="/tmp/daemon"),lambda m:m["patch"].update(path="/tmp/patch")):
            changed=copy.deepcopy(original);mutate(changed);variants.append(changed)
        for changed in variants:
            self.ctx.path(common.DAEMON_MANIFEST).write_text(json.dumps(changed))
            with self.assertRaises(common.Refusal):common.runtime_readiness(self.ctx)
        self.ctx.path(common.DAEMON_MANIFEST).write_text(json.dumps(original))
        self.ctx.daemon_proof["installedBinaryChecked"]=False
        self.refuse_readiness("did not establish installed patched")
        self.ctx.daemon_proof["installedBinaryChecked"]=True;self.ctx.daemon_version="1.8.0-unreviewed"
        self.refuse_readiness("patched daemon version")

    def test_actual_daemon_pid_path_hash_uid_and_exec_contract_refuse_drift(self):
        self.ctx.system_config()
        daemon=self.ctx.properties["opensnitch.service"]
        daemon["MainPID"]="0";self.refuse_readiness("daemon not active");daemon["MainPID"]="501"
        daemon["ExecStart"]="{ path=/tmp/daemon ; argv[]=/tmp/daemon ; }";self.refuse_readiness("daemon ExecStart");daemon["ExecStart"]="{ path=/usr/bin/opensnitchd ; argv[]=/usr/bin/opensnitchd ; }"
        self.ctx.daemon_process_path="/usr/bin/opensnitchd (deleted)";self.refuse_readiness("daemon executable path");self.ctx.daemon_process_path=common.DAEMON
        proc=self.ctx.path("/proc/501");(proc/"exe").unlink();old=self.ctx.write("/old-running-daemon","old unrepaired image");(proc/"exe").symlink_to(old)
        self.refuse_readiness("daemon executable hash")
        (proc/"exe").unlink();(proc/"exe").symlink_to("../../usr/bin/opensnitchd")
        (proc/"status").write_text("Uid:\t1000\t1000\t1000\t1000\n");self.refuse_readiness("root NFQUEUE account")

    def test_stopped_daemon_argv_override_refuses_before_migration(self):
        self.ctx.states["opensnitch.service"]["active"]="inactive"
        self.ctx.properties["opensnitch.service"]["MainPID"]="0"
        self.ctx.properties["opensnitch.service"]["ExecStart"]="{ path=/usr/bin/opensnitchd ; argv[]=/usr/bin/opensnitchd -config-file /tmp/deny.json ; ignore_errors=no ; }"
        original=self.ctx.path(common.CONFIG).read_bytes()
        with self.assertRaisesRegex(common.Refusal,"CLI config/IPC/policy overrides"):
            migration.Migration(self.ctx).apply()
        self.assertEqual(self.ctx.path(common.CONFIG).read_bytes(),original)
        self.assertFalse(self.ctx.path(migration.STATE).exists())
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_live_daemon_argv_only_drift_refuses(self):
        self.ctx.system_config()
        self.ctx.path("/proc/501/cmdline").write_bytes(common.DAEMON.encode()+b"\0-config-file\0/tmp/deny.json\0")
        self.refuse_readiness("command-line config/IPC/policy override")
        self.ctx.path("/proc/501/cmdline").write_bytes(common.DAEMON.encode()+b"\0")
        self.ctx.properties["opensnitch.service"]["ExecStart"]="{ path=/usr/bin/opensnitchd ; argv[]=/usr/bin/opensnitchd -ui-socket unix:/tmp/legacy.sock ; }"
        self.refuse_readiness("CLI config/IPC/policy overrides")

    def test_missing_daemon_manifest_fails_migration_before_mutation(self):
        self.ctx.path(common.DAEMON_MANIFEST).unlink()
        with self.assertRaises((FileNotFoundError,common.Refusal)):migration.Migration(self.ctx).apply()
        self.assertFalse(self.ctx.path(migration.STATE).exists())
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_operator_edit_between_preflight_and_baseline_capture_refuses(self):
        operator=migration.Migration(self.ctx);real=operator.check
        config=json.loads(self.ctx.path(common.CONFIG).read_text());config["DefaultAction"]="deny"
        edited=(json.dumps(config)+"\n").encode()
        def checked_then_edited():
            result=real()
            self.ctx.path(common.CONFIG).write_bytes(edited)
            return result
        with mock.patch.object(operator,"check",side_effect=checked_then_edited):
            with self.assertRaisesRegex(common.Refusal,"between preflight and baseline capture"):
                operator.apply()
        self.assertEqual(self.ctx.path(common.CONFIG).read_bytes(),edited)
        self.assertFalse(operator.current.exists())
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_apply_final_publication_boundary_preserves_operator_edit(self):
        operator = migration.Migration(self.ctx)
        config = json.loads(self.ctx.path(common.CONFIG).read_text());config["DefaultAction"]="deny"
        edited = (json.dumps(config)+"\n").encode()
        with self.publication_edit(lambda: self.ctx.path(common.CONFIG).write_bytes(edited)):
            with self.assertRaisesRegex(common.Refusal, "recovery-required"):
                operator.apply()
        self.assert_retained_policy(operator, edited)
        self.assertFalse(any(cmd[:2]==["systemctl","start"] for cmd in self.ctx.commands))

    def test_rollback_final_publication_boundary_preserves_operator_edit(self):
        operator = migration.Migration(self.ctx);operator.apply()
        config = json.loads(self.ctx.path(common.CONFIG).read_text());config["DefaultAction"]="deny"
        edited = (json.dumps(config)+"\n").encode()
        count = len(self.ctx.commands)
        with self.publication_edit(lambda: self.ctx.path(common.CONFIG).write_bytes(edited)):
            with self.assertRaises(migration.PublicationDrift):
                operator.rollback()
        self.assert_retained_policy(operator, edited)
        self.assertFalse(any(cmd[:2]==["systemctl","start"] for cmd in self.ctx.commands[count:]))

    def test_failed_activation_recovery_boundary_preserves_operator_edit(self):
        operator = migration.Migration(self.ctx);self.ctx.fail_start=common.SERVICE
        real = migration.exchange_files
        calls = 0
        edited = b'{"DefaultAction":"deny","operator":"recovery-boundary"}\n'
        def exchange(left, right):
            nonlocal calls
            calls += 1
            if calls==2:
                self.ctx.path(common.CONFIG).write_bytes(edited)
            return real(left, right)
        with mock.patch.object(migration, "exchange_files", side_effect=exchange):
            with self.assertRaisesRegex(common.Refusal, "recovery-required"):
                operator.apply()
        self.assert_retained_policy(operator, edited)

    def test_atomic_exchange_unavailable_refuses_before_activation(self):
        original=self.ctx.path(common.CONFIG).read_bytes()
        operator=migration.Migration(self.ctx)
        with mock.patch.object(migration, "exchange_files", side_effect=OSError(95,"unsupported")):
            with self.assertRaises(common.Refusal):
                operator.apply()
        self.assertEqual(self.ctx.path(common.CONFIG).read_bytes(), original)
        self.assertFalse(any(cmd[:2]==["systemctl","start"] for cmd in self.ctx.commands))

    def test_drift_refuses_before_mutation_or_journal(self):
        config=json.loads(self.ctx.path(common.CONFIG).read_text());config["DefaultDuration"]="always";self.ctx.write(common.CONFIG,json.dumps(config))
        with self.assertRaisesRegex(common.Refusal,"config/policy/address drift"):
            migration.Migration(self.ctx).apply()
        self.assertFalse(self.ctx.path(migration.STATE).exists())
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands))

    def test_rollback_refuses_later_policy_edit(self):
        operator=migration.Migration(self.ctx);operator.apply()
        config=json.loads(self.ctx.path(common.CONFIG).read_text());config["DefaultAction"]="deny";self.ctx.write(common.CONFIG,json.dumps(config))
        count=len(self.ctx.commands)
        with self.assertRaisesRegex(common.Refusal,"rollback refuses"):
            operator.rollback()
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands[count:]))

    def test_rollback_refuses_enablement_drift(self):
        operator=migration.Migration(self.ctx);operator.apply();self.ctx.states[common.SOCKETS[0]]["enabled"]="disabled"
        with self.assertRaisesRegex(common.Refusal,"enablement changed"):
            operator.rollback()

    def test_journal_symlink_refuses_without_mutation(self):
        destination=self.ctx.path(migration.STATE);destination.parent.mkdir(parents=True);destination.symlink_to(self.ctx.path("/var/home/gate"))
        with self.assertRaisesRegex(common.Refusal,"journal directory"):
            migration.Migration(self.ctx).apply()
        self.assertFalse(any(cmd[:2] in (["systemctl","start"],["systemctl","stop"]) for cmd in self.ctx.commands))


if __name__ == "__main__":
    unittest.main(verbosity=2)
