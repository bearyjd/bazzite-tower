#!/usr/bin/env python3
"""ci/base-bump-guard.py: automerge only for same Fedora major + same-or-newer kernel. Fails closed."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'ci/base-bump-guard.py'
FIXTURE = ROOT / 'tests/fixtures/base-bump'  # frozen policy copy: floors 44.20261023 / 7.2.9-ogc3.1.fc44
CUR = ('44.20261030', '7.2.9-ogc3.1.fc44')


def guard(cur=CUR, new=('44.20261104', '7.2.10-ogc1.1.fc44'), root=FIXTURE):
    out = subprocess.run([sys.executable, str(GUARD), '--root', str(root),
                          '--current-version', cur[0], '--current-kernel', cur[1],
                          '--new-version', new[0], '--new-kernel', new[1]],
                         check=True, capture_output=True, text=True).stdout.splitlines()
    return out[0], out[1]


class Guard(unittest.TestCase):
    def test_same_major_newer_kernel(self):
        self.assertEqual(guard()[0], 'automerge=true')

    def test_same_major_same_kernel(self):
        self.assertEqual(guard(new=('44.20261104', CUR[1]))[0], 'automerge=true')

    def test_major_jump(self):
        decision, reason = guard(new=('45.20261104', '7.3.1-ogc1.1.fc45'))
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('Fedora major', reason)

    def test_kernel_downgrade(self):
        decision, reason = guard(new=('44.20261104', '7.2.8-ogc3.1.fc44'))
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('downgrade', reason)

    def test_base_version_downgrade(self):
        decision, reason = guard(new=('44.20261001', CUR[1]))
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('base version downgrade', reason)

    def test_below_policy_floor(self):
        # Older than the policy minimumKernel (7.2.9-ogc3.1.fc44) but not older than the current pin.
        decision, reason = guard(cur=('44.20261001', '7.2.1-ogc1.1.fc44'), new=('44.20261104', '7.2.5-ogc1.1.fc44'))
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('policy', reason)

    def test_malformed_input_fails_closed(self):
        for bad in (('', ''), ('unknown', CUR[1]), (CUR[0], ''), (CUR[0], '7.2.9-200.fc44.x86_64'), ('44.2026', CUR[1])):
            for decision, reason in (guard(new=bad), guard(cur=bad)):
                self.assertEqual(decision, 'automerge=false', bad)
                self.assertIn('guard failed closed', reason, bad)

    def test_bad_policy_fails_closed(self):
        decision, reason = guard(root=ROOT / 'tests')
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('guard failed closed', reason)


if __name__ == '__main__':
    unittest.main()
