#!/usr/bin/env python3
"""Offline fixtures for interval accounting and packaged ELF BPF objects."""
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / 'system_files/usr/libexec'
RESOURCE = runpy.run_path(str(HELPERS / 'bazzite-tower-snitchwatch-resources'))
BPF = runpy.run_path(str(HELPERS / 'bazzite-tower-opensnitch-ebpf-check'))


class ResourceTests(unittest.TestCase):
    def sample(self, pid=10, start=123, cpu=100, stamp=0):
        return dict(pid=pid, start_ticks=start, cpu_ticks=cpu, monotonic=stamp,
                    rss_bytes=4096 + int(stamp), threads=2)

    def test_interval_uses_deltas_not_lifetime_or_machine_cpu(self):
        old = self.sample(cpu=1000000, stamp=100)
        new = self.sample(cpu=1000250, stamp=102.5)
        self.assertEqual(RESOURCE['interval'](old, new, 100), 100)
        new['cpu_ticks'] += 250
        self.assertEqual(RESOURCE['interval'](old, new, 100), 200)

    def test_restarts_and_nonmonotonic_counters(self):
        for changed in (self.sample(pid=11), self.sample(start=124)):
            self.assertIsNone(RESOURCE['interval'](self.sample(), changed, 100))
        for changed in (self.sample(cpu=99, stamp=1), self.sample(stamp=0)):
            with self.assertRaises(ValueError):
                RESOURCE['interval'](self.sample(), changed, 100)

    def test_proc_stat_with_parentheses_and_no_payload_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '10').mkdir()
            fields = ['0'] * 22
            fields[0], fields[11], fields[12], fields[17], fields[19], fields[21] = 'S', '20', '30', '7', '123', '8'
            (root / '10/stat').write_text('10 (a (tricky) process) ' + ' '.join(fields))
            row = RESOURCE['snapshot'](10, root, lambda: 5)
            self.assertEqual((row['cpu_ticks'], row['start_ticks'], row['threads']), (50, 123, 7))
            self.assertEqual(row['rss_bytes'], 8 * os.sysconf('SC_PAGE_SIZE'))
            with self.assertRaises(FileNotFoundError):
                RESOURCE['snapshot'](11, root)

    def test_finite_duration_series_restart_missing_permission(self):
        clock = [0.0]
        events = [self.sample(), self.sample(cpu=200, stamp=2), PermissionError(),
                  FileNotFoundError(), self.sample(start=456, cpu=20, stamp=8),
                  self.sample(start=456, cpu=220, stamp=10)]

        def read(_):
            event = events.pop(0)
            if isinstance(event, Exception):
                raise event
            return event

        def sleep(seconds):
            clock[0] += seconds

        report = RESOURCE['collect']({'daemon': 10}, 10, 2, read=read,
                                     now=lambda: clock[0], sleep=sleep)
        summary = report['summary']['daemon']
        self.assertEqual(report['duration_seconds'], 10)
        self.assertEqual(len(report['samples']), 6)
        self.assertEqual(summary['restarts_observed'], 1)
        self.assertEqual(summary['unavailable_samples'], 2)
        self.assertEqual(summary['valid_cpu_intervals'], 2)
        self.assertEqual(summary['cpu_core_percent_median'], 75 * 100 / os.sysconf('SC_CLK_TCK'))
        self.assertEqual(summary['cpu_core_percent_p95'], 100 * 100 / os.sysconf('SC_CLK_TCK'))
        self.assertEqual(summary['rss_initial_bytes'], 4096)
        self.assertEqual(summary['rss_final_bytes'], 4106)
        self.assertEqual(summary['rss_peak_bytes'], 4106)
        self.assertEqual(summary['threads_peak'], 2)
        self.assertIsNone(report['samples'][4]['cpu_core_percent'])
        self.assertEqual([r['status'] for r in report['samples']][2:4], ['permission-denied', 'missing'])

    def test_mainpid_replacement_and_pid_reuse_are_both_counted(self):
        clock = [0.0]
        pids = iter((10, 11, 11))
        starts = iter((1, 2, 3))
        def read(pid):
            return self.sample(pid=pid, start=next(starts), stamp=clock[0])
        def sleep(seconds):
            clock[0] += seconds
        report = RESOURCE['collect']({'unit': 'fixture.service'}, 2, 1,
                                    resolve=lambda _: next(pids), read=read,
                                    now=lambda: clock[0], sleep=sleep)
        self.assertEqual(report['summary']['unit']['restarts_observed'], 2)
        self.assertEqual(report['summary']['unit']['valid_cpu_intervals'], 0)

    def test_missing_mainpid_and_timeout_are_reported(self):
        with patch('subprocess.run') as run:
            run.return_value.stdout = '0\n'
            with self.assertRaises(ProcessLookupError):
                RESOURCE['unit_pid']('missing.service')
            self.assertEqual(run.call_args.kwargs['timeout'], 2)
            self.assertEqual(run.call_args.args[0][0], '/usr/bin/systemctl')
            self.assertEqual(run.call_args.kwargs['env'], {'PATH': '/usr/sbin:/usr/bin', 'LC_ALL': 'C'})
        clock = [0.0]
        def timeout(_):
            raise subprocess.TimeoutExpired('systemctl', 2)
        def sleep(seconds):
            clock[0] += seconds
        report = RESOURCE['collect']({'unit': 'slow.service'}, 1, 1, resolve=timeout,
                                    now=lambda: clock[0], sleep=sleep)
        self.assertEqual(report['summary']['unit']['unavailable_samples'], 2)
        self.assertTrue(all(row['status'] == 'unavailable' for row in report['samples']))
        self.assertIsNone(report['summary']['unit']['cpu_core_percent_median'])


class BpfPackagingTests(unittest.TestCase):
    def test_all_objects_and_invalid_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = root / 'usr/lib/opensnitchd/ebpf'
            objects.mkdir(parents=True)
            header = bytearray(64)
            header[:7] = b'\x7fELF\x02\x01\x01'
            struct.pack_into('<HHI', header, 16, 1, 247, 1)
            struct.pack_into('<H', header, 52, 64)
            for name in BPF['OBJECTS']:
                (objects / name).write_bytes(header)
            BPF['verify'](root)
            for name in BPF['OBJECTS']:
                path = objects / name
                for payload in (b'', b'not ELF', bytes(64), header[:20]):
                    path.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        BPF['verify'](root)
                wrong_arch = header.copy()
                struct.pack_into('<H', wrong_arch, 18, 62)
                path.write_bytes(wrong_arch)
                with self.assertRaises(ValueError):
                    BPF['verify'](root)
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    BPF['verify'](root)
                path.symlink_to(objects / 'missing')
                with self.assertRaises(ValueError):
                    BPF['verify'](root)
                path.unlink()
                os.mkfifo(path)
                with self.assertRaises(ValueError):
                    BPF['verify'](root)
                path.unlink()
                path.write_bytes(header)


class ProcFallbackRecipeTests(unittest.TestCase):
    """Run the RUNBOOK's real one-key proc fallback command (sans sudo/restart) against fixture configs."""

    SHIPPED = {'ProcMonitorMethod': 'ebpf', 'DefaultAction': 'allow', 'LogLevel': 2, 'MaxEvents': 150,
               'InterceptUnknown': False, 'Server': {'Address': 'unix:opensnitchd.sock'},
               'Ebpf': {'EventsWorkers': 8, 'QueueEventsSize': 0, 'ModulesPath': '/usr/lib/opensnitchd/ebpf'}}
    LOCK = '/run/lock/bazzite-tower-snitchwatch-migration.lock'

    @classmethod
    def setUpClass(cls):
        runbook = (ROOT / 'docs/RUNBOOK.md').read_text()
        cls.line = next(item for item in runbook.splitlines() if item.startswith("sudo sh -c 'umask 077; exec flock -n " + cls.LOCK))
        cls.command = cls.line.split(' && sudo systemctl restart opensnitch.service', 1)[0]

    def run_recipe(self, text, path_after=None, env=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'default-config.json'
            path.write_text(text)
            path.chmod(0o600)
            lock = Path(directory) / 'lock'
            command = self.command.removeprefix('sudo ').replace('/etc/opensnitchd/default-config.json', str(path)).replace(self.LOCK, str(lock))
            result = subprocess.run(['sh', '-c', command], capture_output=True, text=True, timeout=10, env=env)
            lock_mode = lock.stat().st_mode & 0o7777 if lock.exists() else None
            return result, path.read_text(), path.stat().st_mode & 0o7777, sorted(p.name for p in Path(directory).iterdir()), lock_mode

    def test_recipe_changes_exactly_one_key_and_restarts_under_the_migration_lock(self):
        self.assertIn('&& sudo systemctl restart opensnitch.service', self.line)
        result, text, mode, names, _ = self.run_recipe(json.dumps(self.SHIPPED))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(text), {**self.SHIPPED, 'ProcMonitorMethod': 'proc'})
        self.assertEqual(mode, 0o644)
        self.assertEqual(names, ['default-config.json', 'lock'])  # no leftover .new

    def test_a_freshly_created_lock_has_the_mode_the_helpers_require(self):
        _, _, _, _, lock_mode = self.run_recipe(json.dumps(self.SHIPPED))
        self.assertEqual(lock_mode, 0o600)
        for helper in ('bazzite-tower-snitchwatch-migrate', 'bazzite-tower-snitchwatch-deny'):
            self.assertIn('0o600', (ROOT / 'system_files/usr/libexec' / helper).read_text())

    def test_recipe_preserves_types_and_nested_keys(self):
        _, text, _, _, _ = self.run_recipe(json.dumps(self.SHIPPED))
        after = json.loads(text)
        self.assertEqual(json.dumps(after, sort_keys=True), json.dumps({**self.SHIPPED, 'ProcMonitorMethod': 'proc'}, sort_keys=True))
        self.assertIs(after['InterceptUnknown'], False)
        self.assertEqual(after['Ebpf']['ModulesPath'], '/usr/lib/opensnitchd/ebpf')

    def test_unparseable_config_is_left_untouched_and_leaves_no_new_file(self):
        result, text, mode, names, _ = self.run_recipe('{not json')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(text, '{not json')
        self.assertEqual(mode, 0o600)
        self.assertNotIn('default-config.json.new', names)

    def test_recipe_restores_the_label_when_restorecon_exists_and_works_without_it(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / 'bin'
            bin_dir.mkdir()
            log = Path(directory) / 'restorecon.log'
            fake = bin_dir / 'restorecon'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> ' + str(log) + '\n')
            fake.chmod(0o755)
            env = {'PATH': str(bin_dir) + ':' + os.environ['PATH']}
            result, _, _, _, _ = self.run_recipe(json.dumps(self.SHIPPED), env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(log.read_text(), r'^-- .*default-config\.json\n$')
        with tempfile.TemporaryDirectory() as directory:  # a PATH holding only the recipe's tools: no restorecon
            for tool in ('sh', 'jq', 'flock', 'chmod', 'mv', 'rm'):
                (Path(directory) / tool).symlink_to(shutil.which(tool))
            result, text, _, _, _ = self.run_recipe(json.dumps(self.SHIPPED), env={'PATH': directory})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(text)['ProcMonitorMethod'], 'proc')


class ShippedConfigTests(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / 'system_files/usr/share/bazzite-tower' / name).read_text())

    def test_both_profiles_ship_ebpf_with_the_pinned_modules_path_and_are_otherwise_equal(self):
        legacy, system = (self.load(name) for name in ('opensnitchd-default-config.json', 'opensnitchd-system-bridge-config.json'))
        for config in (legacy, system):
            self.assertEqual(config['ProcMonitorMethod'], 'ebpf')
            self.assertEqual(config['Ebpf']['ModulesPath'], '/usr/lib/opensnitchd/ebpf')
            self.assertEqual(config['DefaultAction'], 'allow')
        legacy['Server']['Address'] = system['Server']['Address']
        self.assertEqual(legacy, system)


if __name__ == '__main__':
    unittest.main()
