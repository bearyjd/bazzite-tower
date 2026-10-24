#!/usr/bin/env python3
"""Offline fixtures for interval accounting and packaged ELF BPF objects."""
import os
from pathlib import Path
import runpy
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


if __name__ == '__main__':
    unittest.main()
