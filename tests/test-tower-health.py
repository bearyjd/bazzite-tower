#!/usr/bin/env python3
"""Offline runtime fixtures: probes, identity selection and fixed repair target."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "system_files/usr/libexec/bazzite-tower-health"
REPAIR = ROOT / "system_files/usr/libexec/bazzite-tower-boot-log-label"

MOCK = r'''#!/usr/bin/python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TRACE"], "a") as log:
    log.write(json.dumps([name] + args) + "\n")
if name == "rpm-ostree":
    if args == ["status", "--json"]:
        mode = os.environ.get("IDENTITY", "ok")
        if mode == "denied":
            print("Permission denied", file=sys.stderr); sys.exit(1)
        if mode == "invalid":
            print("{}"); sys.exit()
        if mode == "huge":
            sys.stdout.write("x" * 2200000); sys.exit()
        print(json.dumps({"deployments": [
            {"booted": True, "version": "booted-v1", "checksum": "ostree-only",
             "container-image-reference": "ostree-image-signed:docker://example@sha256:abc",
             "packages": ["PRIVATE-MANIFEST-MARKER"]},
            {"staged": True, "version": "staged-v2", "checksum": "staged-tree",
             "container-image-reference": "example@sha256:def"}]}))
    else: print("rpm-ostree fixture")
elif name == "systemctl":
    if args[0] == "is-enabled": print("disabled"); sys.exit(1)
    if args[0] == "is-active": print("inactive"); sys.exit(3)
    if args[0] == "--failed": print("broken.service loaded failed failed Fixture")
    if args[0] == "show": print("ActiveState=inactive\nResult=success\nExecMainStatus=0")
elif name == "journalctl":
    if os.environ.get("JOURNAL") == "denied":
        print("Hint: You are currently not seeing messages from other users.", file=sys.stderr)
        sys.exit(0)
    print("mce: [Hardware Error]: Corrected cache error")
elif name == "cat":
    if sys.argv[-1] == "/sys/module/nvidia/version": print("999.fixture")
    else:
        for item in args:
            try: print(pathlib.Path(item).read_text())
            except OSError: print("unavailable", file=sys.stderr); sys.exit(1)
elif name == "rpm": print("fixture-package-1")
elif name == "fwupdmgr":
    print("security fixture")
    print("x" * 300 + "\n" + "\n".join("line " + str(i) for i in range(40)))
elif name == "ls": print("system_u:object_r:var_log_t:s0 " + args[-1])
elif name == "matchpathcon": print("system_u:object_r:plymouthd_var_log_t:s0")
elif name == "ras-mc-ctl": print("Corrected error fixture")
elif name == "mokutil": print("SecureBoot enabled")
elif name == "uname":
    if os.environ.get("BUFFERED_EXCESS"):
        os.write(1, b"x" * 12290)
    elif os.environ.get("LONG_LINE"):
        print("y" * 241)
    else: print("fixture-kernel")
elif name == "stat":
    path = pathlib.Path(args[-1])
    print(os.environ.get("MOCK_UID", "0") + ":" + str(path.stat().st_nlink))
elif name == "selinuxenabled": sys.exit(int(os.environ.get("SELINUX_RC", "0")))
elif name == "restorecon": pass
else: raise SystemExit("unexpected command: " + name)
'''


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tower-health-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.trace = self.base / "trace"
        self.env = {**os.environ, "PATH": str(self.bin), "TRACE": str(self.trace)}
        for name in ("timeout", "head", "tr", "awk", "python3", "grep"):
            (self.bin / name).symlink_to(shutil.which(name))
        for name in ("rpm-ostree", "systemctl", "journalctl", "cat", "rpm", "fwupdmgr",
                     "ls", "matchpathcon", "ras-mc-ctl", "mokutil", "uname", "stat",
                     "selinuxenabled", "restorecon"):
            path = self.bin / name
            path.write_text(MOCK)
            path.chmod(0o755)

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def health(self, *args):
        result = subprocess.run(["/bin/bash", str(HEALTH), *args], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_identity_versions_labels_and_no_mutation(self):
        output = self.health()
        for expected in ("booted-v1", "staged-v2", "sha256:abc", "sha256:def", "ostree-checksum (not container digest)",
                         "999.fixture", "broken.service", "HardwareCorrupted", "plymouthd_var_log_t", "disabled", "inactive"):
            self.assertIn(expected, output)
        self.assertNotIn("PRIVATE-MANIFEST-MARKER", output)
        self.assertIn("UNKNOWN (tool unavailable)", output)  # getenforce absent
        self.assertIn("[display truncated]", output)
        self.assertLess(max(map(len, output.splitlines())), 520)
        for command in self.calls():
            if command[0] == "systemctl":
                self.assertIn(command[1], ("is-enabled", "is-active", "--failed", "show"))
            self.assertNotIn(command[0], ("restorecon", "selinuxenabled"))
            self.assertNotIn("refresh", command)
            if command[0] == "fwupdmgr":
                self.assertEqual(command, ["fwupdmgr", "--version"])
            if command[0] == "rpm":
                self.assertEqual(command, ["rpm", "-q", "kernel-core", "nvidia-driver", "kmod-nvidia", "linux-firmware", "microcode_ctl"])
        journals = [c for c in self.calls() if c[0] == "journalctl"]
        self.assertTrue(journals)
        self.assertTrue(all("-b" in c and "-n" in c and (("-k" in c and any(x.startswith("--grep=") for x in c)) or ("-u" in c and "rasdaemon.service" in c)) for c in journals))

    def test_permission_denial_even_with_zero_journal_status(self):
        self.env.update(IDENTITY="denied", JOURNAL="denied")
        output = self.health()
        self.assertIn("UNKNOWN (rpm-ostree status failed or denied)", output)
        self.assertIn("UNKNOWN (probe failed, denied, timed out or truncated; status=0)", output)

    def test_invalid_identity(self):
        self.env["IDENTITY"] = "invalid"
        self.assertIn("UNKNOWN (unrecognized rpm-ostree status)", self.health())

    def test_buffered_excess_bytes_marked_despite_zero_writer_exit(self):
        self.env["BUFFERED_EXCESS"] = "1"
        output = self.health()
        section = output.split("  Running kernel:", 1)[1].split("  Loaded NVIDIA", 1)[0]
        self.assertIn("UNKNOWN (probe failed, denied, timed out or truncated; status=0)", section)
        self.assertIn("[display truncated] (byte limit)", section)

    def test_long_line_clipping_is_explicit(self):
        self.env["LONG_LINE"] = "1"
        output = self.health()
        section = output.split("  Running kernel:", 1)[1].split("  Loaded NVIDIA", 1)[0]
        self.assertIn("[display truncated]", section)
        self.assertNotIn("y" * 241, section)

    def test_oversized_identity_is_unknown_and_not_echoed(self):
        self.env["IDENTITY"] = "huge"
        output = self.health()
        self.assertIn("Booted/staged image: UNKNOWN", output)
        self.assertLess(len(output), 16000)

    def test_exact_local_override_presence_without_contents(self):
        fixture = self.base / "file_contexts.local"
        fixture.write_text("/dev/kvmfr0    system_u:object_r:svirt_tmpfs_t:s0\n/private SECRET-MAPPING\n")
        copy = self.base / "health"
        copy.write_text(HEALTH.read_text().replace("/etc/selinux/targeted/contexts/files/file_contexts.local", str(fixture)))
        result = subprocess.run(["/bin/bash", str(copy)], env=self.env, capture_output=True, text=True, timeout=20)
        self.assertIn("Local SELinux override for exact /dev/kvmfr0: present", result.stdout)
        self.assertNotIn("SECRET-MAPPING", result.stdout)
        self.assertNotIn("svirt_tmpfs_t", result.stdout)

    def test_explicit_diagnostics_are_bounded(self):
        self.assertNotIn("Decoded recorded RAS errors", self.health())
        self.assertIn("Decoded recorded RAS errors", self.health("--diagnostics"))
        self.assertIn(["ras-mc-ctl", "--errors"], self.calls())
        result = subprocess.run(["/bin/bash", str(HEALTH), "--unknown"], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def repair(self):
        # Rewrite fixed absolute paths in a throwaway COPY. Production helper has
        # no path/environment override; none of these tests touches /var/log.
        text = REPAIR.read_text()
        text = re.sub(r"/var/log/boot\.log|/var/log|/var", lambda m: str(self.base / "fs") + m[0], text)
        helper = self.base / "repair"
        helper.write_text(text)
        return subprocess.run(["/bin/bash", str(helper)], env=self.env, capture_output=True, text=True, timeout=10)

    def target(self):
        target = self.base / "fs/var/log/boot.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def test_repair_only_existing_regular_single_link(self):
        target = self.target()
        self.assertEqual(self.repair().returncode, 0)  # absent: no file created
        self.assertFalse(target.exists())
        target.write_text("unchanged")
        self.assertEqual(self.repair().returncode, 0)
        self.assertIn(["restorecon", "--", str(target)], self.calls())
        self.assertEqual(target.read_text(), "unchanged")

    def test_repair_refuses_aliases_nonregular_and_nonroot(self):
        target = self.target()
        target.symlink_to(self.base / "missing")
        self.assertNotEqual(self.repair().returncode, 0)
        target.unlink()
        target.mkdir()
        self.assertNotEqual(self.repair().returncode, 0)
        target.rmdir()
        target.write_text("unchanged")
        os.link(target, self.base / "alias")
        self.assertNotEqual(self.repair().returncode, 0)
        (self.base / "alias").unlink()
        self.env["MOCK_UID"] = "1000"
        self.assertNotEqual(self.repair().returncode, 0)
        self.assertFalse(any(c[0] == "restorecon" for c in self.calls()))

    def test_repair_skips_disabled_selinux(self):
        self.target().write_text("unchanged")
        self.env["SELINUX_RC"] = "1"
        self.assertEqual(self.repair().returncode, 0)
        self.assertFalse(any(c[0] == "restorecon" for c in self.calls()))

    def test_repair_refuses_symlink_parent(self):
        log = self.base / "fs/var/log"
        log.parent.mkdir(parents=True)
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "boot.log").write_text("unchanged")
        log.symlink_to(elsewhere)
        self.assertNotEqual(self.repair().returncode, 0)
        self.assertFalse(any(c[0] == "restorecon" for c in self.calls()))

    def test_repair_unknown_selinux_or_missing_tools_fails(self):
        self.target().write_text("unchanged")
        self.env["SELINUX_RC"] = "2"
        self.assertNotEqual(self.repair().returncode, 0)
        (self.bin / "restorecon").unlink()
        self.assertNotEqual(self.repair().returncode, 0)
        self.assertFalse(any(c[0] == "restorecon" for c in self.calls()))

    def test_dropin_preserves_vendor_start_and_conditions(self):
        dropin = ROOT / "system_files/usr/lib/systemd/system/logrotate.service.d/20-boot-log-label.conf"
        lines = [line for line in dropin.read_text().splitlines() if line and not line.startswith("#")]
        self.assertEqual(lines, ["[Service]", "ExecStartPre=/usr/libexec/bazzite-tower-boot-log-label"])
        self.assertNotIn("-R", [c for c in REPAIR.read_text().splitlines() if c.startswith("exec ")][0])
        result = subprocess.run(["/bin/bash", str(REPAIR), "/tmp/arbitrary"], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
