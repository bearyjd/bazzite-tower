#!/usr/bin/env python3
"""Behavioral fixtures for the opt-in OpenSnitch deny helper (fake root, no systemd)."""
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
LIBEXEC = REPO/"system_files/usr/libexec"
HELPER = LIBEXEC/"bazzite-tower-snitchwatch-deny"
DROPIN = REPO/"system_files/usr/lib/systemd/system/opensnitch.service.d/bazzite-tower-snitchwatch-deny-guard.conf"
GUARD_UNIT = REPO/"system_files/usr/lib/systemd/system/bazzite-tower-snitchwatch-deny-guard.service"


def load(name, path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


system_tests = load("system_tests_for_deny", REPO/"tests/test-snitchwatch-system.py")
common = system_tests.common
readiness = system_tests.readiness
NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
TIMER = "bazzite-tower-snitchwatch-deny-revert"
STATE_DIR = "/var/lib/bazzite-tower/snitchwatch-deny"
STATE = STATE_DIR+"/state.json"
BACKUP = STATE_DIR+"/config.backup"
MARKER = "/etc/bazzite-tower/snitchwatch-deny-optin"


class DenyFixture(system_tests.Fixture):
    def __init__(self, root):
        super().__init__(root)
        self.restart_fails = False
        self.systemd_run_fails = False
        self.on_restart = None
        self.after_systemd_run = None
        self.during_systemd_run = None
        self.leftover_unit = False

    def run(self, argv, **kwargs):
        if argv[:2] in (["systemctl", "restart"], ["systemctl", "try-restart"]):
            self.commands.append(argv)
            if self.on_restart:
                self.on_restart()
            failed = self.restart_fails
            return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "injected restart failure" if failed else "")
        if argv[0] == "systemd-run":
            self.commands.append(argv)
            if self.during_systemd_run:
                self.during_systemd_run()
            failed = self.systemd_run_fails or (self.leftover_unit and "--collect" not in argv)
            if not failed and self.after_systemd_run:
                self.after_systemd_run()
            return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "injected systemd-run failure" if failed else "")
        if argv[:2] == ["systemctl", "stop"] and argv[2].startswith(TIMER):
            self.commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return super().run(argv, **kwargs)

    def mutating(self):
        return [cmd for cmd in self.commands if cmd[0] == "systemd-run" or cmd[:2] in (["systemctl", "restart"], ["systemctl", "try-restart"], ["systemctl", "stop"])]


class DenyBase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HELPER.exists(), "helper not implemented yet")
        self.deny = load("deny_under_test", HELPER)
        self.deny.common = common
        self.temp = tempfile.TemporaryDirectory(prefix="sw-deny-")
        self.ctx = DenyFixture(Path(self.temp.name))
        self.ctx.system_config()
        self.patches = [mock.patch.object(common.os, "readlink", side_effect=self.ctx.link),
                        mock.patch.object(self.deny.os, "fchown"),
                        mock.patch.object(self.deny, "utcnow", side_effect=lambda: self.now)]
        self.now = NOW
        for patch in self.patches:
            patch.start()
        self.original = self.config_path.read_bytes()
        self.deny.LOCK_TIMEOUT, self.deny.LOCK_POLL = 0.4, 0.02
        self.reboot("boot-A")

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def hold_lock(self):
        lock = self.ctx.path("/run/lock/bazzite-tower-snitchwatch-migration.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd

    def reboot(self, boot_id):
        self.ctx.write("/proc/sys/kernel/random/boot_id", boot_id+"\n")

    def setUp_clean(self):
        # Reset to a fresh allow baseline between subtests.
        for logical in (STATE, BACKUP, MARKER):
            if self.ctx.path(logical).exists():
                self.ctx.path(logical).unlink()
        self.ctx.write(common.CONFIG, self.original.decode())
        self.ctx.commands.clear()
        self.reboot("boot-A")

    @property
    def config_path(self):
        return self.ctx.path(common.CONFIG)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.deny.main(list(args), ctx=self.ctx)
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args):
        code, out, err = self.cli(*args)
        self.assertEqual(code, 0, err)
        return out

    def refused(self, *args, text=""):
        code, out, err = self.cli(*args)
        self.assertEqual(code, 1, out)
        self.assertRegex(err, "REFUSED")
        self.assertRegex(err.lower(), text.lower())
        return err

    def status(self):
        return json.loads(self.ok("status", "--json"))

    def edit_config(self, **changes):
        config = json.loads(self.config_path.read_text())
        config.update(changes)
        self.ctx.write(common.CONFIG, json.dumps(config, indent=4)+"\n")
        self.original = self.config_path.read_bytes()

    def assert_untouched(self):
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(self.ctx.path(STATE_DIR).exists())
        self.assertFalse(self.ctx.path(MARKER).exists())
        self.assertEqual(self.ctx.mutating(), [])

    def deny_bytes(self):
        return self.original.replace(b'"DefaultAction": "allow"', b'"DefaultAction": "deny"')

    def ready(self):
        self.ctx.flatpak()
        return common.runtime_readiness(self.ctx)


class Refusals(DenyBase):
    def test_legacy_profile_is_refused_even_though_otherwise_valid(self):
        self.ctx.write(common.CONFIG, self.ctx.path(common.LEGACY_REFERENCE).read_text())
        self.original = self.config_path.read_bytes()
        self.refused("on", text="legacy")
        self.assert_untouched()
        report = self.status()
        self.assertEqual((report["profile"], report["canEnable"]), ("legacy", False))
        self.assertTrue(report["blockers"])

    def test_unknown_profile_is_refused(self):
        self.edit_config(Server={"Address": "127.0.0.1:9999"})
        self.refused("on", text="profile")
        self.assertEqual(self.status()["profile"], "unknown")
        self.assert_untouched()

    def test_service_inactive_socket_missing_and_unsafe_config(self):
        self.ctx.states["opensnitch.service"]["active"] = "inactive"
        self.refused("on", text="opensnitch.service")
        self.assert_untouched()
        self.ctx.states["opensnitch.service"]["active"] = "active"
        self.ctx.path("/run/snitchwatch/opensnitchd.sock").unlink()
        self.refused("on", text="socket")
        self.assert_untouched()
        self.ctx.write("/run/snitchwatch/opensnitchd.sock", "x", 0o600)
        self.config_path.chmod(0o666)
        self.refused("on", text="root-owned")
        self.config_path.chmod(0o644)
        self.assert_untouched()

    def test_wrong_current_action_or_proc_method(self):
        for changes, text in (({"DefaultAction": "deny"}, "allow"), ({"DefaultAction": "ask"}, "allow"),
                              ({"ProcMonitorMethod": "ebpf"}, "proc")):
            with self.subTest(changes=changes):
                self.edit_config(**changes)
                self.refused("on", text=text)
                self.assert_untouched()
                self.assertFalse(self.status()["canEnable"])
                self.edit_config(DefaultAction="allow", ProcMonitorMethod="proc")

    def test_already_pending_is_refused_and_changes_nothing_more(self):
        self.ok("on")
        before = (self.config_path.read_bytes(), self.ctx.path(STATE).read_bytes(), list(self.ctx.mutating()))
        self.refused("on", text="pending")
        self.assertEqual((self.config_path.read_bytes(), self.ctx.path(STATE).read_bytes(), self.ctx.mutating()), before)

    def test_requires_root_without_injected_context(self):
        with mock.patch.object(self.deny.os, "geteuid", return_value=1000), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.deny.main(["status"]), 1)
        self.assertIn("root", err.getvalue())

    def test_minutes_bounds(self):
        for bad in ("0", "61", "-1", "abc", "1.5"):
            with self.subTest(minutes=bad), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.deny.main(["on", "--revert-after", bad], ctx=self.ctx)
                self.assertEqual(raised.exception.code, 2)
                self.assert_untouched()
        for good, expected in (("1", "1"), ("60", "60")):
            with self.subTest(minutes=good):
                self.ok("on", "--revert-after", good)
                self.assertIn("--on-active="+expected+"m", self.ctx.commands[-1] if self.ctx.commands[-1][0] == "systemd-run" else next(c for c in self.ctx.commands if c[0] == "systemd-run" and "--on-active="+expected+"m" in c))
                self.ok("off")
        self.ok("on")
        self.assertIn("--on-active=10m", next(c for c in reversed(self.ctx.commands) if c[0] == "systemd-run"))


class Transactions(DenyBase):
    def test_round_trip_on_confirm_off(self):
        out = self.ok("on", "--revert-after", "7")
        self.assertIn("7", out)
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertNotEqual(self.deny_bytes(), self.original)
        self.assertEqual(oct(self.config_path.stat().st_mode & 0o777), oct(0o644))
        self.assertEqual([p.name for p in self.config_path.parent.iterdir() if p.name.startswith(".")], [])
        run = [c for c in self.ctx.commands if c[0] == "systemd-run"]
        self.assertEqual(run, [["systemd-run", "--on-active=7m", "--on-calendar=2026-10-26 12:07:00 UTC", "--collect", "--unit="+TIMER,
                                "/usr/libexec/bazzite-tower-snitchwatch-deny", "revert-if-pending"]])
        self.assertLess(self.ctx.commands.index(run[0]), self.ctx.commands.index(["systemctl", "restart", "opensnitch.service"]))
        self.assertEqual(oct(self.ctx.path(STATE).stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(self.ctx.path(BACKUP).read_bytes(), self.original)
        report = self.status()
        self.assertEqual((report["pending"], report["optIn"], report["defaultAction"], report["canEnable"]), (True, False, "deny", False))
        self.assertEqual(report["expiresAt"], "2026-10-26T12:07:00Z")
        with self.assertRaisesRegex(common.Refusal, "deny"):
            self.ready()
        self.ok("confirm")
        self.assertIn(["systemctl", "stop", TIMER+".timer"], self.ctx.commands)
        marker = json.loads(self.ctx.path(MARKER).read_text())
        self.assertEqual((marker["schema"], marker["confirmedAt"]), (1, "2026-10-26T12:00:00Z"))
        self.assertEqual(oct(self.ctx.path(MARKER).stat().st_mode & 0o777), oct(0o644))
        report = self.status()
        self.assertEqual((report["pending"], report["optIn"], report["defaultAction"], report["expiresAt"]), (False, True, "deny", None))
        self.assertEqual(self.ready()["defaultAction"], "deny (operator opt-in)")
        restarts = self.ctx.commands.count(["systemctl", "restart", "opensnitch.service"])
        self.ok("off")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertEqual(self.ctx.commands.count(["systemctl", "restart", "opensnitch.service"]), restarts+1)
        self.assertFalse(self.ctx.path(MARKER).exists())
        self.assertFalse(self.ctx.path(STATE).exists())
        self.assertFalse(self.ctx.path(BACKUP).exists())
        self.assertEqual(self.ready()["defaultAction"], "allow")
        self.assertEqual(self.status()["canEnable"], True)

    def test_off_while_pending_reverts_and_stops_timer(self):
        self.ok("on")
        self.ok("off")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertIn(["systemctl", "stop", TIMER+".timer"], self.ctx.commands)
        self.assertFalse(self.ctx.path(STATE).exists())

    def test_timeout_revert(self):
        self.ok("on", "--revert-after", "5")
        self.now = NOW+timedelta(minutes=5, seconds=1)
        restarts = self.ctx.commands.count(["systemctl", "restart", "opensnitch.service"])
        self.ok("revert-if-pending")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertEqual(self.ctx.commands.count(["systemctl", "restart", "opensnitch.service"]), restarts+1)
        self.assertFalse(self.ctx.path(STATE).exists())
        self.assertEqual(self.ready()["defaultAction"], "allow")

    def test_late_confirm_after_suspend_reverts_immediately(self):
        # Suspend can stall the monotonic timer; confirm must not trust it.
        self.ok("on", "--revert-after", "5")
        self.now = NOW+timedelta(hours=3)
        restarts = self.ctx.commands.count(["systemctl", "restart", "opensnitch.service"])
        self.refused("confirm", text="expired")
        self.assertFalse(self.ctx.path(MARKER).exists())
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(self.ctx.path(STATE).exists())
        self.assertEqual(self.ctx.commands.count(["systemctl", "restart", "opensnitch.service"]), restarts+1)

    def test_timer_also_uses_wall_clock_deadline_so_suspend_cannot_extend_it(self):
        self.ok("on", "--revert-after", "12")
        argv = next(c for c in self.ctx.commands if c[0] == "systemd-run")
        self.assertIn("--on-active=12m", argv)
        self.assertIn("--on-calendar="+json.loads(self.ctx.path(STATE).read_text())["expiresAt"].replace("T", " ").replace("Z", " UTC"), argv)

    def test_leftover_failed_transient_unit_does_not_block_next_on(self):
        self.ctx.leftover_unit = True
        self.ok("on")
        self.assertIn("--collect", next(c for c in self.ctx.commands if c[0] == "systemd-run"))

    def test_confirm_requires_pending(self):
        self.refused("confirm", text="pending")
        self.assert_untouched()

    def test_revert_if_pending_is_a_noop_otherwise(self):
        self.ok("revert-if-pending")
        self.assert_untouched()
        self.ok("on")
        self.ok("confirm")
        before = (self.config_path.read_bytes(), list(self.ctx.commands))
        self.ok("revert-if-pending")
        self.assertEqual((self.config_path.read_bytes(), self.ctx.commands), before)
        self.assertTrue(self.ctx.path(MARKER).exists())

    def test_off_when_already_allow_changes_nothing(self):
        out = self.ok("off")
        self.assertIn("nothing", out.lower())
        self.assert_untouched()

    def test_reboot_while_pending_guard_reverts_before_firewall(self):
        self.ok("on")
        self.reboot("boot-B")
        mark = len(self.ctx.commands)
        self.ok("revert-if-pending", "--boot")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(self.ctx.path(STATE).exists())
        self.assertFalse(self.ctx.path(BACKUP).exists())
        # The daemon is not up yet and is ordered after the guard: never restart it from the guard.
        self.assertFalse(any(c[:2] in (["systemctl", "restart"], ["systemctl", "try-restart"]) for c in self.ctx.commands[mark:]))

    def test_reboot_with_confirmed_deny_keeps_it(self):
        self.ok("on")
        self.ok("confirm")
        self.reboot("boot-B")
        self.ok("revert-if-pending", "--boot")
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertTrue(self.ctx.path(MARKER).exists())

    def test_guard_unit_orders_before_firewall_and_is_conditional(self):
        text = GUARD_UNIT.read_text()
        self.assertRegex(text, r"(?m)^Type=oneshot$")
        self.assertRegex(text, r"(?m)^ConditionPathExists=" + re.escape(STATE) + "$")
        self.assertRegex(text, r"(?m)^Before=.*\bopensnitch\.service\b")
        self.assertRegex(text, r"(?m)^ExecStart=/usr/libexec/bazzite-tower-snitchwatch-deny revert-if-pending --boot$")
        self.assertRegex(text, r"(?m)^TimeoutStartSec=45$")
        self.assertRegex(text, r"(?m)^WantedBy=multi-user\.target$")

    def test_failure_after_write_restores_backup(self):
        for label, setup in (("restart", lambda: setattr(self.ctx, "restart_fails", True)),
                             ("daemon down", lambda: setattr(self.ctx, "on_restart", lambda: self.ctx.states["opensnitch.service"].update(active="failed")))):
            with self.subTest(label=label):
                self.ctx.restart_fails = False
                self.ctx.on_restart = None
                self.ctx.states["opensnitch.service"]["active"] = "active"
                setup()
                self.refused("on", text="restored")
                self.assertEqual(self.config_path.read_bytes(), self.original)
                self.assertFalse(self.ctx.path(STATE).exists())
                self.assertFalse(self.ctx.path(BACKUP).exists())
                self.assertFalse(self.ctx.path(MARKER).exists())
                self.assertIn(["systemctl", "stop", TIMER+".timer"], self.ctx.commands)

    def test_timer_is_armed_before_deny_is_written(self):
        order = []
        real = self.deny.publish
        def watching(ctx, logical, *args, **kwargs):
            if logical == common.CONFIG:
                order.append("config")
            return real(ctx, logical, *args, **kwargs)
        self.ctx.after_systemd_run = lambda: order.append("timer")
        with mock.patch.object(self.deny, "publish", side_effect=watching):
            self.ok("on")
        self.assertEqual(order, ["timer", "config"])

    def test_systemd_run_failure_leaves_config_untouched(self):
        self.ctx.systemd_run_fails = True
        original_mtime = self.config_path.stat().st_mtime_ns
        self.refused("on", text="systemd-run")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertEqual(self.config_path.stat().st_mtime_ns, original_mtime)
        self.assertFalse(self.ctx.path(STATE).exists())
        self.assertFalse(any(c[:2] == ["systemctl", "restart"] for c in self.ctx.commands))

    def test_failure_restore_keeps_concurrent_operator_edit(self):
        def operator_edit():
            config = json.loads(self.config_path.read_text())
            config["LogLevel"] = 0
            self.ctx.write(common.CONFIG, json.dumps(config, indent=4)+"\n")
        self.ctx.on_restart = operator_edit
        self.ctx.restart_fails = True
        self.refused("on", text="restored")
        config = json.loads(self.config_path.read_text())
        expected = json.loads(self.original)
        expected["LogLevel"] = 0
        self.assertEqual(config, expected)
        self.assertEqual(config["DefaultAction"], "allow")

    def test_concurrent_edit_before_publication_is_refused_and_nothing_overwritten(self):
        real = self.deny.tempfile.mkstemp
        def racing(*args, **kwargs):
            config = json.loads(self.config_path.read_text())
            config["LogLevel"] = 3
            self.ctx.write(common.CONFIG, json.dumps(config, indent=4)+"\n")
            return real(*args, **kwargs)
        with mock.patch.object(self.deny.tempfile, "mkstemp", side_effect=racing):
            self.refused("on", text="changed")
        self.assertFalse(any(c[:2] == ["systemctl", "restart"] for c in self.ctx.commands))
        self.assertEqual(json.loads(self.config_path.read_text())["LogLevel"], 3)
        self.assertEqual(json.loads(self.config_path.read_text())["DefaultAction"], "allow")
        self.assertFalse(self.ctx.path(STATE).exists())

    def hold_lock(self):
        lock = self.ctx.path("/run/lock/bazzite-tower-snitchwatch-migration.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd

    def test_on_and_confirm_refuse_immediately_under_lock_contention(self):
        fd = self.hold_lock()
        try:
            self.refused("on", text="lock")
            self.assert_untouched()
            self.assertFalse(self.status()["pending"])
        finally:
            os.close(fd)
        self.ok("on")
        fd = self.hold_lock()
        try:
            self.refused("confirm", text="lock")
        finally:
            os.close(fd)

    def test_revert_paths_wait_for_the_lock_then_still_revert(self):
        for action, extra in (("revert-if-pending", []), ("revert-if-pending", ["--boot"]), ("off", [])):
            with self.subTest(action=action, extra=extra):
                self.ok("on")
                self.reboot("boot-B")
                fd = self.hold_lock()
                threading.Timer(0.15, os.close, args=(fd,)).start()
                self.ok(action, *extra)
                self.assertEqual(self.config_path.read_bytes(), self.original)
                self.assertFalse(self.ctx.path(STATE).exists())
                self.reboot("boot-A")

    def test_revert_gives_clear_retryable_error_if_lock_never_released(self):
        self.ok("on")
        fd = self.hold_lock()
        try:
            for action in ("revert-if-pending", "off"):
                with self.subTest(action=action):
                    self.refused(action, text="timed out")
                    self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
                    self.assertTrue(json.loads(self.ctx.path(STATE).read_text())["pending"])
        finally:
            os.close(fd)
        self.ok("revert-if-pending")
        self.assertEqual(self.config_path.read_bytes(), self.original)

    def test_hard_kill_between_any_two_steps_leaves_allow_or_armed_deny(self):
        class Killed(BaseException):
            pass
        real_publish = self.deny.publish
        for point in ("backup", "state", "timer", "config", "restart"):
            with self.subTest(point=point):
                self.setUp_clean()
                snapshot = {}
                def die(*_):
                    # What a SIGKILL at this instant would leave on disk.
                    snapshot["files"] = {name: (self.ctx.path(name).read_bytes() if self.ctx.path(name).exists() else None)
                                         for name in (common.CONFIG, STATE, BACKUP, MARKER)}
                    snapshot["armed"] = any(c[0] == "systemd-run" for c in self.ctx.commands)
                    raise Killed()
                def publishing(ctx, logical, *args, **kwargs):
                    result = real_publish(ctx, logical, *args, **kwargs)
                    if logical == {"backup": BACKUP, "state": STATE, "config": common.CONFIG}.get(point) and not snapshot:
                        die()
                    return result
                if point == "timer":
                    self.ctx.after_systemd_run = die
                if point == "restart":
                    self.ctx.on_restart = die
                with mock.patch.object(self.deny, "publish", side_effect=publishing):
                    try:
                        self.deny.main(["on"], ctx=self.ctx)
                    except Killed:
                        pass
                self.assertTrue(snapshot, "injection point never reached: "+point)
                files = snapshot["files"]
                if files[common.CONFIG] != self.original:
                    self.assertEqual(files[common.CONFIG], self.deny_bytes())
                    self.assertTrue(snapshot["armed"], "deny live without an armed revert timer at "+point)
                    self.assertTrue(json.loads(files[STATE])["pending"], "deny live without pending state at "+point)
                # Put the killed-instant disk state back, then the timer/boot guard must recover.
                self.ctx.on_restart = self.ctx.after_systemd_run = None
                self.reboot("boot-B")
                for name, content in files.items():
                    path = self.ctx.path(name)
                    if content is None:
                        if path.exists():
                            path.unlink()
                    else:
                        self.ctx.write(name, content.decode())
                        path.chmod(0o600 if name in (STATE, BACKUP) else 0o644)
                self.ok("revert-if-pending", "--boot")
                self.assertEqual(self.config_path.read_bytes(), self.original)
                self.assertFalse(self.ctx.path(STATE).exists())

    def test_sigterm_during_on_rolls_back_like_a_failure(self):
        import signal
        def term():
            os.kill(os.getpid(), signal.SIGTERM)
        self.ctx.on_restart = term
        self.refused("on", text="restored")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(self.ctx.path(STATE).exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_failed_boot_revert_exits_nonzero_so_the_guard_unit_fails(self):
        self.ok("on")
        self.reboot("boot-B")
        self.config_path.chmod(0o666)
        self.refused("revert-if-pending", "--boot", text="root-owned")
        self.config_path.chmod(0o644)
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertTrue(json.loads(self.ctx.path(STATE).read_text())["pending"])

    def test_opensnitch_requires_the_guard_so_a_failed_guard_blocks_the_firewall(self):
        text = DROPIN.read_text()
        self.assertRegex(text, r"(?m)^Requires=bazzite-tower-snitchwatch-deny-guard\.service$")
        self.assertRegex(text, r"(?m)^After=bazzite-tower-snitchwatch-deny-guard\.service$")
        self.ctx.properties["opensnitch.service"]["DropInPaths"] = common.DAEMON_DROPIN+" "+common.DENY_GUARD_DROPIN
        self.ctx.write(common.DENY_GUARD_DROPIN, text)
        self.assertEqual(self.ready()["defaultAction"], "allow")
        self.ctx.write(common.DENY_GUARD_DROPIN, text+"ExecStartPre=/bin/true\n")
        with self.assertRaisesRegex(common.Refusal, "drop-in"):
            self.ready()

    def guard(self):
        return self.deny.main(["revert-if-pending", "--boot"], ctx=self.ctx)

    def test_guard_ignores_a_trial_from_this_boot_without_touching_the_lock(self):
        self.ok("on")
        self.deny.LOCK_TIMEOUT = 5
        fd = self.hold_lock()
        try:
            started = time.monotonic()
            code, out, err = self.cli("revert-if-pending", "--boot")
            self.assertLess(time.monotonic()-started, 1.0)
        finally:
            os.close(fd)
        self.assertEqual(code, 0, err)
        self.assertIn("this boot", out)
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertTrue(json.loads(self.ctx.path(STATE).read_text())["pending"])

    def test_guard_reverts_a_trial_from_an_earlier_boot_or_with_no_boot_id(self):
        for label in ("different", "missing"):
            with self.subTest(label=label):
                self.setUp_clean()
                self.reboot("boot-A")
                self.ok("on")
                self.assertEqual(json.loads(self.ctx.path(STATE).read_text())["bootId"], "boot-A")
                if label == "different":
                    self.reboot("boot-B")
                else:
                    state = json.loads(self.ctx.path(STATE).read_text())
                    del state["bootId"]
                    self.ctx.write(STATE, json.dumps(state), 0o600)
                self.ok("revert-if-pending", "--boot")
                self.assertEqual(self.config_path.read_bytes(), self.original)
                self.assertFalse(self.ctx.path(STATE).exists())

    def test_restart_inside_on_runs_the_guard_which_must_not_wait_for_on_lock(self):
        # Real sequence: `on` holds the lock and restarts opensnitch, systemd re-queues the guard.
        self.deny.LOCK_TIMEOUT = 5
        results = []
        def systemd_runs_guard():
            started = time.monotonic()
            results.append((self.guard(), time.monotonic()-started))
        self.ctx.on_restart = systemd_runs_guard
        self.ok("on")
        self.assertEqual(results[0][0], 0)
        self.assertLess(results[0][1], 1.0)
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertTrue(json.loads(self.ctx.path(STATE).read_text())["pending"])

    def test_sighup_and_sigterm_while_systemd_run_is_in_flight_still_stop_the_timer(self):
        import signal
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig):
                self.setUp_clean()
                self.ctx.during_systemd_run = lambda sig=sig: os.kill(os.getpid(), sig)
                self.refused("on", text="restored")
                self.assertIn(["systemctl", "stop", TIMER+".timer"], self.ctx.commands)
                self.assertEqual(self.config_path.read_bytes(), self.original)
                self.assertFalse(self.ctx.path(STATE).exists())
                self.assertIs(signal.getsignal(sig), signal.SIG_DFL)
                self.ctx.during_systemd_run = None

    def test_backup_digest_mismatch_falls_back_to_key_edit(self):
        self.ok("on")
        self.ok("confirm")
        self.ctx.path(BACKUP).write_bytes(b"tampered backup")
        self.ok("off")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(self.ctx.path(MARKER).exists())
        self.assertFalse(self.ctx.path(STATE).exists())

    def test_off_after_operator_edit_keeps_edit_and_only_flips_action(self):
        self.ok("on")
        self.ok("confirm")
        config = json.loads(self.config_path.read_text())
        config["LogLevel"] = 1
        self.ctx.write(common.CONFIG, json.dumps(config, indent=4)+"\n")
        self.ok("off")
        after = json.loads(self.config_path.read_text())
        expected = json.loads(self.original)
        expected["LogLevel"] = 1
        self.assertEqual(after, expected)

    def test_only_default_action_bytes_change_for_odd_formatting(self):
        text = self.original.decode().replace('"DefaultAction": "allow"', '"DefaultAction"  :\t"allow"')
        self.ctx.write(common.CONFIG, text.rstrip("\n")+"\n\n")
        self.original = self.config_path.read_bytes()
        self.ok("on")
        old, new = self.original, self.config_path.read_bytes()
        prefix = os.path.commonprefix([old, new])
        suffix = os.path.commonprefix([old[::-1], new[::-1]])[::-1]
        self.assertEqual((old[len(prefix):len(old)-len(suffix)], new[len(prefix):len(new)-len(suffix)]), (b"allow", b"deny"))
        self.assertEqual(json.loads(new)["DefaultAction"], "deny")
        self.ok("off")
        self.assertEqual(self.config_path.read_bytes(), self.original)

    def test_ambiguous_default_action_text_is_refused(self):
        config = json.loads(self.config_path.read_text())
        config["Extra"] = {"DefaultAction": "allow"}
        self.ctx.write(common.CONFIG, json.dumps(config, indent=4)+"\n")
        self.original = self.config_path.read_bytes()
        self.refused("on", text="DefaultAction")
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertEqual(self.ctx.mutating(), [])

    def test_symlinked_state_is_refused(self):
        self.ok("on")
        state = self.ctx.path(STATE)
        target = state.with_name("real.json")
        state.rename(target)
        state.symlink_to(target)
        self.refused("confirm", text="regular")
        self.refused("status", text="regular")

    def test_status_human_output(self):
        out = self.ok("status")
        self.assertIn("allow", out)
        self.ok("on")
        self.assertIn("pending", self.ok("status"))


class StatusSchema(DenyBase):
    def test_json_schema_is_stable(self):
        report = self.status()
        self.assertEqual(list(report), ["schema", "profile", "defaultAction", "optIn", "pending", "expiresAt", "canEnable", "blockers", "expired"])
        self.assertEqual(report, {"schema": 1, "profile": "system", "defaultAction": "allow", "optIn": False, "pending": False,
                                  "expiresAt": None, "canEnable": True, "blockers": [], "expired": False})
        self.edit_config(DefaultAction="ask")
        self.assertEqual(self.status()["defaultAction"], "other")


class ExpiredTrial(DenyBase):
    def test_not_expired_expired_not_pending_and_clock_skew(self):
        self.ok("on")
        report = self.status()
        self.assertEqual((report["pending"], report["expired"]), (True, False))
        self.assertFalse(any("expired" in b for b in report["blockers"]))
        self.now = NOW+timedelta(minutes=10)
        self.assertFalse(self.status()["expired"])
        self.now = NOW+timedelta(minutes=10, seconds=1)
        report = self.status()
        self.assertEqual((report["pending"], report["expired"], report["canEnable"]), (True, True, False))
        self.assertTrue(any("expired" in b and "ujust snitchwatch-deny off" in b for b in report["blockers"]))
        self.assertEqual(report["schema"], 1)
        self.now = NOW-timedelta(hours=1)
        self.assertFalse(self.status()["expired"])
        self.now = NOW+timedelta(hours=5)
        self.ctx.commands.clear()
        before = self.config_path.read_bytes()
        out = self.ok("status")
        self.assertIn("EXPIRED: still pending; run: ujust snitchwatch-deny off", out)
        self.assertEqual((self.ctx.commands, self.config_path.read_bytes()), ([c for c in self.ctx.commands if c[:2] == ["systemctl", "is-active"]], before))

    def test_confirmed_or_idle_is_never_expired(self):
        self.assertFalse(self.status()["expired"])
        self.ok("on")
        self.ok("confirm")
        self.now = NOW+timedelta(days=2)
        report = self.status()
        self.assertEqual((report["pending"], report["expired"]), (False, False))
        self.assertNotIn("EXPIRED", self.ok("status"))

    def test_on_while_expired_tells_the_operator_to_run_off(self):
        self.ok("on")
        self.now = NOW+timedelta(hours=1)
        err = self.refused("on", text="expired")
        self.assertIn("ujust snitchwatch-deny off", err)


class BootGuardRecovery(DenyBase):
    def trial(self, confirmed):
        self.ok("on")
        if confirmed:
            self.ok("confirm")
        self.reboot("boot-B")

    def corrupt(self, kind, text="{"):
        state = self.ctx.path(STATE)
        if kind == "json":
            state.write_text(text)
        elif kind == "mode":
            state.chmod(0o644)
        elif kind == "symlink":
            target = state.with_name("real.json")
            state.rename(target)
            state.symlink_to(target)

    def asides(self):
        return sorted(p for p in self.ctx.path(STATE_DIR).iterdir() if p.name.startswith("state.json.corrupt-"))

    def assert_recovered(self, code, err):
        self.assertEqual(code, 0, err)
        self.assertIn("WARNING", err)
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(os.path.lexists(self.ctx.path(STATE)))
        self.assertEqual(len(self.asides()), 1)
        self.assertFalse(os.path.lexists(self.ctx.path(MARKER)))
        self.assertIn(["systemctl", "stop", TIMER+".timer"], self.ctx.commands)

    def test_bad_state_at_boot_forces_allow_moves_it_aside_and_exits_zero(self):
        for confirmed in (False, True):
            for kind in ("json", "mode", "symlink", "unreadable"):
                with self.subTest(kind=kind, confirmed=confirmed):
                    self.setUp_clean()
                    for leftover in self.ctx.path(STATE_DIR).glob("state.json.corrupt-*") if self.ctx.path(STATE_DIR).exists() else []:
                        leftover.unlink()
                    self.trial(confirmed)
                    mark = len(self.ctx.commands)
                    if kind == "unreadable":
                        real = self.deny.private_bytes
                        def denied(ctx, logical, what, real=real):
                            if logical == STATE:
                                raise PermissionError(13, "Permission denied")
                            return real(ctx, logical, what)
                        with mock.patch.object(self.deny, "private_bytes", side_effect=denied):
                            code, out, err = self.cli("revert-if-pending", "--boot")
                    else:
                        self.corrupt(kind)
                        code, out, err = self.cli("revert-if-pending", "--boot")
                    self.assert_recovered(code, err)
                    self.assertFalse(any(c[:2] in (["systemctl", "restart"], ["systemctl", "try-restart"]) for c in self.ctx.commands[mark:]))
                    aside = self.asides()[0]
                    if not aside.is_symlink():
                        self.assertEqual(oct(aside.stat().st_mode & 0o777), oct(0o600))

    def test_bad_state_with_unsafe_or_ambiguous_config_still_fails_loudly(self):
        for label in ("unsafe", "ambiguous"):
            with self.subTest(label=label):
                self.setUp_clean()
                self.trial(False)
                self.corrupt("json")
                if label == "unsafe":
                    self.config_path.chmod(0o666)
                else:
                    config = json.loads(self.config_path.read_text())
                    config["Extra"] = {"DefaultAction": "deny"}
                    self.ctx.write(common.CONFIG, json.dumps(config, indent=4)+"\n")
                before = self.config_path.read_bytes()
                code, out, err = self.cli("revert-if-pending", "--boot")
                self.config_path.chmod(0o644)
                self.assertEqual(code, 1)
                self.assertIn("REFUSED", err)
                self.assertEqual(self.config_path.read_bytes(), before)
                self.assertTrue(os.path.lexists(self.ctx.path(STATE)))
                self.assertEqual(self.asides(), [])

    def test_aside_files_are_never_overwritten(self):
        for index in range(2):
            self.setUp_clean()
            self.trial(False)
            self.corrupt("json", "{broken-%d" % index)
            self.assertEqual(self.cli("revert-if-pending", "--boot")[0], 0)
        asides = self.asides()
        self.assertEqual(sorted(p.read_text() for p in asides), ["{broken-0", "{broken-1"])

    def flaky_load(self, failures, error):
        real = self.deny.load_state
        calls = []
        def load(ctx):
            calls.append(1)
            if len(calls) <= failures:
                raise error
            return real(ctx)
        return mock.patch.object(self.deny, "load_state", side_effect=load), calls

    def test_single_read_error_does_not_quarantine_a_confirmed_opt_in(self):
        self.trial(True)
        patch, calls = self.flaky_load(1, OSError(5, "Input/output error"))
        with patch:
            code, out, err = self.cli("revert-if-pending", "--boot")
        self.assertEqual(code, 0, err)
        self.assertGreater(len(calls), 1)
        self.assertEqual(self.asides(), [])
        self.assertTrue(self.ctx.path(MARKER).exists())
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertTrue(self.ctx.path(STATE).exists())

    def test_single_read_error_then_pending_earlier_boot_still_reverts_normally(self):
        self.trial(False)
        patch, _ = self.flaky_load(1, OSError(5, "Input/output error"))
        with patch:
            self.assertEqual(self.cli("revert-if-pending", "--boot")[0], 0)
        self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertEqual(self.asides(), [])

    def test_enoent_race_is_nothing_pending(self):
        self.trial(True)
        patch, _ = self.flaky_load(99, FileNotFoundError(2, "No such file"))
        with patch:
            code, out, err = self.cli("revert-if-pending", "--boot")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.asides(), [])
        self.assertTrue(self.ctx.path(MARKER).exists())
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())

    def test_two_failures_quarantine_as_before(self):
        self.trial(True)
        patch, calls = self.flaky_load(2, OSError(5, "Input/output error"))
        with patch:
            code, out, err = self.cli("revert-if-pending", "--boot")
        self.assert_recovered(code, err)

    def test_bad_state_lock_timeout_changes_nothing_and_fails_retryably(self):
        self.trial(True)
        self.corrupt("json")
        fd = self.hold_lock()
        try:
            code, out, err = self.cli("revert-if-pending", "--boot")
        finally:
            os.close(fd)
        self.assertEqual(code, 1)
        self.assertIn("timed out", err)
        self.assertEqual(self.asides(), [])
        self.assertTrue(self.ctx.path(MARKER).exists())
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())

    def test_operator_subcommands_still_refuse_on_bad_state(self):
        self.trial(False)
        self.corrupt("json")
        for action in ("off", "confirm", "on"):
            self.refused(action, text="state")
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
        self.assertTrue(os.path.lexists(self.ctx.path(STATE)))


class ReadinessSemantics(DenyBase):
    def deny_config(self):
        self.ctx.write(common.CONFIG, self.deny_bytes().decode())

    def marker(self, text=None, mode=0o644):
        text = text or json.dumps({"schema": 1, "confirmedAt": "2026-10-26T12:00:00Z", "id": "trial1"})
        self.ctx.write(MARKER, text, mode)

    def state(self, pending):
        self.ctx.write(STATE, json.dumps({"schema": 1, "pending": pending, "id": "trial1"}), 0o600)

    def test_allow_still_passes(self):
        self.assertEqual(self.ready()["defaultAction"], "allow")

    def test_markerless_deny_is_drift(self):
        self.deny_config()
        with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
            self.ready()

    def test_pending_deny_is_drift_even_with_marker(self):
        self.deny_config()
        self.state(True)
        with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
            self.ready()
        self.marker()
        with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
            self.ready()

    def test_confirmed_deny_is_accepted_and_reported(self):
        self.deny_config()
        self.marker()
        self.state(False)
        self.assertEqual(self.ready()["defaultAction"], "deny (operator opt-in)")
        self.ctx.path(STATE).unlink()
        self.assertEqual(self.ready()["defaultAction"], "deny (operator opt-in)")

    def test_invalid_markers_and_state_do_not_count(self):
        self.deny_config()
        self.state(False)
        for label, prepare in (("bad json", lambda: self.marker("not json")),
                               ("wrong schema", lambda: self.marker(json.dumps({"schema": 2, "confirmedAt": "x"}))),
                               ("no timestamp", lambda: self.marker(json.dumps({"schema": 1}))),
                               ("world writable", lambda: self.marker(mode=0o666)),
                               ("corrupt state", lambda: (self.marker(), self.ctx.write(STATE, "{", 0o600)))):
            with self.subTest(label=label):
                prepare()
                with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
                    self.ready()
                for path in (MARKER, STATE):
                    if self.ctx.path(path).exists():
                        self.ctx.path(path).unlink()
                self.state(False)

    def test_marker_must_belong_to_the_current_trial(self):
        self.deny_config()
        self.state(False)
        self.marker(json.dumps({"schema": 1, "confirmedAt": "2026-10-26T12:00:00Z", "id": "someone-else"}))
        with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
            self.ready()
        self.marker(json.dumps({"schema": 1, "confirmedAt": "2026-10-26T12:00:00Z"}))
        with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
            self.ready()

    def test_symlinked_marker_does_not_count(self):
        self.deny_config()
        real = self.ctx.write("/etc/bazzite-tower/real-marker", json.dumps({"schema": 1, "confirmedAt": "x"}))
        self.ctx.path(MARKER).symlink_to(real)
        with self.assertRaisesRegex(common.Refusal, "DefaultAction"):
            self.ready()

    def test_readiness_cli_names_the_opt_in(self):
        self.deny_config()
        self.marker()
        self.ctx.flatpak()
        out = io.StringIO()
        with mock.patch.object(readiness.sys, "argv", ["bazzite-tower-snitchwatch-readiness"]), \
             mock.patch.object(readiness.os, "geteuid", return_value=0), \
             mock.patch.object(common, "Context", return_value=self.ctx), contextlib.redirect_stdout(out):
            readiness.main()
        lines = out.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])["defaultAction"], "deny (operator opt-in)")
        self.assertIn("deny (operator opt-in)", lines[1])
        self.assertNotIn("DefaultAction is allow", lines[1])

    def test_image_ships_allow_everywhere(self):
        for name in ("opensnitchd-default-config.json", "opensnitchd-system-bridge-config.json"):
            self.assertEqual(json.loads((REPO/"system_files/usr/share/bazzite-tower"/name).read_text())["DefaultAction"], "allow")
        legacy = (LIBEXEC/"bazzite-tower-opensnitch-readiness").read_text()
        self.assertIn("'.DefaultAction == \"allow\"'", legacy)
        for build_time in ("ci/snitchwatch-system-verify.py", "ci/snitchwatch-system-stage.py"):
            path = REPO/build_time
            if path.exists():
                self.assertNotIn("opt-in marker", path.read_text())
                self.assertNotIn(MARKER, path.read_text())

SNAP = "/run/snitchwatch/deny-status.json"
STATUS_UNIT = REPO/"system_files/usr/lib/systemd/system/bazzite-tower-snitchwatch-deny-status.service"
SCHEMA_KEYS = ["schema", "profile", "defaultAction", "optIn", "pending", "expiresAt", "canEnable", "blockers", "expired"]


class StatusExport(DenyBase):
    def snap(self):
        return json.loads(self.ctx.path(SNAP).read_text())

    def assert_snapshot_matches_status(self):
        path = self.ctx.path(SNAP)
        info = os.lstat(path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(oct(stat.S_IMODE(info.st_mode)), oct(0o644))
        self.assertEqual(info.st_uid, os.geteuid())
        raw = self.snap()
        self.assertEqual(list(raw), SCHEMA_KEYS+["updatedAt", "bootId"])
        self.assertEqual(raw["updatedAt"], self.deny.stamp(self.now))
        self.assertEqual(raw["bootId"], self.ctx.path("/proc/sys/kernel/random/boot_id").read_text().strip())
        body = {key: raw[key] for key in SCHEMA_KEYS}
        self.assertEqual(body, self.status())
        self.assertEqual([p.name for p in path.parent.iterdir() if p.name.startswith(".")], [])
        return raw

    def fail_snapshot_writes(self):
        real = tempfile.mkstemp
        def broken(*args, **kwargs):
            if str(kwargs.get("dir")).endswith("run/snitchwatch"):
                raise OSError(30, "Read-only file system")
            return real(*args, **kwargs)
        return mock.patch.object(self.deny.tempfile, "mkstemp", side_effect=broken)

    def test_status_is_read_only_and_json_is_byte_identical(self):
        before = sorted(p.name for p in self.ctx.path("/run/snitchwatch").iterdir())
        out = self.ok("status", "--json")
        self.ok("status")
        self.assertEqual(sorted(p.name for p in self.ctx.path("/run/snitchwatch").iterdir()), before)
        self.assertFalse(os.path.lexists(self.ctx.path(SNAP)))
        self.assertEqual(out, json.dumps({"schema": 1, "profile": "system", "defaultAction": "allow", "optIn": False, "pending": False,
                                          "expiresAt": None, "canEnable": True, "blockers": [], "expired": False})+"\n")

    def test_every_state_change_publishes(self):
        self.ok("on", "--revert-after", "5")
        raw = self.assert_snapshot_matches_status()
        self.assertEqual((raw["pending"], raw["defaultAction"], raw["expiresAt"]), (True, "deny", "2026-10-26T12:05:00Z"))
        self.now = NOW+timedelta(seconds=30)
        self.ok("confirm")
        raw = self.assert_snapshot_matches_status()
        self.assertEqual((raw["pending"], raw["optIn"]), (False, True))
        self.ok("off")
        raw = self.assert_snapshot_matches_status()
        self.assertEqual((raw["optIn"], raw["defaultAction"], raw["canEnable"]), (False, "allow", True))

    def test_timer_revert_and_boot_revert_publish_allow(self):
        self.ok("on", "--revert-after", "5")
        self.now = NOW+timedelta(minutes=6)
        self.ok("revert-if-pending")
        self.assertEqual(self.assert_snapshot_matches_status()["defaultAction"], "allow")
        self.setUp_clean()
        self.ok("on")
        self.reboot("boot-B")
        self.ok("revert-if-pending", "--boot")
        raw = self.assert_snapshot_matches_status()
        self.assertEqual((raw["defaultAction"], raw["pending"], raw["bootId"]), ("allow", False, "boot-B"))

    def test_late_confirm_revert_publishes(self):
        self.ok("on", "--revert-after", "5")
        self.now = NOW+timedelta(minutes=6)
        self.refused("confirm", text="expired")
        self.assertEqual(self.assert_snapshot_matches_status()["defaultAction"], "allow")

    def test_quarantine_path_publishes_allow(self):
        self.ok("on")
        self.ok("confirm")
        self.reboot("boot-B")
        self.ctx.path(STATE).write_text("{")
        code, out, err = self.cli("revert-if-pending", "--boot")
        self.assertEqual(code, 0, err)
        raw = self.assert_snapshot_matches_status()
        self.assertEqual((raw["defaultAction"], raw["optIn"], raw["pending"]), ("allow", False, False))

    def test_failed_on_rolls_back_and_snapshot_shows_allow(self):
        self.ctx.systemd_run_fails = True
        self.refused("on", text="rolled back")
        raw = self.assert_snapshot_matches_status()
        self.assertEqual((raw["defaultAction"], raw["pending"]), ("allow", False))

    def test_snapshot_failure_never_fails_or_rolls_back(self):
        with self.fail_snapshot_writes():
            code, out, err = self.cli("on")
            self.assertEqual(code, 0, err)
            self.assertRegex(err, "WARNING.*status snapshot")
            self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())
            self.assertEqual(self.status()["pending"], True)
            self.assertEqual(self.cli("confirm")[0], 0)
            self.assertEqual(self.status()["optIn"], True)
            self.assertEqual(self.cli("off")[0], 0)
            self.assertEqual(self.config_path.read_bytes(), self.original)
            self.ok("on")
            self.reboot("boot-B")
            code, out, err = self.cli("revert-if-pending", "--boot")
            self.assertEqual(code, 0, err)
            self.assertEqual(self.config_path.read_bytes(), self.original)
        self.assertFalse(os.path.lexists(self.ctx.path(SNAP)))

    def test_unexpected_snapshot_exception_is_also_contained(self):
        with mock.patch.object(self.deny, "write_snapshot", side_effect=RuntimeError("boom")):
            code, out, err = self.cli("on")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.config_path.read_bytes(), self.deny_bytes())

    def test_missing_directory_is_not_created(self):
        with mock.patch.object(self.deny, "SNAPSHOT_DIR", "/run/absent"), mock.patch.object(self.deny, "SNAPSHOT", "/run/absent/deny-status.json"):
            code, out, err = self.cli("on")
        self.assertEqual(code, 0, err)
        self.assertIn("WARNING", err)
        self.assertFalse(self.ctx.path("/run/absent").exists())

    def test_symlinked_or_writable_directory_is_refused(self):
        directory = self.ctx.path("/run/snitchwatch")
        directory.chmod(0o777)
        self.assertEqual(self.cli("publish-status")[0], 1)
        self.assertFalse(os.path.lexists(directory/"deny-status.json"))
        directory.chmod(0o711)
        moved = directory.with_name("real")
        directory.rename(moved)
        directory.symlink_to(moved)
        self.assertEqual(self.cli("publish-status")[0], 1)
        self.assertFalse(os.path.lexists(moved/"deny-status.json"))

    def test_target_symlink_is_replaced_not_followed(self):
        victim = self.ctx.path("/run/victim")
        victim.write_text("keep")
        self.ctx.path(SNAP).symlink_to(victim)
        self.ok("publish-status")
        self.assertFalse(self.ctx.path(SNAP).is_symlink())
        self.assertEqual(victim.read_text(), "keep")
        self.assert_snapshot_matches_status()

    def test_publish_status_recomputes_and_fails_loudly_when_it_cannot_write(self):
        self.ok("publish-status")
        self.assert_snapshot_matches_status()
        self.ok("on")
        self.ctx.path(SNAP).unlink()
        self.ok("publish-status")
        self.assertEqual(self.snap()["pending"], True)
        with self.fail_snapshot_writes():
            self.refused("publish-status", text="snapshot")

    def test_publish_status_requires_root_like_other_subcommands(self):
        with mock.patch.object(self.deny.os, "geteuid", return_value=1000), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.deny.main(["publish-status"]), 1)
        self.assertIn("root", err.getvalue())

    def test_publish_status_skips_when_a_writer_holds_the_lock(self):
        fd = self.hold_lock()
        try:
            code, out, err = self.cli("publish-status")
        finally:
            os.close(fd)
        self.assertEqual(code, 0, err)
        self.assertIn("skipped", out)
        self.assertFalse(os.path.lexists(self.ctx.path(SNAP)))

    def test_interrupted_write_leaves_old_snapshot_or_none(self):
        self.ok("publish-status")
        old = self.ctx.path(SNAP).read_bytes()
        self.ok("on")
        for target, error in (("replace", SystemExit(9)), ("fsync", OSError(5, "io"))):
            with self.subTest(kill=target):
                with mock.patch.object(self.deny.os, target, side_effect=error):
                    with contextlib.suppress(SystemExit):
                        self.cli("publish-status")
                self.assertEqual(json.loads(self.ctx.path(SNAP).read_bytes())["schema"], 1)
                self.assertEqual([p.name for p in self.ctx.path("/run/snitchwatch").iterdir() if p.name.startswith(".")], [])
        self.assertNotEqual(old, b"")

    def test_snapshot_has_only_schema_fields_and_no_paths(self):
        self.ok("on")
        text = self.ctx.path(SNAP).read_text()
        self.assertNotIn(self.temp.name, text)
        self.assertNotIn("/var/lib", text)
        self.assertEqual(set(json.loads(text)), set(SCHEMA_KEYS)|{"updatedAt", "bootId"})

    def test_boot_publish_unit_cannot_block_or_fail_the_firewall(self):
        text = STATUS_UNIT.read_text()
        self.assertRegex(text, r"(?m)^Type=oneshot$")
        self.assertRegex(text, r"(?m)^ExecStart=-/usr/libexec/bazzite-tower-snitchwatch-deny publish-status$")
        self.assertRegex(text, r"(?m)^TimeoutStartSec=20$")
        self.assertRegex(text, r"(?m)^After=.*bazzite-tower-snitchwatch-deny-guard\.service")
        self.assertRegex(text, r"(?m)^WantedBy=multi-user\.target$")
        self.assertNotRegex(text, r"(?m)^(Requires|Requisite|BindsTo|RequiredBy)=")
        self.assertNotRegex(text, r"(?m)^Before=.*opensnitch")
        self.assertNotIn("status", DROPIN.read_text())
        self.assertNotIn("ExecStartPost", GUARD_UNIT.read_text())
        self.assertIn("deny-status", (REPO/"build_files/build.d/96-snitchwatch-deny-guard.sh").read_text())
        smoke = (REPO/"tests/smoke.sh").read_text()
        self.assertIn("bazzite-tower-snitchwatch-deny-status.service", smoke)


if __name__ == "__main__":
    unittest.main(verbosity=2)
