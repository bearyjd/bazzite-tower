#!/usr/bin/env python3
"""Offline tests for `bazzite-tower-opensnitch-ebpf-check --runtime`.

Fakes only: a procfs tree of dangling symlinks, a config, a log and `systemctl`/`bpftool`
scripts passed through the test-only --systemctl/--bpftool flags. No daemon, kernel, root, container or network. The fd link
name and log strings are UNVERIFIED on the real kernel/daemon; the constants are
asserted by name so a calibration edit is a visible, deliberate test change.
"""
import contextlib
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zoneinfo

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'system_files/usr/libexec/bazzite-tower-opensnitch-ebpf-check'
_spec = importlib.util.spec_from_file_location('ebpf_tests', Path(__file__).with_name('test-snitchwatch-ebpf.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

BPF = 'anon_inode:bpf_link'
FALLBACK = 'error starting ebpf monitor method'
PARTIAL = 'opensnitch-procs.o not available'
EPOCH = 1794909600  # 2026-11-17 10:00:00 UTC
START = f'@{EPOCH}'
PID = 4242
ENV_FLAG = 'BAZZITE_TOWER_EBPF_CHECK_TESTING'
FAKE_SYSTEMCTL = '''#!/bin/sh
d=${0%/*}
case "$*" in
  *MainPID*)
    [ -e "$d/mainpid_fail" ] && exit 1
    if [ -e "$d/mainpid2" ]; then
      n=$(cat "$d/calls" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$d/calls"
      [ "$n" -gt 1 ] && { cat "$d/mainpid2"; exit 0; }
    fi
    cat "$d/mainpid" ;;
  *ActiveEnterTimestamp*) cat "$d/start" ;;
  *) exit 1 ;;
esac
'''
FAKE_BPFTOOL = '''#!/bin/sh
cat "${0%/*}/bpftool.json"
'''


def stamp(epoch, zone='UTC'):
    return datetime.datetime.fromtimestamp(epoch, zoneinfo.ZoneInfo(zone)).strftime('[%Y-%m-%d %H:%M:%S]')


# A log that reaches back to the unit start: the default for tests that are not about log coverage.
COVERING_LOG = f'{stamp(EPOCH + 1)}  INF  daemon started\n'


def load_module():
    loader = importlib.machinery.SourceFileLoader('ebpf_check_inproc', str(CHECK))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class Runtime(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMPDIR') or None)
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        for name, text in (('systemctl', FAKE_SYSTEMCTL), ('bpftool', FAKE_BPFTOOL)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.proc = self.dir / 'proc'
        self.config = self.dir / 'default-config.json'
        self.log = self.dir / 'opensnitchd.log'
        self.profile_file = self.dir / 'bridge-profile'  # absent by default: unreadable profile, conservative floor

    def set_profile(self, profile):
        self.profile_file.write_text(f'{profile}\n')

    def setup_fds(self, bpf=2, other=('/dev/null', 'socket:[123]', 'socket:[456]', 'pipe:[9]'), pid=PID, exe='/usr/bin/opensnitchd'):
        fd = self.proc / str(pid) / 'fd'
        fd.mkdir(parents=True)
        for index, target in enumerate([BPF] * bpf + list(other)):
            os.symlink(target, fd / str(index))
        if exe:
            os.symlink(exe, self.proc / str(pid) / 'exe')
        return fd

    def argv(self, *extra, log=None, bpftool=False):
        return ['--runtime', '--config', str(self.config), '--log', str(log or self.log), '--proc-root', str(self.proc),
                '--profile-file', str(self.profile_file), '--systemctl', str(self.bin / 'systemctl'), '--bpftool', str(self.bin / 'bpftool' if bpftool else self.dir / 'no-bpftool'),
                *extra]

    def run_check(self, method='ebpf', pid=PID, log=COVERING_LOG, start=START, mainpid_fail=False, args=(), config=True, env=None,
                  log_utc=True, config_text=None, bpftool=None, logpath=None, pid2=None):
        if config:
            self.config.write_text(config_text if config_text is not None else
                                   json.dumps({'LogUTC': log_utc, **({'ProcMonitorMethod': method} if method is not None else {})}))
        if log is not None:
            self.log.write_text(log)
        (self.bin / 'mainpid').write_text(f'{pid}\n')
        (self.bin / 'start').write_text(f'{start}\n')
        if mainpid_fail:
            (self.bin / 'mainpid_fail').write_text('')
        if pid2 is not None:
            (self.bin / 'mainpid2').write_text(f'{pid2}\n')
        if bpftool is not None:
            (self.bin / 'bpftool.json').write_text(bpftool)
        return subprocess.run(['python3', '-B', str(CHECK), *self.argv(*args, log=logpath, bpftool=bpftool is not None)],
                              capture_output=True, text=True, timeout=60, env={**os.environ, **(env or {})})

    def result(self, run):
        last = run.stdout.rstrip('\n').splitlines()[-1]
        self.assertTrue(last.startswith('RESULT: '), run.stdout + run.stderr)
        return last.split(': ', 2)[1], last

    def assertVerdict(self, run, verdict, code, *needles):
        got, line = self.result(run)
        self.assertEqual((got, run.returncode), (verdict, code), run.stdout + run.stderr)
        for needle in needles:
            self.assertIn(needle, line)

    def test_ebpf_with_bpf_link_fds_passes_and_prints_the_count(self):
        self.setup_fds(bpf=3)
        self.assertVerdict(self.run_check(), 'PASS', 0, '3 ' + BPF)

    def test_pass_says_the_fd_floor_is_uncalibrated_while_it_is_the_placeholder(self):
        self.setup_fds(bpf=1)
        self.assertVerdict(self.run_check(), 'PASS', 0, '(fd floor UNCALIBRATED: gate A pending)')

    def test_ebpf_with_zero_bpf_link_fds_is_a_silent_fallback(self):
        self.setup_fds(bpf=0)
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'silent /proc fallback')

    def test_live_kernel_fd_set_with_closed_prog_fds_passes(self):
        # Observed live (7.3.4-ogc8.1, opensnitchCommit a1353848): the Go loader closes the program fds after
        # attaching, so a healthy eBPF load holds bpf_link/perf_event/bpf-map fds and zero bpf-prog fds.
        live = ['anon_inode:[perf_event]'] * 17 + ['anon_inode:bpf-map'] * 10 + ['/dev/null', 'socket:[123]']
        self.setup_fds(bpf=17, other=live)
        self.assertVerdict(self.run_check(), 'PASS', 0, '17 ' + BPF, 'no fallback warning since start')

    def test_no_bpf_link_fds_is_a_silent_fallback_even_with_maps_and_perf_events(self):
        other = ['anon_inode:[perf_event]'] * 17 + ['anon_inode:bpf-map'] * 10 + ['/dev/null']
        self.setup_fds(bpf=0, other=other)
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'silent /proc fallback', 'holds no ' + BPF + ' fd')

    def test_bpf_prog_fds_alone_are_not_counted(self):
        self.setup_fds(bpf=0, other=['anon_inode:bpf-prog'] * 3 + ['/dev/null'])
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'silent /proc fallback')

    def test_fallback_warning_after_unit_start_fails_even_with_fds(self):
        self.setup_fds()
        log = f'{stamp(EPOCH + 5)}  WRN  {FALLBACK}: boom\n'
        self.assertVerdict(self.run_check(log=log), 'FAIL', 1, 'silent /proc fallback', FALLBACK)

    def test_fallback_warning_before_unit_start_is_ignored(self):
        self.setup_fds()
        log = f'{stamp(EPOCH - 1)}  WRN  {FALLBACK}: old\n{stamp(EPOCH + 7)}  INF  ok\n'
        self.assertVerdict(self.run_check(log=log), 'PASS', 0)

    def test_untimestamped_continuation_inherits_the_previous_line_time(self):
        self.setup_fds()
        old = f'{stamp(EPOCH - 3600)}  WRN  boom\n{FALLBACK} (continued)\n'
        self.assertVerdict(self.run_check(log=old), 'PASS', 0)

    def test_unparseable_unit_start_counts_every_log_line_and_says_so(self):
        self.setup_fds()
        log = f'{stamp(EPOCH - 3600)}  WRN  {FALLBACK}\n'
        for start in ('', 'garbage', '@0'):
            with self.subTest(start=start):
                self.assertVerdict(self.run_check(log=log, start=start), 'FAIL', 1,
                                   '(start time unknown: log not time-bounded, may be stale; restart the daemon and re-run)')

    def test_stale_start_branch_is_visible_through_detail(self):
        self.setup_fds()
        run = self.run_check(log=f'{stamp(EPOCH - 3600)}  WRN  {FALLBACK}\n', start='', args=('--detail',))
        self.assertVerdict(run, 'FAIL', 1, 'start time unknown')
        self.assertIn('ActiveEnterTimestamp=unknown', run.stdout)
        self.assertIn(f'detail: log {stamp(EPOCH - 3600)}  WRN  {FALLBACK}', run.stdout)

    def test_procs_object_warning_is_partial_mode_warn(self):
        self.setup_fds()
        log = f'{stamp(EPOCH + 5)}  WRN  {PARTIAL}\n'
        self.assertVerdict(self.run_check(log=log), 'WARN', 0, 'partial eBPF', PARTIAL)

    def test_partial_warning_with_zero_fds_is_a_fail(self):
        self.setup_fds(bpf=0)
        self.assertVerdict(self.run_check(log=f'{stamp(EPOCH + 5)}  WRN  {PARTIAL}\n'), 'FAIL', 1, 'silent /proc fallback')

    def test_fallback_outranks_partial_warning(self):
        self.setup_fds()
        log = f'{stamp(EPOCH + 5)}  WRN  {PARTIAL}\n{stamp(EPOCH + 6)}  ERR  {FALLBACK}\n'
        self.assertVerdict(self.run_check(log=log), 'FAIL', 1)

    def test_system_profile_floor_is_17_and_clean_pass_has_no_uncalibrated_note(self):
        self.set_profile('system')
        self.setup_fds(bpf=17)
        run = self.run_check()
        self.assertVerdict(run, 'PASS', 0, '17 ' + BPF)
        self.assertNotIn('UNCALIBRATED', run.stdout)

    def test_system_profile_below_17_warns(self):
        self.set_profile('system')
        self.setup_fds(bpf=16)
        self.assertVerdict(self.run_check(), 'WARN', 0, 'only 16 ' + BPF, 'below the calibrated floor 17')

    def test_system_profile_above_17_passes(self):
        self.set_profile('system')
        self.setup_fds(bpf=18)
        run = self.run_check()
        self.assertVerdict(run, 'PASS', 0, '18 ' + BPF)
        self.assertNotIn('UNCALIBRATED', run.stdout)

    def test_legacy_profile_keeps_the_uncalibrated_floor_of_one(self):
        self.set_profile('legacy')
        for count in (1, 17):
            with self.subTest(count=count):
                shutil.rmtree(self.proc, ignore_errors=True)
                self.setup_fds(bpf=count)
                self.assertVerdict(self.run_check(), 'PASS', 0, f'{count} ' + BPF, '(fd floor UNCALIBRATED: gate A pending)')

    def test_unknown_profile_uses_the_conservative_floor(self):
        self.set_profile('bogus')
        self.setup_fds(bpf=1)
        self.assertVerdict(self.run_check(), 'PASS', 0, '(fd floor UNCALIBRATED: gate A pending)')

    def test_floor_constants_are_pinned(self):
        loaded = base.load(CHECK)
        self.assertEqual(loaded['EBPF_PROG_FD_FLOORS']['system'], 17)
        self.assertEqual(loaded['EBPF_PROG_FD_FLOORS']['legacy'], 1)
        self.assertEqual(loaded['EBPF_PROG_FD_FLOOR'], 1)
        self.assertEqual(loaded['BPF_PROG_LINK'], BPF)

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
        run = subprocess.run(['python3', '-B', str(CHECK), '--runtime', '--config', str(self.config), '--proc-root', str(self.proc),
                              '--systemctl', str(self.dir / 'absent')], capture_output=True, text=True)
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
        log = f'{stamp(EPOCH + 5)}  WRN  {PARTIAL}\n{stamp(EPOCH - 3600)}  WRN  {FALLBACK}\n'
        run = self.run_check(log=log, args=('--detail',))
        lines = run.stdout.splitlines()
        self.assertEqual(self.result(run)[0], 'WARN')
        for needle in (f'MainPID={PID}', 'ActiveEnterTimestamp=', "ProcMonitorMethod='ebpf'", f'{BPF}=2', 'fd total=6',
                       f'fd      2 {BPF}', 'fd      2 socket:[*]', 'bpftool', f'log {stamp(EPOCH + 5)}  WRN  {PARTIAL}'):
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

    def test_detail_escapes_log_lines_and_fd_targets(self):
        self.setup_fds(other=('/tmp/evil\x1b[2J\x07',))
        log = f'{stamp(EPOCH + 5)}  WRN  {PARTIAL} \x1b[31mRESULT: PASS: forged\r\n'
        run = self.run_check(log=log, args=('--detail',), env={'PYTHONIOENCODING': 'ascii'})
        self.assertEqual(self.result(run)[0], 'WARN')
        self.assertNotIn('\x1b', run.stdout)
        self.assertNotIn('\x07', run.stdout)
        self.assertEqual(len([l for l in run.stdout.splitlines() if l.startswith('RESULT: ')]), 1)
        self.assertIn('\\x1b[31m', run.stdout)

    def test_usage_errors_exit_2(self):
        for argv in (['--runtime', '--root', '/x'], ['--detail'], ['--runtime', '--bogus']):
            with self.subTest(argv=argv):
                self.assertEqual(subprocess.run(['python3', '-B', str(CHECK), *argv], capture_output=True, text=True).returncode, 2)

    def test_runtime_never_writes_anything(self):
        self.setup_fds()
        self.config.write_text('{"ProcMonitorMethod": "ebpf"}')
        self.log.write_text('')
        (self.bin / 'mainpid').write_text(f'{PID}\n')
        (self.bin / 'start').write_text(f'{START}\n')
        before = sorted(p for p in self.dir.rglob('*'))
        mtimes = {p: p.lstat().st_mtime_ns for p in before}
        run = subprocess.run(['python3', '-B', str(CHECK), *self.argv('--detail')], capture_output=True, text=True)
        self.assertEqual(self.result(run)[0] in ('PASS', 'WARN'), True)
        self.assertEqual(sorted(self.dir.rglob('*')), before)
        self.assertEqual({p: p.lstat().st_mtime_ns for p in before}, mtimes)

    # --- timezone: the start is an epoch, log stamps are UTC iff the config says LogUTC ---
    def test_current_run_is_seen_and_previous_run_ignored_in_every_zone_with_utc_logs(self):
        self.setup_fds()
        for zone in ('UTC', 'Pacific/Auckland', 'America/New_York', 'Asia/Kolkata'):
            with self.subTest(zone=zone):
                now = f'{stamp(EPOCH + 5)}  WRN  {FALLBACK}\n'
                old = f'{stamp(EPOCH - 1)}  WRN  {FALLBACK}\n{stamp(EPOCH + 9)}  INF  up\n'
                self.assertVerdict(self.run_check(log=now, env={'TZ': zone}), 'FAIL', 1, 'silent /proc fallback')
                self.assertVerdict(self.run_check(log=old, env={'TZ': zone}), 'PASS', 0)

    def test_current_run_is_seen_and_previous_run_ignored_with_local_logs(self):
        self.setup_fds()
        for zone in ('UTC', 'Pacific/Auckland', 'America/New_York'):
            with self.subTest(zone=zone):
                now = f'{stamp(EPOCH + 5, zone)}  WRN  {FALLBACK}\n'
                old = f'{stamp(EPOCH - 1, zone)}  WRN  {FALLBACK}\n{stamp(EPOCH + 9, zone)}  INF  up\n'
                self.assertVerdict(self.run_check(log=now, env={'TZ': zone}, log_utc=False), 'FAIL', 1, 'silent /proc fallback')
                self.assertVerdict(self.run_check(log=old, env={'TZ': zone}, log_utc=False), 'PASS', 0)

    def test_log_stamps_are_not_read_as_local_when_the_config_says_utc(self):
        # east of UTC a naive-local parse would put a 09:59 UTC line AFTER the 10:00 UTC start (false FAIL)
        self.setup_fds()
        old = f'{stamp(EPOCH - 60)}  WRN  {FALLBACK}\n{stamp(EPOCH + 9)}  INF  up\n'
        self.assertVerdict(self.run_check(log=old, env={'TZ': 'Pacific/Auckland'}), 'PASS', 0)

    # --- log coverage ---
    def test_log_that_starts_after_the_unit_start_warns_instead_of_passing(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(log=f'{stamp(EPOCH + 600)}  INF  hello\n'), 'WARN', 0, 'log does not cover the unit start (rotated?)')

    def test_empty_log_with_a_known_start_warns_instead_of_passing(self):
        # rotated or truncated after the daemon started, nothing written since: the startup fallback line may be gone
        self.setup_fds()
        self.assertVerdict(self.run_check(log=''), 'WARN', 0, 'log does not cover the unit start')

    def test_log_without_any_parseable_timestamp_warns_instead_of_passing(self):
        # e.g. LogMicro changes the stamp format: coverage cannot be shown
        self.setup_fds()
        self.assertVerdict(self.run_check(log='[2026-11-17 10:00:01.123456]  INF  hello\nno stamp here\n'), 'WARN', 0, 'log does not cover the unit start')

    def test_log_covering_the_start_passes(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(log=f'{stamp(EPOCH + 1)}  INF  hello\n'), 'PASS', 0)

    def test_truncated_tail_that_no_longer_reaches_the_start_warns(self):
        self.setup_fds()
        text = ''.join(f'{stamp(EPOCH + 100 + i)}  INF  filler line number {i}\n' for i in range(40))
        copy = self.dir / 'check'
        copy.write_text(CHECK.read_text().replace('LOG_TAIL_BYTES = 8 * 1024 * 1024', 'LOG_TAIL_BYTES = 300'))
        self.config.write_text('{"ProcMonitorMethod": "ebpf", "LogUTC": true}')
        self.log.write_text(text)
        (self.bin / 'mainpid').write_text(f'{PID}\n')
        (self.bin / 'start').write_text(f'{START}\n')
        run = subprocess.run(['python3', '-B', str(copy), *self.argv()], capture_output=True, text=True)
        self.assertVerdict(run, 'WARN', 0, 'log does not cover the unit start')

    # --- bpftool names ---
    def test_bpftool_program_names_are_added_to_the_reason(self):
        self.setup_fds()
        listing = json.dumps([{'type': 'kprobe', 'name': 'kprobe__tcp_v4', 'pids': [{'pid': PID, 'comm': 'opensnitchd'}]},
                              {'type': 'kprobe', 'name': 'other_proc', 'pids': [{'pid': 1}]}])
        run = self.run_check(bpftool=listing)
        self.assertVerdict(run, 'PASS', 0, 'kprobe__tcp_v4')
        self.assertNotIn('other_proc', run.stdout)

    def test_odd_bpftool_output_never_crashes_the_check(self):
        self.setup_fds()
        for text in ('[1, "x", {"pids": 5}, {"pids": [3, null], "name": 7}]', '{"a": 1}', 'not json', ''):
            with self.subTest(text=text):
                self.assertVerdict(self.run_check(bpftool=text), 'PASS', 0)

    # --- robustness ---
    def test_malformed_log_timestamp_is_skipped_not_fatal(self):
        self.setup_fds()
        log = f'[2026-99-99 99:99:99]  WRN  junk\n{stamp(EPOCH + 1)}  INF  ok\n'
        run = self.run_check(log=log)
        self.assertVerdict(run, 'PASS', 0)
        self.assertNotIn('Traceback', run.stderr)

    def test_malformed_timestamp_on_a_fallback_line_still_fails_closed(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(log=f'[2026-99-99 99:99:99]  WRN  {FALLBACK}\n'), 'FAIL', 1, 'silent /proc fallback')

    def test_deeply_nested_config_gives_an_internal_error_result(self):
        self.setup_fds()
        run = self.run_check(config_text='[' * 400000)
        self.assertVerdict(run, 'FAIL', 1, 'internal error (RecursionError)')

    def test_non_ascii_output_never_breaks_the_result_line(self):
        self.setup_fds()
        run = self.run_check(log=f'{stamp(EPOCH + 5)}  WRN  é {PARTIAL}\n', args=('--detail',), env={'PYTHONIOENCODING': 'ascii'})
        self.assertVerdict(run, 'WARN', 0)

    # --- log opening ---
    def test_fifo_and_dev_zero_logs_are_rejected_without_hanging(self):
        self.setup_fds()
        fifo = self.dir / 'fifo'
        os.mkfifo(fifo)
        for path in (fifo, Path('/dev/zero')):
            with self.subTest(path=str(path)):
                self.assertVerdict(self.run_check(log=None, logpath=path), 'WARN', 0, 'unreadable')

    def test_symlinked_log_is_rejected(self):
        self.setup_fds()
        real = self.dir / 'real.log'
        real.write_text('')
        self.log.symlink_to(real)
        self.assertVerdict(self.run_check(log=None), 'WARN', 0, 'unreadable')

    # --- daemon identity ---
    def test_daemon_exe_must_be_opensnitchd(self):
        self.setup_fds(exe='/usr/bin/bash')
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'opensnitchd')

    def test_daemon_exe_with_deleted_suffix_is_still_opensnitchd(self):
        self.setup_fds(exe='/usr/bin/opensnitchd (deleted)')
        self.assertVerdict(self.run_check(), 'PASS', 0)

    def test_missing_exe_link_fails(self):
        self.setup_fds(exe=None)
        self.assertVerdict(self.run_check(), 'FAIL', 1, 'cannot read')

    def test_mainpid_changing_during_the_scan_fails(self):
        self.setup_fds()
        self.setup_fds(pid=777)
        self.assertVerdict(self.run_check(pid2=777), 'FAIL', 1, 'daemon pid changed during the check')

    def test_stable_mainpid_is_fine(self):
        self.setup_fds()
        self.assertVerdict(self.run_check(pid2=PID), 'PASS', 0)


class InProcess(unittest.TestCase):
    def setUp(self):
        self.m = load_module()
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMPDIR') or None)
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def fds(self, names=('0', '1', '2'), pid=5):
        fd = self.dir / str(pid) / 'fd'
        fd.mkdir(parents=True)
        for name in names:
            os.symlink(BPF, fd / name)

    def decide(self, **over):
        facts = dict(method='ebpf', config='/c', log='/l', pid=7, problem=None, count=3, fallback=0, partial=0, log_readable=True,
                     covered=True, started_label='2026-11-17 10:00:00 UTC', programs=(), floor=1)
        return self.m.decide(**{**facts, **over})

    # fd race
    def test_fd_vanishing_mid_scan_is_skipped(self):
        self.fds()
        real = os.readlink

        def flaky(path, *a, **k):
            if str(path).endswith('/1'):
                raise FileNotFoundError(path)
            return real(path, *a, **k)
        with mock.patch.object(os, 'readlink', flaky):
            self.assertEqual(self.m.fd_links(self.dir, 5), [BPF, BPF])

    def test_pid_dying_mid_scan_is_skipped_per_entry(self):
        self.fds()
        with mock.patch.object(os, 'readlink', side_effect=ProcessLookupError()):
            self.assertEqual(self.m.fd_links(self.dir, 5), [])

    def test_fd_directory_vanishing_is_fatal_with_a_reason(self):
        self.fds()
        with mock.patch.object(Path, 'iterdir', side_effect=FileNotFoundError('gone')):
            with self.assertRaisesRegex(ValueError, 'cannot read .*fd'):
                self.m.fd_links(self.dir, 5)

    def test_other_per_entry_errors_stay_fatal(self):
        self.fds()
        with mock.patch.object(os, 'readlink', side_effect=PermissionError()):
            with self.assertRaisesRegex(ValueError, 'cannot read'):
                self.m.fd_links(self.dir, 5)

    def test_end_to_end_pass_when_an_fd_vanishes(self):
        fd = self.dir / 'proc' / '4242' / 'fd'
        fd.mkdir(parents=True)
        for name in '012':
            os.symlink(BPF, fd / name)
        os.symlink('/usr/bin/opensnitchd', self.dir / 'proc' / '4242' / 'exe')
        (self.dir / 'systemctl').write_text(FAKE_SYSTEMCTL)
        (self.dir / 'systemctl').chmod(0o755)
        (self.dir / 'mainpid').write_text('4242\n')
        (self.dir / 'start').write_text(f'{START}\n')
        (self.dir / 'c.json').write_text('{"ProcMonitorMethod": "ebpf", "LogUTC": true}')
        (self.dir / 'l.log').write_text(COVERING_LOG)
        real = os.readlink

        def flaky(path, *a, **k):
            if str(path).endswith('/fd/1'):
                raise FileNotFoundError(path)
            return real(path, *a, **k)
        out = io.StringIO()
        with mock.patch.object(os, 'readlink', flaky), contextlib.redirect_stdout(out):
            code = self.m.main(['--runtime', '--config', str(self.dir / 'c.json'), '--log', str(self.dir / 'l.log'),
                                '--proc-root', str(self.dir / 'proc'), '--systemctl', str(self.dir / 'systemctl'),
                                '--bpftool', str(self.dir / 'none'), '--profile-file', str(self.dir / 'no-profile')])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn('RESULT: PASS', out.getvalue())
        self.assertIn('2 ' + BPF, out.getvalue())

    # decision table
    def test_decide_table(self):
        cases = [
            (dict(problem='cannot read x'), 'FAIL', 'cannot read x'),
            (dict(method='proc'), 'WARN', 'documented'),
            (dict(method='proc', problem='x'), 'FAIL', 'x'),
            (dict(method=None), 'FAIL', 'expected ebpf'),
            (dict(method='audit'), 'FAIL', 'expected ebpf'),
            (dict(fallback=1), 'FAIL', 'silent /proc fallback'),
            (dict(fallback=1, count=0), 'FAIL', 'silent /proc fallback'),
            (dict(count=0), 'FAIL', 'holds no'),
            (dict(count=0, partial=2), 'FAIL', 'holds no'),
            (dict(partial=1), 'WARN', 'partial eBPF'),
            (dict(partial=1, covered=False), 'WARN', 'partial eBPF'),
            (dict(covered=False), 'WARN', 'log does not cover the unit start (rotated?)'),
            (dict(count=2, floor=5), 'WARN', 'below the calibrated floor 5'),
            (dict(log_readable=False), 'WARN', 'unreadable'),
            (dict(), 'PASS', '3 ' + BPF + ' fds'),
            (dict(floor=1), 'PASS', '(fd floor UNCALIBRATED: gate A pending)'),
            (dict(count=6, floor=5), 'PASS', 'no fallback warning'),
            (dict(programs=('a', 'b')), 'PASS', 'a, b'),
            (dict(programs=('a',), partial=1), 'WARN', 'a'),
        ]
        for over, level, needle in cases:
            with self.subTest(over=over):
                got = self.decide(**over)
                self.assertEqual(got[0], level, got)
                self.assertIn(needle, got[1])

    def test_system_floor_drops_the_placeholder_note(self):
        self.assertNotIn('UNCALIBRATED', self.decide(count=17, floor=self.m.EBPF_PROG_FD_FLOORS['system'])[1])

    def test_calibrated_floor_drops_the_placeholder_note(self):
        self.assertNotIn('UNCALIBRATED', self.decide(count=6, floor=5)[1])

    def test_stale_start_reason(self):
        level, reason = self.decide(fallback=2, started_label=None)
        self.assertEqual(level, 'FAIL')
        self.assertIn('start time unknown: log not time-bounded, may be stale; restart the daemon and re-run', reason)
        self.assertNotIn('start time unknown', self.decide(fallback=2)[1])

    def test_decide_is_pure(self):
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('no subprocess')), mock.patch.object(os, 'readlink', side_effect=AssertionError):
            self.assertEqual(self.decide()[0], 'PASS')

    # internal error
    def test_any_exception_still_ends_in_a_result_line(self):
        for error in (AttributeError('x'), RecursionError(), UnicodeEncodeError('ascii', 'a', 0, 1, 'x'), KeyError('k')):
            with self.subTest(error=type(error).__name__):
                out = io.StringIO()
                with mock.patch.dict(self.m.main.__globals__, {'evaluate': mock.Mock(side_effect=error)}), contextlib.redirect_stdout(out):
                    code = self.m.main(['--runtime'])
                self.assertEqual(code, 1)
                self.assertEqual(out.getvalue().splitlines()[-1], f'RESULT: FAIL: internal error ({type(error).__name__})')

    # interpreter / environment pinning
    def test_shebang_isolated_interpreter(self):
        self.assertEqual(CHECK.read_text().splitlines()[0], '#!/usr/bin/python3 -I')

    def test_children_run_with_absolute_paths_and_a_fixed_environment(self):
        calls = []

        def record(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout='@1794909600\n', stderr='')
        with mock.patch.object(subprocess, 'run', record):
            self.assertEqual(self.m.unit_property(self.m.SYSTEMCTL, 'ActiveEnterTimestamp'), '@1794909600')
        argv, kwargs = calls[0]
        self.assertEqual(argv, ['/usr/bin/systemctl', 'show', '--timestamp=unix', '-p', 'ActiveEnterTimestamp', '--value', 'opensnitch.service'])
        self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/usr/sbin', 'LC_ALL': 'C'})
        self.assertEqual(self.m.BPFTOOL, '/usr/bin/bpftool')

    def test_bpftool_is_called_by_absolute_path_with_the_fixed_environment(self):
        tool = self.dir / 'bpftool'
        tool.write_text('#!/bin/sh\n')
        tool.chmod(0o755)
        calls = []

        def record(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout='[]', stderr='')
        with mock.patch.object(subprocess, 'run', record):
            self.assertEqual(self.m.bpftool_programs(str(tool), 1), [])
        self.assertEqual(calls[0][0], [str(tool), 'prog', 'show', '--json'])
        self.assertEqual(calls[0][1]['env'], {'PATH': '/usr/bin:/usr/sbin', 'LC_ALL': 'C'})

    def test_tail_read_is_capped_even_if_the_file_keeps_growing(self):
        log = self.dir / 'growing.log'
        log.write_text('x\n')
        calls = []

        def endless(fd, size):
            calls.append(size)
            if len(calls) > 50:
                raise AssertionError('read past the cap')
            return b'y' * 1000
        with mock.patch.dict(self.m.read_tail.__globals__, {'LOG_TAIL_BYTES': 5000}), mock.patch.object(os, 'read', endless):
            lines = self.m.read_tail(str(log))
        self.assertEqual(sum(len(line) for line in lines), 5000)

    # test-only flags
    def test_test_only_flags_are_refused_for_root_without_the_testing_variable(self):
        for flag in ('--config', '--log', '--proc-root', '--systemctl', '--bpftool', '--profile-file'):
            with self.subTest(flag=flag), mock.patch.object(os, 'geteuid', return_value=0), \
                    mock.patch.dict(os.environ, {}, clear=False), contextlib.redirect_stderr(io.StringIO()) as err:
                os.environ.pop(ENV_FLAG, None)
                with self.assertRaises(SystemExit) as raised:
                    self.m.main(['--runtime', flag, '/x'])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(ENV_FLAG, err.getvalue())

    def test_test_only_flags_are_allowed_for_root_with_the_testing_variable(self):
        with mock.patch.object(os, 'geteuid', return_value=0), mock.patch.dict(os.environ, {ENV_FLAG: '1'}), \
                mock.patch.dict(self.m.main.__globals__, {'evaluate': mock.Mock(return_value=('PASS', 'ok'))}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(['--runtime', '--config', '/x']), 0)

    def test_root_without_flags_runs_normally(self):
        with mock.patch.object(os, 'geteuid', return_value=0), mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.dict(self.m.main.__globals__, {'evaluate': mock.Mock(return_value=('PASS', 'ok'))}), contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop(ENV_FLAG, None)
            self.assertEqual(self.m.main(['--runtime']), 0)


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
        module = load_module()
        seen = []
        with mock.patch.dict(module.main.__globals__, {'verify_image': lambda root: seen.append(root) or 'detail'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main([]), 0)
        self.assertEqual(seen, ['/'])


class Wiring(unittest.TestCase):
    def test_this_file_runs_in_both_build_workflow_blocks_and_is_listed_for_agents(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(build.count('python3 tests/test-opensnitch-ebpf-runtime.py'), 2)
        self.assertIn('python3 tests/test-opensnitch-ebpf-runtime.py', (ROOT / 'AGENTS.md').read_text())


if __name__ == '__main__':
    unittest.main()
