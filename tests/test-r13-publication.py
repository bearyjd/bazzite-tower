#!/usr/bin/env python3
"""Offline refusals for fixed-target r13 publication; no image/registry execution."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
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
        self.root = Path(self.tmp.name) / 'control'; self.root.mkdir()
        self.data = Path(self.tmp.name) / 'data'; self.data.mkdir()
        self.ids = {'towerCommit': '1' * 40, 'towerTree': '2' * 40, 'sourceCommit': '3' * 40,
                    'sourceTree': '4' * 40, 'submoduleCommit': '5' * 40, 'bridgePinsSha256': '6' * 64,
                    'daemonPinsSha256': '7' * 64, 'workflowSha256': '8' * 64}
        self.auth = {'schemaVersion': 1, 'candidate': {'identities': self.ids,
                     'reviewReceiptSha256': self.make_receipt('independent-workflow-review', pub.sha(pub.canonical(self.ids)))}, 'accepted': None}
        self.write_auth()
        for name, source in [('cosign.pub', 'cosign.pub'), ('policy.json', 'system_files/usr/share/bazzite-tower/containers/policy.json'),
                             ('bazzite-tower.yaml', 'system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml')]:
            path = self.root / source; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, path); shutil.copyfile(path, self.data / name)
        self.image = archive(self.data / 'image.oci.tar')
        self.bridge = {'profile': 'system', 'source': {'commit': self.ids['sourceCommit'], 'tree': self.ids['sourceTree'], 'submoduleCommit': self.ids['submoduleCommit']},
                       'binary': {'path': '/usr/bin/snitchwatch-bridge-cli', 'sha256': 'a' * 64},
                       'flatpak': {'manifestSha256': 'b' * 64, 'runtimeRef': 'org.kde.Platform/x86_64/6.11'}}
        self.daemon = {'profile': 'system', 'source': {'commit': self.ids['submoduleCommit']},
                       'binary': {'path': '/usr/bin/opensnitchd', 'sha256': 'c' * 64}}
        (self.data / 'bridge-manifest.json').write_bytes(pub.canonical(self.bridge))
        (self.data / 'daemon-manifest.json').write_bytes(pub.canonical(self.daemon))
        (self.data / 'sbom.json').write_bytes(pub.canonical({'spdxVersion': 'SPDX-2.3', 'packages': [{'name': 'rpm-fixture'}]}))
        self.provenance = {'schemaVersion': 1, 'kind': 'candidate-build', 'repository': pub.REPOSITORY,
                           'workflow': pub.CANDIDATE_WORKFLOW, 'ref': pub.CANDIDATE_REF, 'runId': 100, 'runAttempt': 1,
                           'identities': self.ids, 'image': self.image, 'sbomSha256': pub.file_sha(self.data / 'sbom.json'),
                           'bridgeManifestSha256': pub.file_sha(self.data / 'bridge-manifest.json'),
                           'daemonManifestSha256': pub.file_sha(self.data / 'daemon-manifest.json')}
        (self.data / 'build-provenance.json').write_bytes(pub.canonical(self.provenance))
        self.candidate = {'schemaVersion': 1, 'repository': pub.CANDIDATE_REPOSITORY, 'ref': pub.CANDIDATE_REF,
                          'workflow': pub.CANDIDATE_WORKFLOW, 'runId': 100, 'runAttempt': 1, 'identities': self.ids, 'image': self.image,
                          'files': {p.name: pub.file_sha(p) for p in self.data.iterdir()}}
        self.write_candidate()

    def store(self, category, value):
        raw = pub.canonical(value); value_hash = pub.sha(raw)
        path = self.root / '.github/r13' / category / (value_hash + '.json')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return value_hash

    def make_receipt(self, receipt_kind, packet_hash, **changes):
        value = {'schemaVersion': 1, 'kind': receipt_kind, 'packetSha256': packet_hash, 'identities': self.ids,
                 'reviewer': 'trusted-native/reviewer', 'evidenceSha256': 'a' * 64, 'verdict': 'PASS'}
        value.update(changes)
        return self.store('receipts', value)

    def write_auth(self):
        path = self.root / '.github/r13/authority.json'; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pub.canonical(self.auth))

    def write_candidate(self):
        (self.data / 'candidate.json').write_bytes(pub.canonical(self.candidate))

    def test_valid_candidate(self):
        self.assertEqual(pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100), self.candidate)

    def test_retired_promote_entrypoint_and_head_variable_are_gone(self):
        # Replaces the old promotion-entrypoint test: no workflow calls `promote`.
        result = subprocess.run(['python3', str(ROOT / 'ci/r13-publication.py'), 'promote', '--control', str(self.root)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('invalid choice', result.stderr)
        workflows = sorted((ROOT / '.github/workflows').glob('*.yml'))
        self.assertTrue({'build.yml', 'base-refresh.yml'} <= {path.name for path in workflows})
        for path in workflows:
            self.assertNotIn('R13_PROMOTION_HEAD', path.read_text(), path.name)

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

    def test_candidate_target_and_identity_refusals(self):
        cases = [('repository', pub.STABLE_REPOSITORY), ('repository', 'ghcr.io/attacker/target'),
                 ('ref', 'refs/heads/main'), ('workflow', '.github/workflows/build.yml'), ('runId', 999), ('runAttempt', 2)]
        original = copy.deepcopy(self.candidate)
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.candidate = copy.deepcopy(original); self.candidate[key] = value; self.write_candidate()
                pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)

    def test_source_and_binary_identity_refusals(self):
        for key in self.ids:
            changed = copy.deepcopy(self.candidate); changed['identities'][key] = 'f' * len(self.ids[key])
            with self.subTest(key=key), self.assertRaises(ValueError):
                (self.data / 'candidate.json').write_bytes(pub.canonical(changed))
                pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)

    def test_rehashed_manifest_identity_and_provenance_tamper_refuse(self):
        original = (self.data / 'bridge-manifest.json').read_bytes()
        for change in [('profile', 'legacy'), ('source', dict(self.bridge['source'], commit='f' * 40)),
                       ('binary', dict(self.bridge['binary'], path='/tmp/attacker'))]:
            changed = copy.deepcopy(self.bridge); changed[change[0]] = change[1]
            (self.data / 'bridge-manifest.json').write_bytes(pub.canonical(changed))
            self.candidate['files']['bridge-manifest.json'] = pub.file_sha(self.data / 'bridge-manifest.json')
            self.write_candidate()
            with self.subTest(change=change[0]), self.assertRaises(ValueError):
                pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)
        (self.data / 'bridge-manifest.json').write_bytes(original)
        self.candidate['files']['bridge-manifest.json'] = pub.file_sha(self.data / 'bridge-manifest.json')
        self.provenance['runId'] = 999
        (self.data / 'build-provenance.json').write_bytes(pub.canonical(self.provenance))
        self.candidate['files']['build-provenance.json'] = pub.file_sha(self.data / 'build-provenance.json'); self.write_candidate()
        with self.assertRaises(ValueError): pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)

    def test_candidate_hash_substitution_refuses_before_writes(self):
        for name in pub.FILES - {'candidate.json'}:
            path = self.data / name; old = path.read_bytes(); path.write_bytes(old + b'changed')
            with self.subTest(name=name), self.assertRaises((ValueError, tarfile.ReadError, json.JSONDecodeError)):
                pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)
            path.write_bytes(old)

    def test_oci_link_duplicate_platform_index_and_blob_tamper_refuse(self):
        for options in [dict(link=True), dict(duplicate=True), dict(arch='arm64'), dict(multi=True), dict(tamper=True)]:
            archive(self.data / 'image.oci.tar', **options)
            with self.subTest(options=options), self.assertRaises(ValueError): pub.image_identity(self.data / 'image.oci.tar')

    def test_duplicate_json_refuses(self):
        with self.assertRaises(ValueError): pub.decode(b'{"verdict":"FAIL","verdict":"PASS"}')

    def test_context_ref_workflow_event_head_and_rerun_refuse(self):
        env = {'GITHUB_REPOSITORY': pub.REPOSITORY, 'GITHUB_EVENT_NAME': 'push', 'GITHUB_SHA': self.ids['towerCommit'], 'GITHUB_RUN_ATTEMPT': '1'}
        ref = pub.CANDIDATE_REF; workflow = pub.REPOSITORY + '/' + pub.CANDIDATE_WORKFLOW + '@' + ref
        with patch.dict(os.environ, env, clear=True):
            pub.context(self.ids['towerCommit'], ref, workflow, 'a' * 40)
            for changed_ref, changed_workflow in [('refs/heads/main', workflow), (ref, 'attacker/workflow')]:
                with self.assertRaises(ValueError): pub.context(self.ids['towerCommit'], changed_ref, changed_workflow, 'a' * 40)
            with self.assertRaises(ValueError): pub.context(self.ids['towerCommit'], ref, workflow, '')
        for key, value in [('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REPOSITORY', 'attacker/repo'), ('GITHUB_SHA', 'f' * 40), ('GITHUB_RUN_ATTEMPT', '2')]:
            with patch.dict(os.environ, dict(env, **{key: value}), clear=True), self.assertRaises(ValueError):
                pub.context(self.ids['towerCommit'], ref, workflow, 'a' * 40)

    def test_clean_source_tree_workflow_and_pin_checks(self):
        source = Path(self.tmp.name) / 'source'; source.mkdir()
        # The candidate workflow file is retired from the repo; the gate still hashes
        # whatever bytes sit at that path in the source tree, so use a fixture.
        workflow_fixture = source / pub.CANDIDATE_WORKFLOW
        workflow_fixture.parent.mkdir(parents=True, exist_ok=True); workflow_fixture.write_bytes(b'name: retired-candidate-fixture\n')
        for name in ['build_files/firewall/snitchwatch-system-pins.json',
                     'build_files/firewall/snitchwatch-system-daemon-pins.json']:
            target = source / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / name, target)
        pins = json.loads((source / 'build_files/firewall/snitchwatch-system-pins.json').read_bytes())
        ids = dict(self.ids, **{key: pins[key] for key in ('sourceCommit', 'sourceTree', 'submoduleCommit')})
        ids['workflowSha256'] = pub.file_sha(source / pub.CANDIDATE_WORKFLOW)
        ids['bridgePinsSha256'] = pub.file_sha(source / 'build_files/firewall/snitchwatch-system-pins.json')
        ids['daemonPinsSha256'] = pub.file_sha(source / 'build_files/firewall/snitchwatch-system-daemon-pins.json')
        def git(args, **kwargs):
            if args[-1] == 'HEAD': return ids['towerCommit']
            if args[-1] == 'HEAD^{tree}': return ids['towerTree']
            return ''
        with patch.object(pub.subprocess, 'check_output', side_effect=git):
            pub.verify_source(source, ids)
            for key in ('towerCommit', 'towerTree', 'workflowSha256', 'bridgePinsSha256', 'daemonPinsSha256', 'sourceCommit', 'sourceTree', 'submoduleCommit'):
                bad = dict(ids, **{key: 'f' * len(ids[key])})
                with self.subTest(key=key), self.assertRaises(ValueError): pub.verify_source(source, bad)
        with patch.object(pub.subprocess, 'check_output', side_effect=[ids['towerCommit'], ids['towerTree'], ' M code']):
            with self.assertRaises(ValueError): pub.verify_source(source, ids)


    def test_missing_and_extra_inventory_still_refuse_with_details(self):
        path = self.data / 'candidate.json'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'unexpected artifact inventory:') as caught:
            pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)
        details = json.loads(str(caught.exception).split(': ', 1)[1])
        self.assertEqual(details, {'missing': ['candidate.json'], 'extra': [], 'extraCount': 0})
        path.write_bytes(original)
        (self.data / 'unreviewed').write_text('DO-NOT-PRINT-CONTENTS')
        with self.assertRaisesRegex(ValueError, 'unexpected artifact inventory:') as caught:
            pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)
        details = json.loads(str(caught.exception).split(': ', 1)[1])
        self.assertEqual(details, {'missing': [], 'extra': ['unreviewed'], 'extraCount': 1})
        self.assertNotIn('DO-NOT-PRINT-CONTENTS', str(caught.exception))

    def test_inventory_error_is_bounded_and_escapes_control_characters(self):
        for index in range(24):
            (self.data / ('extra-' + str(index).zfill(2))).write_bytes(b'')
        (self.data / ('\x1b\n' + 'z' * 180)).write_bytes(b'')
        with self.assertRaises(ValueError) as caught:
            pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)
        message = str(caught.exception)
        details = json.loads(message.split(': ', 1)[1])
        self.assertEqual(details['extraCount'], 25)
        self.assertEqual(len(details['extra']), 20)
        self.assertTrue(all(len(name) <= 120 for name in details['extra']))
        self.assertNotIn('\n', message)
        self.assertNotIn('\x1b', message)


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
