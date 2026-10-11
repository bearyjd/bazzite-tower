#!/usr/bin/env python3
"""Gate reporting and safety boundaries, without running a real daemon."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('gate', Path(__file__).resolve().parents[1] / 'scripts/snitchwatch-gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def test_snapshot_hashes_and_safe_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'inputs'
            records, adapted = gate.snapshot(gate.ROOT, target)
            for name, record in records.items():
                self.assertEqual(record['sha256'], gate.digest((gate.ROOT / name).read_bytes()))
            for name, expected in adapted.items():
                data = (target / name).read_bytes()
                self.assertEqual(expected, gate.digest(data))
                self.assertNotIn(b'rm -rf', data)
                self.assertNotEqual(expected, records[name]['sha256'])

    def test_changed_cleanup_contract_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'test.sh'
            path.write_text('rm -rf -- "$T"\n')
            with mock.patch.object(gate, 'INPUTS', ('test.sh',)), mock.patch.object(
                    gate, 'ADAPTATIONS', {'test.sh': (('T', 2),)}):
                with self.assertRaisesRegex(ValueError, 'cleanup contract changed'):
                    gate.snapshot(root, root / 'snapshot')

    def test_cleanup_bounds_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scratch = root / 'scratch'
            scratch.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            child = scratch / 'child'
            child.mkdir()
            link = scratch / 'link'
            link.symlink_to(outside, target_is_directory=True)
            with mock.patch.dict(os.environ, SNITCHWATCH_GATE_SCRATCH=str(scratch)):
                for path in (scratch, outside, link):
                    with self.assertRaises(ValueError):
                        gate.cleanup(path)
                gate.cleanup(child)
                self.assertFalse(child.exists())
                self.assertTrue(outside.exists())

    def test_summary_and_isolation_fail_closed(self):
        for states, overall, exit_code in (
                (['PASS', 'PASS', 'PASS'], 'PASS', 0),
                (['PASS', 'FAIL', 'PASS'], 'FAIL', 1),
                (['FAIL'], 'BLOCKED', 2),
                (['PASS', 'BLOCKED', 'PASS'], 'BLOCKED', 2)):
            with self.subTest(overall=overall), tempfile.TemporaryDirectory() as directory:
                evidence = Path(directory)
                commands = []
                outcomes = iter(states)

                def execute(command, cwd, env, log, timeout=120):
                    commands.append(command)
                    self.assertNotIn('HELPER', env)
                    self.assertNotIn('PYTHONPATH', env)
                    state = next(outcomes)
                    log.write_text('FAIL: fixture refusal\n' if state == 'FAIL' else 'ok\n')
                    return state, 1 if state == 'FAIL' else 0

                with mock.patch.object(gate, 'CHECKS', gate.CHECKS[:2]), mock.patch.object(
                        gate, 'snapshot', return_value=({}, {})), mock.patch.object(
                        gate, 'execute', side_effect=execute), mock.patch.object(
                        gate.shutil, 'which', return_value='/usr/bin/tool'), mock.patch.dict(
                        os.environ, HELPER='/unsafe', PYTHONPATH='/unsafe'):
                    self.assertEqual(gate.run(gate.ROOT, evidence), exit_code)
                summary = json.loads((evidence / 'summary.json').read_text())
                self.assertEqual(summary['offline'], overall)
                self.assertEqual(summary['runtime'], 'UNVERIFIED')
                self.assertTrue(all(cmd[:4] == ['unshare', '--user', '--map-root-user', '--net'] for cmd in commands))
                if states == ['FAIL']:
                    self.assertEqual(len(commands), 1)
                    self.assertTrue(all(check['status'] == 'BLOCKED' for check in summary['checks']))
                for check in summary['checks']:
                    self.assertEqual(check['log_sha256'], gate.digest((evidence / check['log']).read_bytes()))

    def test_missing_dependency_blocks_only_affected_check(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            def execute(command, cwd, env, log, timeout=120):
                log.write_text('ok\n')
                return 'PASS', 0
            with mock.patch.object(gate, 'CHECKS', gate.CHECKS[-1:]), mock.patch.object(
                    gate, 'snapshot', return_value=({}, {})), mock.patch.object(
                    gate, 'execute', side_effect=execute), mock.patch.object(
                    gate.shutil, 'which', side_effect=lambda tool, **kwargs: None if tool == 'jq' else '/bin/tool'):
                self.assertEqual(gate.run(gate.ROOT, evidence), 2)
            summary = json.loads((evidence / 'summary.json').read_text())
            self.assertIn('missing tools: jq', summary['checks'][0]['first_error'])

    def test_missing_image_fixture_utilities_are_blocked(self):
        for utility in ('cat', 'mkdir', 'touch'):
            with self.subTest(utility=utility), tempfile.TemporaryDirectory() as directory:
                evidence = Path(directory)
                def execute(command, cwd, env, log, timeout=120):
                    log.write_text('ok\n')
                    return 'PASS', 0
                with mock.patch.object(gate, 'CHECKS', gate.CHECKS[1:2]), mock.patch.object(
                        gate, 'snapshot', return_value=({}, {})), mock.patch.object(
                        gate, 'execute', side_effect=execute), mock.patch.object(
                        gate.shutil, 'which', side_effect=lambda tool, **kwargs: None if tool == utility else '/bin/tool'):
                    self.assertEqual(gate.run(gate.ROOT, evidence), 2)
                summary = json.loads((evidence / 'summary.json').read_text())
                self.assertEqual(summary['checks'][0]['status'], 'BLOCKED')
                self.assertIn('missing tools: ' + utility, summary['checks'][0]['first_error'])

    def test_failure_excerpt_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'check.log'
            status, code = gate.execute(['python3', '-c', 'import time; time.sleep(30)'], root, dict(os.environ), log, timeout=0.05)
            self.assertEqual((status, code), ('BLOCKED', None))
            self.assertIn('timed out', gate.first_error(log))
            status, code = gate.execute(['python3', '-c', 'raise SystemExit(7)'], root, dict(os.environ), log)
            self.assertEqual((status, code), ('FAIL', 7))
            log.write_text('setup\nFAIL: concrete fault\nmore\n')
            self.assertEqual(gate.first_error(log), 'FAIL: concrete fault')


if __name__ == '__main__':
    unittest.main()
