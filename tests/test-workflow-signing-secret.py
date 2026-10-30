#!/usr/bin/env python3
"""The cosign private key must only reach cosign steps (or a boolean presence flag)."""
import os
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(os.environ.get('WORKFLOW_ROOT') or Path(__file__).resolve().parents[1])
WORKFLOWS = sorted((ROOT / '.github/workflows').glob('*.yml'))
FLAG = re.compile(r"^\$\{\{\s*secrets\.SIGNING_SECRET\s*!=\s*''\s*\}\}$")
KEY = re.compile(r'secrets\.SIGNING_SECRET')
WORDS = ('SIGNING_SECRET', 'COSIGN_PRIVATE_KEY')


def load(path):
    return yaml.safe_load(path.read_text())


def is_cosign_step(step):
    return 'cosign' in (step.get('name', '') + step.get('run', '')).lower()


class SigningSecretScope(unittest.TestCase):
    def test_workflows_found(self):
        self.assertTrue(WORKFLOWS)

    def test_no_workflow_or_job_level_exposure(self):
        for path in WORKFLOWS:
            doc = load(path)
            scopes = [('workflow', doc)] + [(f'job {n}', j) for n, j in (doc.get('jobs') or {}).items()]
            for label, scope in scopes:
                env = scope.get('env')
                if isinstance(env, dict):
                    for word in WORDS:
                        self.assertNotIn(word, env, f'{path.name} {label} env exposes {word}')
                    for key, value in env.items():
                        self.assertIsNone(KEY.search(str(value)), f'{path.name} {label} env {key} references the key')

    def test_no_secrets_inherit_or_job_with(self):
        for path in WORKFLOWS:
            for name, job in (load(path).get('jobs') or {}).items():
                self.assertNotEqual(job.get('secrets'), 'inherit', f'{path.name} job {name}')
                self.assertIsNone(KEY.search(str(job.get('with', ''))), f'{path.name} job {name} with')
                self.assertIsNone(KEY.search(str(job.get('secrets', ''))), f'{path.name} job {name} secrets')

    def test_only_cosign_steps_or_presence_flag_reference_key(self):
        for path in WORKFLOWS:
            for name, job in (load(path).get('jobs') or {}).items():
                for step in job.get('steps') or []:
                    where = f"{path.name} job {name} step {step.get('name', step.get('uses', '?'))!r}"
                    self.assertIsNone(KEY.search(str(step.get('with', ''))), f'{where}: key passed via with')
                    self.assertIsNone(KEY.search(str(step.get('run', ''))), f'{where}: key interpolated in run')
                    self.assertIsNone(KEY.search(str(step.get('if', ''))), f'{where}: key in if')
                    for key, value in (step.get('env') or {}).items():
                        if not KEY.search(str(value)):
                            continue
                        if key == 'HAS_SIGNING_KEY':
                            self.assertRegex(str(value), FLAG, f'{where}: flag must be a != comparison')
                        else:
                            self.assertEqual(key, 'COSIGN_PRIVATE_KEY', where)
                            self.assertTrue(is_cosign_step(step), f'{where}: not a cosign step')
                            self.assertNotIn('docker build', step.get('run', ''), where)

    def test_presence_flag_never_gates_on_env_secret(self):
        # A job-level env copy of the key is the only way `env.SIGNING_SECRET` can be non-empty.
        for path in WORKFLOWS:
            self.assertIsNone(re.search(r'env\.SIGNING_SECRET', path.read_text()), f'{path.name} gates on env.SIGNING_SECRET')


if __name__ == '__main__':
    unittest.main()
