#!/usr/bin/env python3
"""Execute the shipped Bash logic against command and fixed-path fixtures."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]


class LegacyReadiness(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="legacy-ready-")
        self.root=Path(self.temporary.name)
        self.config=self.root/"etc/opensnitchd/default-config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"Server":{"Address":"127.0.0.1:50051"},"DefaultAction":"allow"}')
        self.home=self.root/"home";binary=self.home/".local/bin/snitchwatch-bridge-cli"
        binary.parent.mkdir(parents=True);binary.write_text("released fixture\n");binary.chmod(0o755)
        manifest=self.home/".local/share/snitchwatch/bridge.sha256"
        manifest.parent.mkdir(parents=True);manifest.write_text(hashlib.sha256(binary.read_bytes()).hexdigest()+"  snitchwatch-bridge-cli\n")
        self.manifest=manifest
        self.bin=self.root/"bin";self.bin.mkdir()
        self.log=self.root/"calls.log"
        self.write_command("systemctl", """#!/bin/sh
printf '%s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  'is-active --quiet opensnitch.service'|'--user is-active --quiet snitchwatch-bridge.service') exit 0 ;;
  '--user is-enabled snitchwatch-bridge.service') printf 'enabled\n' ;;
  '--user show --property=MainPID --value snitchwatch-bridge.service') printf '420\n' ;;
  *) exit 99 ;;
esac
""")
        self.write_command("readlink", """#!/bin/sh
case "$*" in
  '-f -- /proc/420/exe') printf '%s\n' "${FOREIGN_EXE:-$EXPECTED_BINARY}" ;;
  *) exec /usr/bin/readlink "$@" ;;
esac
""")
        self.write_command("ss", """#!/bin/sh
printf 'LISTEN 0 128 127.0.0.1:50051 0.0.0.0:* users:(("bridge",pid=%s,fd=3))\n' "${LISTENER_PID:-420}"
""")
        system=self.root/"usr/libexec/bazzite-tower-snitchwatch-readiness"
        system.parent.mkdir(parents=True);system.write_text('#!/bin/sh\nprintf "fixture-system-dispatch:%s\\n" "$*"\nexit 42\n');system.chmod(0o755)
        self.profile=self.root/"usr/share/bazzite-tower/snitchwatch-bridge-profile"
        self.profile.parent.mkdir(parents=True)
        original=(REPO/"system_files/usr/libexec/bazzite-tower-opensnitch-readiness").read_text()
        # Test-only fixed-root rewrite; production exposes no runtime override.
        modified=original.replace("/etc/opensnitchd/",str(self.root/"etc/opensnitchd")+"/").replace("/usr/share/bazzite-tower/",str(self.root/"usr/share/bazzite-tower")+"/").replace("/usr/libexec/bazzite-tower-snitchwatch-readiness",str(system))
        self.script=self.root/"readiness";self.script.write_text(modified)
        self.env={**os.environ,"HOME":str(self.home),"PATH":str(self.bin)+":"+os.environ["PATH"],"EXPECTED_BINARY":str(binary),"COMMAND_LOG":str(self.log)}

    def tearDown(self):
        self.temporary.cleanup()

    def write_command(self,name,text):
        path=self.bin/name;path.write_text(text);path.chmod(0o755)

    def run_check(self):
        return subprocess.run(["bash",str(self.script)],env=self.env,capture_output=True,text=True,timeout=3)

    def test_default_legacy_pass_and_no_mutations(self):
        result=self.run_check();self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("Local prerequisites pass",result.stdout)
        self.assertNotRegex(self.log.read_text(),r"^(start|stop|enable|disable|restart|reload)\b")

    def test_explicit_legacy_same_behavior(self):
        self.profile.write_text("legacy\n");self.assertEqual(self.run_check().returncode,0)

    def test_bad_hash_refuses(self):
        self.manifest.write_text("0"*64+"  snitchwatch-bridge-cli\n")
        result=self.run_check();self.assertEqual(result.returncode,1);self.assertIn("hash does not match",result.stderr)

    def test_wrong_main_process_refuses(self):
        self.env["FOREIGN_EXE"]="/tmp/debug-bridge"
        result=self.run_check();self.assertEqual(result.returncode,1);self.assertIn("not",result.stderr)

    def test_wrong_listener_owner_refuses(self):
        self.env["LISTENER_PID"]="999"
        result=self.run_check();self.assertEqual(result.returncode,1);self.assertIn("not listening",result.stderr)

    def test_deny_policy_refuses(self):
        self.config.write_text('{"Server":{"Address":"127.0.0.1:50051"},"DefaultAction":"deny"}')
        result=self.run_check();self.assertEqual(result.returncode,1);self.assertIn("DefaultAction is not allow",result.stderr)

    def test_invalid_mode_refuses_before_system_commands(self):
        self.profile.write_text("invalid\n");result=self.run_check()
        self.assertEqual(result.returncode,1);self.assertIn("invalid image",result.stderr);self.assertFalse(self.log.exists())

    def test_system_dispatch_and_no_legacy_execution(self):
        self.profile.write_text("system\n");result=self.run_check()
        self.assertEqual(result.returncode,42);self.assertIn("fixture-system-dispatch",result.stdout);self.assertFalse(self.log.exists())


if __name__=="__main__":
    unittest.main(verbosity=2)
