#!/usr/bin/env python3
"""ci/base-bump-guard.py: automerge only for same Fedora major + same-or-newer kernel. Fails closed."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'ci/base-bump-guard.py'
FIXTURE = ROOT / 'tests/fixtures/base-bump'  # frozen policy copy: floors 44.20261023 / 7.2.9-ogc3.1.fc44
CUR = ('44.20261030', '7.2.9-ogc3.1.fc44')


CLEAN = '_No blast-radius package changes._\n'  # exactly what ci/base-diff.py prints for an empty diff
NONE = object()  # no --blast-report at all


def guard(cur=CUR, new=('44.20261104', '7.2.10-ogc1.1.fc44'), root=FIXTURE, blast=CLEAN):
    extra = []
    if blast is not NONE:
        tmp = tempfile.NamedTemporaryFile('w', suffix='.md', delete=False)
        tmp.write(blast)
        tmp.close()
        extra = ['--blast-report', tmp.name]
    out = subprocess.run([sys.executable, str(GUARD), '--root', str(root), *extra,
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

    def test_nonempty_blast_diff_blocks_with_names(self):
        report = ('**Changed**\n- `qemu-kvm` 9.0-1 -> 9.1-1\n- `systemd` 256-1 -> 256-2\n- `libvirt-daemon` 10-1 -> 10-2\n'
                  '- `polkit` 1-1 -> 1-2\n- `bootc` 1-1 -> 1-2\n- `iwd` 1-1 -> 1-2\n\n**Added**\n- `swtpm` 1\n')
        decision, reason = guard(blast=report)
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('qemu-kvm, systemd, libvirt-daemon, polkit, bootc', reason)
        self.assertIn('(7)', reason)
        self.assertIn('and 2 more', reason)
        self.assertNotIn('iwd', reason)

    def test_blast_names_sanitized_and_truncated(self):
        decision, reason = guard(blast='- `evil@x[y]|z$(id)' + 'a' * 100 + '` 1 -> 2\n')
        self.assertEqual(decision, 'automerge=false')
        self.assertNotRegex(reason, r'[@\[\]|$]')
        self.assertLess(len(reason), 200)

    def test_blast_diff_not_computed_fails_closed(self):
        for blast in (NONE, '', '\n', 'garbage with no marker'):
            decision, reason = guard(blast=blast)
            self.assertEqual(decision, 'automerge=false', repr(blast))
            self.assertIn('blast-radius', reason, repr(blast))
        decision, reason = guard(blast=NONE)
        self.assertIn('not computed', reason)

    def test_clean_marker_with_extra_content_blocks(self):
        # A clean marker followed by anything (appended or truncated-in) must not read as an empty diff.
        for blast in (CLEAN + '- `qemu-kvm` 9.0-1 -> 9.1-1\n', CLEAN + 'x', 'x' + CLEAN):
            decision, _ = guard(blast=blast)
            self.assertEqual(decision, 'automerge=false', repr(blast))

    def test_overcap_report_blocks(self):
        report = ''.join('- `qemu-pkg%d` 1-1 -> 1-2\n' % i for i in range(300))
        decision, reason = guard(blast=report)
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('(300)', reason)

    def test_missing_report_file_fails_closed(self):
        out = subprocess.run([sys.executable, str(GUARD), '--root', str(FIXTURE), '--blast-report', '/nonexistent/x',
                              '--current-version', CUR[0], '--current-kernel', CUR[1],
                              '--new-version', '44.20261104', '--new-kernel', '7.2.10-ogc1.1.fc44'],
                             check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(out[0], 'automerge=false')
        self.assertIn('not computed', out[1])

    def test_bad_policy_fails_closed(self):
        decision, reason = guard(root=ROOT / 'tests')
        self.assertEqual(decision, 'automerge=false')
        self.assertIn('guard failed closed', reason)


if __name__ == '__main__':
    unittest.main()
