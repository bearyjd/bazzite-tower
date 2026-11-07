#!/usr/bin/env python3
"""build.yml's notify job finds the release matrix jobs by exact display name.

A rename of the `release` job that misses the lookup string makes the release-gate
report skip silently, and PR CI cannot see it (release only runs on the default
branch). Pin the two together.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / '.github/workflows/build.yml').read_text())


class ReleaseJobNames(unittest.TestCase):
    def test_notify_lookup_matches_release_job_name(self):
        release = DOC['jobs']['release']['name']
        self.assertIn('${{ matrix.variant }}', release)
        scripts = [step['with']['script'] for step in DOC['jobs']['notify']['steps']
                   if isinstance(step.get('with'), dict) and 'script' in step['with']]
        lookups = [m for s in scripts for m in re.findall(r'j\.name === `([^`]+)`', s)]
        self.assertEqual(len(lookups), 1, lookups)
        self.assertEqual(lookups[0].replace('${variant}', 'V'),
                         release.replace('${{ matrix.variant }}', 'V'))

    def test_no_job_still_claims_the_original_r13_label(self):
        for name in ('build.yml', 'boot-test.yml'):
            text = (ROOT / '.github/workflows' / name).read_text()
            self.assertNotIn('Verify original r13 immutable candidate', text, name)


if __name__ == '__main__':
    unittest.main()
