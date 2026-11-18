#!/usr/bin/env python3
"""Offline tests for `bazzite-tower-opensnitch-ebpf-check --runtime`.

Fakes only: a procfs tree of dangling symlinks, a config, a log and a `systemctl`
script first on PATH. No daemon, kernel, root, container or network. The fd link
name and log strings are UNVERIFIED on the real kernel/daemon; the constants are
asserted by name so a calibration edit is a visible, deliberate test change.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'system_files/usr/libexec/bazzite-tower-opensnitch-ebpf-check'
_spec = importlib.util.spec_from_file_location('ebpf_tests', Path(__file__).with_name('test-snitchwatch-ebpf.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

BPF = 'anon_inode:bpf-prog'
FALLBACK = 'error starting ebpf monitor method'
PARTIAL = 'opensnitch-procs.o not available'
START = 'Tue 2026-11-17 10:00:00 UTC'
PID = 4242
FAKE_SYSTEMCTL = '''#!/bin/sh
case "$*" in
  *MainPID*) [ -n "$FAKE_MAINPID_FAIL" ] && exit 1; printf '%s\\n' "$FAKE_MAINPID" ;;
  *ActiveEnterTimestamp*) printf '%s\\n' "$FAKE_START" ;;
  *) exit 1 ;;
esac
'''


class Runtime(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMPDIR') or None)
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        (self.bin / 'systemctl').write_text(FAKE_SYSTEMCTL)
        (self.bin / 'systemctl').chmod(0o755)
        self.proc = self.dir / 'proc'
        self.config = self.dir / 'default-config.json'
        self.log = self.dir / 'opensnitchd.log'

    def setup_fds(self, bpf=2, other=('/dev/null', 'socket:[123]', 'socket:[456]', 'pipe:[9]'), pid=PID):
        fd = self.proc / str(pid) / 'fd'
        fd.mkdir(parents=True)
        for index, target in enumerate([BPF] * bpf + list(other)):
            os.symlink(target, fd / str(index))
        return fd

    def run_check(self, method='ebpf', pid=PID, log='', start=START, mainpid_fail=False, args=(), config=True, env=None):
        if config:
            self.config.write_text(json.dumps({'ProcMonitorMethod': method}) if method is not None else '{}')
        if log is not None:
            self.log.write_text(log)
        environment = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}', 'FAKE_MAINPID': str(pid),
                       'FAKE_START': start, **({'FAKE_MAINPID_FAIL': '1'} if mainpid_fail else {}), **(env or {})}
        return subprocess.run(['python3', '-B', str(CHECK), '--runtime', '--config', str(self.config), '--log', str(self.log),
                               '--proc-root', str(self.proc), *args], capture_output=True, text=True, env=environment)

    def result(self, run):
        last = run.stdout.rstrip('\n').splitlines()[-1]
        self.assertTrue(last.startswith('RESULT: '), run.stdout)
        return last.split(': ', 2)[1], last

    def assertVerdict(self, run, verdict, code, *needles):
        got, line = self.result(run)
        self.assertEqual((got, run.returncode), (verdict, code), run.stdout + run.stderr)
        for needle in needles:
            self.assertIn(needle, line)

    def test_ebpf_with_bpf_prog_fds_passes_and_prints_the_count(self):
        self.setup_fds(bpf=3)
        self.assertVerdict(self.run_check(), 'PASS', 0, '3 ' + BPF)

    def test_ebpf_with_zero_bpf_prog_fds_is_a_silent_fallback(self):
        self.setup_fds(bpf=0)
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'silent /proc fallback')

    def test_fallback_warning_after_unit_start_fails_even_with_fds(self):
        self.setup_fds()
        log = f'[2026-11-17 10:00:05]  WRN  {FALLBACK}: boom\n'
        self.assertVerdict(self.run_check(log=log), 'FAIL', 1, 'silent /proc fallback', FALLBACK)

    def test_fallback_warning_before_unit_start_is_ignored(self):
        self.setup_fds()
        log = f'[2026-11-17 09:59:59]  WRN  {FALLBACK}: old\n[2026-11-17 10:00:07]  INF  ok\n'
        self.assertVerdict(self.run_check(log=log), 'PASS', 0)

    def test_untimestamped_continuation_inherits_the_previous_line_time(self):
        self.setup_fds()
        old = f'[2026-11-17 09:00:00]  WRN  boom\n{FALLBACK} (continued)\n'
        self.assertVerdict(self.run_check(log=old), 'PASS', 0)

    def test_unparseable_unit_start_counts_every_log_line(self):
        self.setup_fds()
        log = f'[2026-11-17 09:00:00]  WRN  {FALLBACK}\n'
        self.assertVerdict(self.run_check(log=log, start=''), 'FAIL', 1)

    def test_procs_object_warning_is_partial_mode_warn(self):
        self.setup_fds()
        log = f'[2026-11-17 10:00:05]  WRN  {PARTIAL}\n'
        self.assertVerdict(self.run_check(log=log), 'WARN', 0, 'partial eBPF', PARTIAL)

    def test_fallback_outranks_partial_warning(self):
        self.setup_fds()
        log = f'[2026-11-17 10:00:05]  WRN  {PARTIAL}\n[2026-11-17 10:00:06]  ERR  {FALLBACK}\n'
        self.assertVerdict(self.run_check(log=log), 'FAIL', 1)

    def test_count_below_the_calibrated_floor_warns(self):
        # EBPF_PROG_FD_FLOOR is 1 until owner gate A calibrates it, so patch the constant in a copy.
        floor_copy = self.dir / 'check'
        floor_copy.write_text(CHECK.read_text().replace('EBPF_PROG_FD_FLOOR = 1', 'EBPF_PROG_FD_FLOOR = 5'))
        self.setup_fds(bpf=2)
        self.config.write_text('{"ProcMonitorMethod": "ebpf"}')
        self.log.write_text('')
        run = subprocess.run(['python3', '-B', str(floor_copy), '--runtime', '--config', str(self.config), '--log', str(self.log),
                              '--proc-root', str(self.proc)], capture_output=True, text=True,
                             env={**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}', 'FAKE_MAINPID': str(PID), 'FAKE_START': START})
        self.assertVerdict(run, 'WARN', 0, 'below the calibrated floor 5')

    def test_floor_constant_is_one_until_calibrated(self):
        self.assertEqual(base.load(CHECK)['EBPF_PROG_FD_FLOOR'], 1)
        self.assertEqual(base.load(CHECK)['BPF_PROG_LINK'], BPF)

    def test_proc_method_warns_without_reading_fds(self):
        self.assertVerdict(self.run_check(method='proc'), 'WARN', 0, 'documented /proc fallback')

    def test_other_methods_and_missing_key_fail(self):
        for method in ('audit', 'ftrace', None):
            with self.subTest(method=method):
                self.assertVerdict(self.run_check(method=method), 'FAIL', 1, 'expected ebpf')

    def test_missing_or_corrupt_config_fails(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(config=False), 'FAIL', 1, 'cannot read ProcMonitorMethod')
        self.config.write_text('{not json')
        self.assertVerdict(self.run_check(config=False), 'FAIL', 1, 'cannot read ProcMonitorMethod')

    def test_daemon_not_running_fails(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(pid=0), 'FAIL', 1, 'no running daemon')
        self.assertVerdict(self.run_check(pid=''), 'FAIL', 1, 'no running daemon')

    def test_systemctl_failure_fails_cleanly(self):
        self.assertVerdict(self.run_check(mainpid_fail=True), 'FAIL', 1, 'cannot query systemd')

    def test_missing_systemctl_fails_cleanly(self):
        self.config.write_text('{"ProcMonitorMethod": "ebpf"}')
        run = subprocess.run([sys.executable, '-B', str(CHECK), '--runtime', '--config', str(self.config), '--proc-root', str(self.proc)],
                             capture_output=True, text=True, env={'PATH': str(self.dir / 'empty')})
        self.assertVerdict(run, 'FAIL', 1, 'cannot query systemd')

    def test_unreadable_fd_dir_fails_instead_of_crashing(self):
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'cannot read')
        fd = self.setup_fds()
        fd.chmod(0)
        self.addCleanup(fd.chmod, 0o755)
        if os.geteuid() != 0:
            run = self.run_check()
            self.assertVerdict(run, 'FAIL', 1, 'cannot read')
            self.assertNotIn('Traceback', run.stderr)

    def test_unreadable_log_with_fds_is_warn_not_pass(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(log=None), 'WARN', 0, 'unreadable')

    def test_detail_prints_the_calibration_fields(self):
        self.setup_fds(bpf=2)
        log = f'[2026-11-17 10:00:05]  WRN  {PARTIAL}\n[2026-11-17 09:00:00]  WRN  {FALLBACK}\n'
        run = self.run_check(log=log, args=('--detail',))
        lines = run.stdout.splitlines()
        self.assertEqual(self.result(run)[0], 'WARN')
        for needle in (f'MainPID={PID}', 'ActiveEnterTimestamp=', "ProcMonitorMethod='ebpf'", f'{BPF}=2', 'fd total=6',
                       f'fd      2 {BPF}', 'fd      2 socket:[*]', 'bpftool', f'log [2026-11-17 10:00:05]  WRN  {PARTIAL}'):
            self.assertTrue(any(needle in line for line in lines), (needle, run.stdout))
        self.assertFalse(any(FALLBACK in line for line in lines if line.startswith('detail: log')), 'pre-start line must not be listed')
        self.assertTrue(lines[-1].startswith('RESULT: '))

    def test_detail_still_prints_when_the_check_fails(self):
        run = self.run_check(args=('--detail',))
        self.assertVerdict(run, 'FAIL', 1)
        self.assertIn('detail: MainPID=', run.stdout)

    def test_detail_does_not_print_the_environment(self):
        self.setup_fds()
        run = self.run_check(args=('--detail',), env={'OPENROUTER_API_KEY': 'sk-secret-canary'})
        self.assertNotIn('sk-secret-canary', run.stdout + run.stderr)

    def test_usage_errors_exit_2(self):
        for argv in (['--runtime', '--root', '/x'], ['--detail'], ['--runtime', '--bogus']):
            with self.subTest(argv=argv):
                self.assertEqual(subprocess.run(['python3', '-B', str(CHECK), *argv], capture_output=True, text=True).returncode, 2)

    def test_runtime_never_writes_anything(self):
        self.setup_fds()
        self.config.write_text('{"ProcMonitorMethod": "ebpf"}')
        self.log.write_text('')
        before = sorted(p for p in self.dir.rglob('*'))
        mtimes = {p: p.lstat().st_mtime_ns for p in before}
        self.run_check(args=('--detail',), config=False, log=None)
        self.assertEqual(sorted(self.dir.rglob('*')), before)
        self.assertEqual({p: p.lstat().st_mtime_ns for p in before}, mtimes)


class PackagingModeUnchanged(base.ImageCheck):
    def test_packaging_output_is_byte_identical_in_shape(self):
        result = self.run_check(self.image())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f'PASS: all three OpenSnitch eBPF objects have ELF BPF headers; built for kernel {base.R72} '
                                        f'from OpenSnitch {"a" * 12}, hashes match the build manifest (runtime unverified)\n')
        self.assertEqual(result.stderr, '')

    def test_packaging_failure_still_goes_to_stderr_with_exit_1(self):
        result = self.run_check(self.image(kernel=base.R73))
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertTrue(result.stderr.startswith('FAIL: eBPF objects were built for kernel'))

    def test_default_root_is_slash(self):
        self.assertIn("args.root = args.root or '/'", CHECK.read_text())


class Wiring(unittest.TestCase):
    def test_this_file_runs_in_both_build_workflow_blocks_and_is_listed_for_agents(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(build.count('python3 tests/test-opensnitch-ebpf-runtime.py'), 2)
        self.assertIn('python3 tests/test-opensnitch-ebpf-runtime.py', (ROOT / 'AGENTS.md').read_text())


if __name__ == '__main__':
    unittest.main()
