#!/usr/bin/env python3
"""build-disk.yml runs the digest-pinned BIB container directly; no archived action, dispatch-only qcow2/btrfs."""
import os
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(os.environ.get('WORKFLOW_ROOT') or Path(__file__).resolve().parents[1])
PATH = ROOT / '.github/workflows/build-disk.yml'
TEXT = PATH.read_text()
DOC = yaml.safe_load(TEXT)
STEPS = DOC['jobs']['build']['steps']
BUILD = next(s for s in STEPS if s.get('id') == 'build')
USES = re.compile(r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}(\s|$)')


class BuildDiskWorkflow(unittest.TestCase):
    def test_no_bib_action(self):
        for step in STEPS:
            self.assertNotIn('image-builder-action', step.get('uses', ''))

    def test_build_step_is_run_not_uses(self):
        self.assertNotIn('uses', BUILD)
        self.assertIn('run', BUILD)

    def test_builder_pinned_by_digest(self):
        self.assertRegex(DOC['env']['BIB_IMAGE'], r'^quay\.io/centos-bootc/bootc-image-builder@sha256:[0-9a-f]{64}$')
        self.assertIn('"${BIB_IMAGE}"', BUILD['run'])

    def test_build_args_preserved(self):
        run = '\n'.join(l for l in BUILD['run'].splitlines() if not l.lstrip().startswith('#'))
        for needle in ('--rootfs btrfs', '--use-librepo=True', '--type "${DISK_BUILD_TYPE}"',
                       ':/config.toml:ro', '"${SOURCE_IMAGE}"'):
            self.assertIn(needle, run)
        self.assertEqual(BUILD['env']['SOURCE_IMAGE'], '${{ steps.source_image.outputs.image }}')

    def test_no_expression_interpolation_in_run(self):
        self.assertNotIn('${{', BUILD['run'])

    def test_exports_output_directory(self):
        self.assertIn('output-directory=', BUILD['run'])
        for step in STEPS:
            if step.get('name', '').startswith('Upload'):
                self.assertIn('steps.build.outputs.output-directory', str(step))

    def test_all_actions_sha_pinned(self):
        for step in STEPS:
            if 'uses' in step:
                self.assertRegex(step['uses'], USES, step['uses'])

    def test_dispatch_and_qcow2_only(self):
        on = DOC.get('on', DOC.get(True))
        self.assertIn('workflow_dispatch', on)
        self.assertEqual(list(on), ['workflow_dispatch', 'pull_request'])
        legs = DOC['jobs']['build']['strategy']['matrix']['include']
        self.assertEqual([(l['type'], l['config']) for l in legs], [('qcow2', './disk_config/disk.toml')])


if __name__ == '__main__':
    unittest.main()
