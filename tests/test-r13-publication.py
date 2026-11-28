#!/usr/bin/env python3
"""Offline refusals for the data helpers and workflow contracts; no image/registry execution."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publication', ROOT / 'ci/r13-publication.py')
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


def archive(path, *, link=False, duplicate=False, arch='amd64', multi=False, tamper=False):
    config = pub.canonical({'os': 'linux', 'architecture': arch})
    layer = b'fixture layer'
    manifest = pub.canonical({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                             'config': {'digest': 'sha256:' + pub.sha(config), 'size': len(config)},
                             'layers': [{'digest': 'sha256:' + pub.sha(layer), 'size': len(layer)}]})
    if multi:
        manifest = pub.canonical({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': []})
    files = {'oci-layout': pub.canonical({'imageLayoutVersion': '1.0.0'}),
             'index.json': pub.canonical({'schemaVersion': 2, 'manifests': [{'digest': 'sha256:' + pub.sha(manifest), 'size': len(manifest)}]}),
             'blobs/sha256/' + pub.sha(manifest): manifest,
             'blobs/sha256/' + pub.sha(config): config,
             'blobs/sha256/' + pub.sha(layer): b'tampered data' if tamper else layer}
    with tarfile.open(path, 'w') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if duplicate:
            info = tarfile.TarInfo('index.json'); info.size = 2
            tar.addfile(info, io.BytesIO(b'{}'))
        if link:
            info = tarfile.TarInfo('blobs/sha256/' + 'f' * 64); info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'
            tar.addfile(info)
    return {'manifestDigest': 'sha256:' + pub.sha(manifest), 'indexDigest': None,
            'platformDigest': 'sha256:' + pub.sha(manifest), 'platform': 'linux/amd64'}


class Publication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / 'data'; self.data.mkdir()

    def test_valid_oci_identity(self):
        expected = archive(self.data / 'image.oci.tar')
        self.assertEqual(pub.image_identity(self.data / 'image.oci.tar'), expected)

    def test_retired_pipeline_entrypoints_and_head_variable_are_gone(self):
        for name in ('main', 'candidate_approval', 'verify_source', 'validate_candidate', 'context'):
            self.assertFalse(hasattr(pub, name), name)
        self.assertNotIn('__main__', (ROOT / 'ci/r13-publication.py').read_text())
        workflows = sorted((ROOT / '.github/workflows').glob('*.yml'))
        self.assertTrue({'build.yml', 'base-refresh.yml'} <= {path.name for path in workflows})
        for path in workflows:
            self.assertNotIn('R13_PROMOTION_HEAD', path.read_text(), path.name)

    def test_hash_digest_and_bounded_read_refusals(self):
        self.assertEqual(pub.digest('sha256:' + 'a' * 64), 'sha256:' + 'a' * 64)
        for value in ('a' * 64, 'sha256:' + 'A' * 64, 'sha256:' + 'a' * 63, None):
            with self.subTest(value=value), self.assertRaises(ValueError): pub.digest(value)
        target = self.data / 'target'; target.write_bytes(b'data')
        link = self.data / 'link'; link.symlink_to(target)
        self.assertEqual(pub.read(target), b'data')
        for path, limit in ((link, 16), (self.data / 'missing', 16), (self.data, 16), (target, 3)):
            with self.subTest(path=path.name, limit=limit), self.assertRaises(ValueError): pub.read(path, limit)
        for path in (link, self.data / 'missing'):
            with self.subTest(path=path.name), self.assertRaises(ValueError): pub.file_sha(path)

    def test_output_names_and_values_refuse_injection(self):
        output = Path(self.tmp.name) / 'output'; output.write_text('')
        with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
            pub.output({'gui_sha256': 'a' * 64, 'packet_sha256': 'b' * 64})
            self.assertIn('gui_sha256=', output.read_text())
            for key in ('', '1key', 'UPPER', 'bad-key', 'key=value', 'key\nnext', 'key\rnext', '../path', 42):
                output.write_text('')
                with self.subTest(key=key), self.assertRaises(ValueError): pub.output({key: 'value'})
                self.assertEqual(output.read_text(), '')
            for value in ('a\nb', 'a\rb'):
                output.write_text('')
                with self.subTest(value=value), self.assertRaises(ValueError): pub.output({'gui_sha256': value})
                self.assertEqual(output.read_text(), '')

    def test_oci_link_duplicate_platform_index_and_blob_tamper_refuse(self):
        for options in [dict(link=True), dict(duplicate=True), dict(arch='arm64'), dict(multi=True), dict(tamper=True)]:
            archive(self.data / 'image.oci.tar', **options)
            with self.subTest(options=options), self.assertRaises(ValueError): pub.image_identity(self.data / 'image.oci.tar')

    def test_duplicate_json_refuses(self):
        with self.assertRaises(ValueError): pub.decode(b'{"verdict":"FAIL","verdict":"PASS"}')


class WorkflowContracts(unittest.TestCase):
    def test_pr_head_proof_refuses_synthetic_merge_and_missing_head(self):
        import yaml
        for filename, job in [('build.yml', 'verify'), ('boot-test.yml', 'boot-test')]:
            workflow = yaml.safe_load((ROOT / '.github/workflows' / filename).read_text())
            steps = workflow['jobs'][job]['steps']
            checkout = next(s for s in steps if s.get('name') == 'Checkout')
            self.assertEqual(checkout['with'], {'ref': '${{ github.event.pull_request.head.sha || github.sha }}', 'persist-credentials': False})
            proof = next(s for s in steps if s.get('name') == 'Prove exact checked-out source head')
            self.assertEqual(proof['env']['EXPECTED_HEAD'], checkout['with']['ref'])
            self.assertEqual(workflow['jobs'][job]['permissions'], {'contents': 'read'})
            self.assertLess(steps.index(proof), next(i for i,s in enumerate(steps) if 'Build' in s.get('name', '') or s.get('name') == 'Build image'))
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); fake_git = root / 'git'
                fake_git.write_text('#!/bin/sh\n[ "$1" = rev-parse ] || exit 2\ncase "$2" in HEAD) echo "$TEST_GIT_HEAD";; "HEAD^{tree}") echo "$TEST_GIT_TREE";; *) exit 2;; esac\n')
                fake_git.chmod(0o755)
                for expected, actual, success in [('a' * 40, 'a' * 40, True), ('a' * 40, 'b' * 40, False), ('', 'a' * 40, False), ('a;echo unsafe', 'a' * 40, False)]:
                    summary = root / 'summary'; summary.write_text('')
                    env = dict(os.environ, PATH=tmp + os.pathsep + os.environ['PATH'], EXPECTED_HEAD=expected,
                               TEST_GIT_HEAD=actual, TEST_GIT_TREE='c' * 40, GITHUB_STEP_SUMMARY=str(summary))
                    result = subprocess.run(['bash', '-c', proof['run']], env=env, capture_output=True, text=True)
                    with self.subTest(workflow=filename, expected=expected, actual=actual):
                        self.assertEqual(result.returncode == 0, success, result.stderr)
                        self.assertEqual(bool(summary.read_text()), success)
            if filename == 'boot-test.yml':
                self.assertEqual(workflow['permissions'], {'contents': 'read'})
                notifier = workflow['jobs']['notify']
                self.assertIn("github.event_name != 'pull_request'", notifier['if'])
                self.assertEqual(notifier['permissions'], {'issues': 'write'})
                self.assertFalse(any('checkout@' in s.get('uses', '') for s in notifier['steps']))
                self.assertFalse(any('run' in s for s in notifier['steps']))
            else:
                release_checkout = next(s for s in workflow['jobs']['release']['steps'] if s.get('name') == 'Checkout')
                self.assertEqual(release_checkout['with']['ref'], '${{ github.sha }}')

    def test_yaml_and_privilege_boundaries(self):
        # PyYAML is a local static-check convenience, not a runtime dependency.
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/build.yml').read_text())
        self.assertEqual([m['variant'] for m in workflow['jobs']['release']['strategy']['matrix']['include']], ['latest-kernel'])
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])
        self.assertIn('bazzite-tower-stable-writers', workflow['concurrency']['group'])


if __name__ == '__main__':
    unittest.main()
