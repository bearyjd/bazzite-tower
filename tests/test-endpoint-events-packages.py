#!/usr/bin/env python3
"""Behavior of build_files/build.d/64-endpoint-events-packages.sh with PATH shims.

The script installs the opt-in endpoint-event packages and must leave every
unit they ship disabled.  dnf, rpm and systemctl are shims that log their
arguments, so no package is installed and no unit is touched.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'build_files/build.d/64-endpoint-events-packages.sh'
PACKAGES = ['rsyslog', 'rsyslog-mmjsonparse', 'rsyslog-gnutls']
RPM_FILES = '/usr/sbin/rsyslogd\n/usr/lib/systemd/system/rsyslog.service\n/usr/lib64/rsyslog/mmjsonparse.so\n'


class PackageScript(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='sw-events-pkgs.')
        self.addCleanup(self.work.cleanup)
        self.tmp = Path(self.work.name)
        (self.tmp / 'bin').mkdir()
        self.log = self.tmp / 'calls.log'

    def shim(self, name, body):
        path = self.tmp / 'bin' / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    def run_script(self, rpm_files=RPM_FILES, dnf_rc=0, enabled_state='disabled'):
        (self.tmp / 'rpm-files').write_text(rpm_files)
        self.shim('dnf', 'echo "dnf $*" >> "%s"\nexit %d\n' % (self.log, dnf_rc))
        self.shim('rpm', 'echo "rpm $*" >> "%s"\ncat "%s"\n' % (self.log, self.tmp / 'rpm-files'))
        self.shim('systemctl', 'echo "systemctl $*" >> "%s"\nif [ "$1" = is-enabled ]; then echo %s; [ %s = disabled ] || [ %s = static ]; exit $?; fi\n'
                  % (self.log, enabled_state, enabled_state, enabled_state))
        env = {**os.environ, 'PATH': str(self.tmp / 'bin') + os.pathsep + os.environ['PATH']}
        result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, calls

    def test_installs_exactly_the_three_packages(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        installs = [c for c in calls if c.startswith('dnf install')]
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0].split()[2:], ['-y', *PACKAGES])

    def test_stock_rsyslog_service_is_explicitly_disabled_never_enabled_or_masked(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('systemctl disable rsyslog.service', calls)
        self.assertFalse([c for c in calls if c.startswith(('systemctl enable', 'systemctl mask', 'systemctl start'))])

    def test_every_unit_the_packages_ship_is_disabled(self):
        files = RPM_FILES + '/usr/lib/systemd/system/rsyslog-extra.socket\n/usr/lib/systemd/system/rsyslog-x.timer\n/usr/lib/systemd/system/rsyslog.service.d/o.conf\n'
        result, calls = self.run_script(rpm_files=files)
        self.assertEqual(result.returncode, 0, result.stderr)
        disabled = ' '.join(c for c in calls if c.startswith('systemctl disable'))
        for unit in ('rsyslog.service', 'rsyslog-extra.socket', 'rsyslog-x.timer'):
            self.assertIn(unit, disabled)
        self.assertNotIn('o.conf', disabled)

    def test_fails_if_the_expected_unit_is_not_shipped(self):
        # A renamed/removed rsyslog.service must fail the build, not skip the disable.
        result, _ = self.run_script(rpm_files='/usr/sbin/rsyslogd\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rsyslog.service', result.stderr)

    def test_fails_if_a_unit_is_still_enabled_after_disable(self):
        result, _ = self.run_script(enabled_state='enabled')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not disabled', result.stderr)

    def test_fails_when_dnf_fails(self):
        result, calls = self.run_script(dnf_rc=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([c for c in calls if c.startswith('systemctl')])


if __name__ == '__main__':
    unittest.main()
