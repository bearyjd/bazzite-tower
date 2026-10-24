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

    def collect_commandless_image(self, failure=None):
        spec = importlib.util.spec_from_file_location('collector', ROOT / 'ci/r13-candidate-data.py')
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        out = Path(self.tmp.name) / ('collector-' + str(failure))
        binaries = {'/usr/bin/snitchwatch-bridge-cli': b'installed bridge fixture',
                    '/usr/bin/opensnitchd': b'installed daemon fixture'}
        manifests = {'bridge': copy.deepcopy(self.bridge), 'daemon': copy.deepcopy(self.daemon)}
        for manifest in manifests.values():
            manifest['binary']['sha256'] = pub.sha(binaries[manifest['binary']['path']])
        if failure == 'profile':
            manifests['bridge']['profile'] = 'legacy'
        calls = []
        real_run = subprocess.run

        def check_output(argv, **kwargs):
            calls.append(list(argv))
            if argv[:2] == ['podman', 'create']:
                # This image has neither CMD nor ENTRYPOINT; draft container
                # creation must supply a command but must never execute it.
                if len(argv) == 3:
                    raise subprocess.CalledProcessError(125, argv, stderr='no command or entrypoint provided')
                self.assertEqual(argv, ['podman', 'create', 'localhost/bazzite-tower:r13-build', '/usr/bin/true'])
                return 'extraction-container\n'
            self.assertEqual(argv, ['git', '-C', str(ROOT), 'log', '-1', '--format=%cI'])
            return '2026-10-11T00:00:00+00:00\n'

        def run(argv, **kwargs):
            argv = list(argv)
            calls.append(argv)
            if argv[:2] == ['podman', 'cp']:
                self.assertTrue(argv[2].startswith('extraction-container:/'))
                if failure == 'copy':
                    raise subprocess.CalledProcessError(125, argv)
                source = argv[2].split(':', 1)[1]
                if source.startswith('/usr/share/snitchwatch/system-'):
                    kind = source.removeprefix('/usr/share/snitchwatch/system-').removesuffix('-manifest.json')
                    data = pub.canonical(manifests[kind])
                else:
                    data = b'tampered binary' if failure == 'binary' else binaries[source]
                Path(argv[3]).write_bytes(data)
            elif argv[:2] == ['podman', 'run']:
                self.assertEqual(argv[:6], ['podman', 'run', '--rm', '--entrypoint', '/usr/bin/rpm',
                                           'localhost/bazzite-tower:r13-build'])
                self.assertEqual(argv[6:], ['-qa', '--qf', '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'])
                kwargs['stdout'].write('fixture-rpm\t0\t1.0\t1.fc44\tx86_64\n')
            elif argv[0] == 'python3':
                return real_run(argv, **kwargs)
            elif argv[0] == 'skopeo':
                # Rootless containers-storage reexec is denied on this runner;
                # the existing Podman namespace helper must own the export.
                raise subprocess.CalledProcessError(1, argv, stderr='unshare: Operation not permitted')
            elif argv[:3] == ['podman', 'unshare', 'skopeo']:
                self.assertEqual(argv[:7], ['podman', 'unshare', 'skopeo', 'copy', '--all', '--preserve-digests',
                                           'containers-storage:localhost/bazzite-tower:r13-build'])
                self.assertEqual(argv[7], 'oci-archive:' + str(out / 'image.oci.tar') + ':r13-build')
                if failure == 'namespace':
                    raise subprocess.CalledProcessError(125, argv)
                archive(out / 'image.oci.tar', tamper=failure == 'archive')
            else:
                self.assertEqual(argv, ['podman', 'rm', 'extraction-container'])
            return subprocess.CompletedProcess(argv, 0)

        argv = ['collector', '--source', str(ROOT), '--control', str(self.root), '--out', str(out)]
        with patch.object(sys, 'argv', argv), patch.dict(os.environ, {'GITHUB_SHA': self.ids['towerCommit'], 'GITHUB_RUN_ID': '100'}), \
                patch.object(collector.subprocess, 'check_output', side_effect=check_output), \
                patch.object(collector.subprocess, 'run', side_effect=run):
            if failure:
                error = subprocess.CalledProcessError if failure in ('copy', 'namespace') else ValueError
                with self.assertRaises(error):
                    collector.main()
            else:
                collector.main()
        self.assertEqual(calls[-1], ['podman', 'rm', 'extraction-container'])
        self.assertFalse(any(call[:2] == ['podman', 'start'] for call in calls))
        self.assertEqual(sum(call[:2] == ['podman', 'create'] for call in calls), 1)
        return out, calls

    def test_complete_collector_with_no_image_command(self):
        out, calls = self.collect_commandless_image()
        candidate = pub.validate_candidate(self.root, out, self.ids['towerCommit'], 100)
        self.assertEqual(set(p.name for p in out.iterdir()), pub.FILES)
        self.assertEqual(candidate['image'], self.image)
        self.assertEqual(candidate['files']['image.oci.tar'], pub.file_sha(out / 'image.oci.tar'))
        self.assertEqual(sum(call[:2] == ['podman', 'run'] for call in calls), 1)

    def test_collector_copy_failure_removes_unstarted_container(self):
        out, calls = self.collect_commandless_image('copy')
        self.assertFalse((out / 'candidate.json').exists())
        self.assertFalse(any(call[:2] == ['podman', 'run'] for call in calls))

    def test_collector_binary_or_archive_drift_removes_container(self):
        for failure in ('binary', 'archive'):
            with self.subTest(failure=failure):
                out, _ = self.collect_commandless_image(failure)
                self.assertFalse((out / 'candidate.json').exists())

    def test_collector_namespace_copy_failure_removes_container(self):
        out, calls = self.collect_commandless_image('namespace')
        self.assertFalse((out / 'candidate.json').exists())
        self.assertTrue(any(call[:3] == ['podman', 'unshare', 'skopeo'] for call in calls))
        self.assertFalse(any(call[0] == 'sudo' for call in calls))

    def test_collector_final_validation_failure_removes_container(self):
        out, _ = self.collect_commandless_image('profile')
        self.assertTrue((out / 'candidate.json').exists(), 'failed candidate evidence must remain')

    def test_retired_promote_entrypoint_and_head_variable_are_gone(self):
        # Replaces the old promotion-entrypoint test: no workflow calls `promote`.
        result = subprocess.run(['python3', str(ROOT / 'ci/r13-publication.py'), 'promote', '--control', str(self.root)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('invalid choice', result.stderr)
        for name in ('r13-candidate.yml', 'r13-promote.yml', 'r13-immutable-ci.yml'):
            self.assertNotIn('R13_PROMOTION_HEAD', (ROOT / '.github/workflows' / name).read_text())

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
        for name in [pub.CANDIDATE_WORKFLOW, 'build_files/firewall/snitchwatch-system-pins.json',
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
        workflows = {name: yaml.safe_load((ROOT / '.github/workflows' / name).read_text()) for name in ('build.yml', 'r13-candidate.yml', 'r13-promote.yml')}
        self.assertEqual([m['variant'] for m in workflows['build.yml']['jobs']['release']['strategy']['matrix']['include']], ['safe-pin', 'latest-kernel'])
        for name, workflow in workflows.items():
            self.assertFalse(workflow['concurrency']['cancel-in-progress'])
            self.assertIn('bazzite-tower-stable-writers', workflow['concurrency']['group'])
        candidate = workflows['r13-candidate.yml']
        self.assertEqual(candidate['jobs']['build']['permissions'], {'contents': 'read'})
        for name, job in [('r13-candidate.yml', 'publish'), ('r13-promote.yml', 'promote')]:
            steps = workflows[name]['jobs'][job]['steps']
            code = '\n'.join(s.get('run', '') for s in steps)
            self.assertNotIn('podman run', code); self.assertNotIn('podman exec', code)
            self.assertNotIn('buildah', code); self.assertNotIn('Containerfile', code)
            self.assertNotIn('python3 control/', code)
            for s in steps:
                self.assertNotIn('actions/checkout@', s.get('uses', ''))
                if 'uses' in s: self.assertRegex(s['uses'], r'@[a-f0-9]{40}$')
            self.assertLess(code.index('verify-attestation'), code.index('Publish fixed candidate') if 'Publish fixed candidate' in code else len(code))
            self.assertIn('--preserve-digests', code)
        candidate_code = '\n'.join(s.get('run', '') for s in candidate['jobs']['publish']['steps'])
        self.assertNotIn('docker://ghcr.io/bearyjd/bazzite-tower:', candidate_code)
        promotion = workflows['r13-promote.yml']
        self.assertNotIn('push', promotion.get('on', promotion.get(True, {})))

    def test_publisher_data_guard_executes_and_rejects_hash_target_path_tamper(self):
        import yaml
        candidate = yaml.safe_load((ROOT / '.github/workflows/r13-candidate.yml').read_text())
        text = next(s['run'] for s in candidate['jobs']['publish']['steps'] if s.get('name') == 'Revalidate bounded artifact bytes and fixed repository policy')
        program = text.split("python3 - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'data'; data.mkdir()
            for name in pub.FILES: (data / name).write_bytes(b'fixture')
            shutil.copyfile(ROOT / 'system_files/usr/share/bazzite-tower/containers/policy.json', data / 'policy.json')
            hashes = {p.name: pub.file_sha(p) for p in data.iterdir()}
            env = {'DATA': str(data), 'HASHES': json.dumps(hashes), 'DIGEST': 'sha256:' + 'a' * 64, 'MODE': 'candidate', 'RUNNER_TEMP': tmp}
            with patch.dict(os.environ, env): exec(compile(program, 'publisher-data', 'exec'), {})
            policy = json.loads((root / 'r13-trust/policy.json').read_bytes())
            self.assertEqual(set(policy['transports']['docker']), {pub.CANDIDATE_REPOSITORY})
            for bad_hashes in [dict(hashes, **{'../escape': 'a' * 64}), dict(hashes, **{'image.oci.tar': 'f' * 64})]:
                with patch.dict(os.environ, dict(env, HASHES=json.dumps(bad_hashes))), self.assertRaises(AssertionError):
                    exec(compile(program, 'publisher-data', 'exec'), {})
            with patch.dict(os.environ, dict(env, DIGEST='sha256:abc;touch /tmp/escape')), self.assertRaises(AssertionError):
                exec(compile(program, 'publisher-data', 'exec'), {})

    def candidate_publish_step(self, prefix):
        import yaml
        steps = yaml.safe_load((ROOT / '.github/workflows/r13-candidate.yml').read_text())['jobs']['publish']['steps']
        return steps, next(s['run'] for s in steps if s.get('name', '').startswith(prefix))

    def test_candidate_signs_and_verifies_attestations_in_bundle_format(self):
        # Run 38483704342: certificate-based GitHub provenance on the same image
        # makes legacy verify-attestation fail with "expected key signature, not
        # certificate"; the r13 policy recovery (run 38168060273) used bundles.
        steps, sign = self.candidate_publish_step('Sign fixed destination')
        _, verify = self.candidate_publish_step('Verify destination-bound')
        self.assertIn('cosign sign -y --key env://COSIGN_PRIVATE_KEY "$image"', sign)
        self.assertIn('cosign sign -y --new-bundle-format --key env://COSIGN_PRIVATE_KEY "$image"', sign)
        self.assertEqual(sign.count('cosign attest -y --new-bundle-format --key env://COSIGN_PRIVATE_KEY'), 2)
        code = '\n'.join(s.get('run', '') for s in steps)
        self.assertNotIn('cosign attest -y --key', code)
        self.assertNotIn('verify-attestation --key', code)
        self.assertIn('timeout 180 cosign verify --key "$DATA/cosign.pub" "$image" >/dev/null', verify)
        for kind, out in [('https://sigstore.dev/cosign/sign/v1', 'sign-v1'), ('spdxjson', 'sbom'),
                          ('https://github.com/bearyjd/bazzite-tower/attestations/r13-candidate-build/v1', 'provenance')]:
            self.assertIn(f'verify-attestation --new-bundle-format --key "$DATA/cosign.pub" --type {kind} "$image" > "$RUNNER_TEMP/{out}.verified.json"', verify)
        self.assertLess(verify.index('verify-attestation'), verify.index('gh attestation verify'))

    def test_candidate_local_oci_export_removes_only_propagated_signatures(self):
        steps, verify = self.candidate_publish_step('Verify destination-bound')
        export = ('skopeo --policy "$RUNNER_TEMP/r13-trust/policy.json" --registries.d "$RUNNER_TEMP/r13-trust/registries.d" \\\n'
                  '  copy --all --preserve-digests --remove-signatures --digestfile "$RUNNER_TEMP/policy.digest" '
                  '"docker://$image" "oci:$RUNNER_TEMP/policy-verified:r13"')
        self.assertIn(export, verify)
        self.assertLess(verify.index("python3 - <<'PY'"), verify.index(export))
        self.assertLess(verify.index(export), verify.index('[[ "$(cat "$RUNNER_TEMP/policy.digest")" == "$DIGEST" ]]'))
        self.assertEqual(sum(s.get('run', '').count('--remove-signatures') for s in steps), 1)

    def test_candidate_verified_bundle_payloads_bind_predicates_and_digest(self):
        import base64
        _, verify = self.candidate_publish_step('Verify destination-bound')
        program = verify.split("python3 - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
        dest, digest = pub.CANDIDATE_REPOSITORY, 'sha256:' + 'a' * 64
        kinds = {'sbom': 'https://spdx.dev/Document', 'provenance': 'https://github.com/bearyjd/bazzite-tower/attestations/r13-candidate-build/v1',
                 'sign-v1': 'https://sigstore.dev/cosign/sign/v1'}
        predicates = {'sbom': {'spdxVersion': 'SPDX-2.3'}, 'provenance': {'kind': 'candidate-build'}, 'sign-v1': {}}

        def envelope(name, *, subject=dest, sha=digest[7:], predicate=None):
            statement = {'_type': 'https://in-toto.io/Statement/v1', 'predicateType': kinds[name],
                         'subject': [{'name': subject, 'digest': {'sha256': sha}}],
                         'predicate': predicates[name] if predicate is None else predicate}
            return {'payloadType': 'application/vnd.in-toto+json', 'payload': base64.b64encode(json.dumps(statement).encode()).decode()}

        def run(overrides):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); data = root / 'data'; data.mkdir()
                (data / 'sbom.json').write_text(json.dumps(predicates['sbom']))
                (data / 'build-provenance.json').write_text(json.dumps(predicates['provenance']))
                for name in kinds:
                    lines = overrides.get(name, [envelope(name)])
                    (root / (name + '.verified.json')).write_text(''.join(json.dumps(x) + '\n' for x in lines))
                env = {'RUNNER_TEMP': tmp, 'DATA': str(data), 'DIGEST': digest, 'DESTINATION': dest}
                with patch.dict(os.environ, env): exec(compile(program, 'candidate-verify', 'exec'), {})

        run({})
        # sign/v1 is checked by digest only, as in the recovery: its subject name is not the destination.
        run({'sign-v1': [envelope('sign-v1', subject='ghcr.io/bearyjd/bazzite-tower-r13-candidate:r13-candidate-1')]})
        for bad in [{'sign-v1': []}, {'sign-v1': [envelope('sign-v1', sha='b' * 64)]},
                    {'sbom': [envelope('sbom', predicate={'spdxVersion': 'SPDX-tampered'})]},
                    {'provenance': [envelope('provenance', subject='ghcr.io/bearyjd/bazzite-tower')]},
                    {'sbom': [envelope('sbom', sha='b' * 64)]}]:
            with self.assertRaises(AssertionError):
                run(bad)

    PROMOTER = ROOT / '.github/workflows/r13-promote.yml'
    FIXED_DIGEST = 'sha256:df4ec1239abae0cbe1d4d4e27bce85d54f17e2b5155e431c2727c724aa879bf7'

    def promoter(self):
        import yaml
        workflow = yaml.safe_load(self.PROMOTER.read_text())
        return workflow, workflow['jobs']['promote'], workflow['jobs']['promote']['steps']

    def step_index(self, steps, fragment):
        return next(i for i, s in enumerate(steps) if fragment in s.get('name', ''))

    def test_promoter_is_dispatch_only_with_one_digest_input_and_main_guard(self):
        workflow, job, _ = self.promoter()
        self.assertEqual(workflow.get('on', workflow.get(True)), {'workflow_dispatch': {'inputs': {'digest': {
            'description': 'Must equal the fixed r13 candidate digest in this workflow', 'required': True, 'type': 'string'}}}})
        self.assertEqual(list(workflow['jobs']), ['promote'])
        self.assertEqual(job['if'], "github.repository == 'bearyjd/bazzite-tower' && github.ref == 'refs/heads/main'")
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])
        self.assertEqual(workflow['concurrency']['group'], 'bazzite-tower-stable-writers')
        self.assertEqual(workflow['permissions'], {})
        self.assertEqual(job['permissions'], {'contents': 'read', 'packages': 'write'})

    def test_fixed_constants_and_first_step_requires_the_fixed_digest(self):
        workflow, _, steps = self.promoter()
        self.assertEqual(workflow['env'], {'SOURCE': 'ghcr.io/bearyjd/bazzite-tower-r13-candidate', 'DEST': 'ghcr.io/bearyjd/bazzite-tower',
                                            'DIGEST': self.FIXED_DIGEST, 'TAG': 'snitchwatch-system'})
        self.assertEqual(steps[0]['env'], {'INPUT_DIGEST': '${{ inputs.digest }}'})
        for given, success in [(self.FIXED_DIGEST, True), ('sha256:' + '0' * 64, False), ('', False), (self.FIXED_DIGEST + ' ', False),
                               (self.FIXED_DIGEST[7:], False)]:
            result = subprocess.run(['bash', '-ec', steps[0]['run']], env=dict(os.environ, DIGEST=self.FIXED_DIGEST, INPUT_DIGEST=given),
                                    capture_output=True, text=True)
            with self.subTest(given=given): self.assertEqual(result.returncode == 0, success, result.stderr)

    def test_promoter_has_no_checkout_pins_every_action_and_scopes_the_signing_secret(self):
        text = self.PROMOTER.read_text()
        _, _, steps = self.promoter()
        self.assertNotIn('actions/checkout', text)
        self.assertNotIn('R13_', text); self.assertNotIn('vars.', text)
        uses = [s['uses'] for s in steps if 'uses' in s]
        self.assertEqual(sorted(u.split('@')[0] for u in uses), ['docker/login-action', 'sigstore/cosign-installer'])
        for use in uses: self.assertRegex(use, r'@[a-f0-9]{40}$')
        self.assertEqual(text.count('SIGNING_SECRET'), 1)
        # Protection rules (required reviewer) attach to the environment.
        self.assertEqual(self.promoter()[1]['environment'], 'release')
        # Staging tag is never deleted: GHCR deletes whole versions, and that
        # version also carries :snitchwatch-system after promotion.
        runs = '\n'.join(s.get('run', '') for s in steps)
        for forbidden in ('skopeo delete', '-X DELETE', 'versions/'): self.assertNotIn(forbidden, runs)
        holder = next(s for s in steps if 'SIGNING_SECRET' in str(s))
        self.assertIn('cosign sign', holder['run']); self.assertIn('cosign attest', holder['run'])
        self.assertEqual(sum('cosign sign' in s.get('run', '') or 'cosign attest' in s.get('run', '') for s in steps), 1)

    def test_tag_moves_only_after_verification_and_only_the_system_tag_moves(self):
        _, _, steps = self.promoter()
        order = ['Require the fixed digest', 'Verify the source', 'Copy the digest', 'Sign the digest', 'Verify the destination',
                 'Move the system tag', 'Verify the moved tag']
        indexes = [self.step_index(steps, name) for name in order]
        self.assertEqual(indexes, sorted(indexes))
        self.assertLess(self.step_index(steps, 'Fetch host signature policy'), indexes[1])
        source, copy_step, sign, dest, move, final = (steps[i]['run'] for i in indexes[1:])
        self.assertEqual(steps[indexes[2]]['env']['STAGING_TAG'], 'r13-promotion-${{ github.run_id }}')
        self.assertIn('--new-bundle-format', source); self.assertIn('--type spdxjson', source)
        self.assertIn('cosign verify --key', dest); self.assertIn('verify-attestation --key', dest); self.assertNotIn('--new-bundle-format', dest)
        for run in (copy_step, move): self.assertIn('skopeo copy --all --preserve-digests --digestfile', run)
        for run in (dest, final): self.assertIn('--policy "${trust}/policy.json" --registries.d "${trust}/registries.d"', run)
        # Every skopeo copy and its destination: staging tag, host-policy probes into local OCI dirs, then the one tag move.
        copies = []
        for index, step in enumerate(steps):
            for line in step.get('run', '').replace('\\\n', ' ').splitlines():
                if 'skopeo' in line and ' copy ' in line: copies.append((index, line.split()[-1]))
        self.assertEqual(copies, [(indexes[2], '"docker://${DEST}:${STAGING_TAG}"'),
                                  (indexes[4], '"oci:${RUNNER_TEMP}/host-policy-image:r13"'),
                                  (indexes[5], '"docker://${DEST}:${TAG}"'),
                                  (indexes[6], '"oci:${RUNNER_TEMP}/host-policy-tag:r13"')])
        text = self.PROMOTER.read_text()
        for other in (':latest', 'latest-kernel', 'safe-pin'): self.assertNotIn(other, text)

    def run_step(self, step, tmp, tools, env):
        for name, body in tools.items():
            path = Path(tmp) / 'bin' / name; path.parent.mkdir(exist_ok=True); path.write_text('#!/bin/sh\n' + body); path.chmod(0o755)
        base = dict(os.environ, PATH=str(Path(tmp) / 'bin') + os.pathsep + os.environ['PATH'], RUNNER_TEMP=tmp, GITHUB_SHA='c' * 40,
                    GITHUB_REPOSITORY='bearyjd/bazzite-tower', SOURCE='ghcr.io/bearyjd/bazzite-tower-r13-candidate',
                    DEST='ghcr.io/bearyjd/bazzite-tower', DIGEST=self.FIXED_DIGEST, TAG='snitchwatch-system')
        return subprocess.run(['bash', '-ec', step['run']], env=dict(base, **env), capture_output=True, text=True)

    def test_trust_fetch_rewrites_only_the_key_path_and_refuses_a_changed_host_rule(self):
        _, _, steps = self.promoter()
        step = steps[self.step_index(steps, 'Fetch host signature policy')]
        host = ROOT / 'system_files/usr/share/bazzite-tower/containers'
        fake_gh = 'for a in "$@"; do case "$a" in repos/*) path="${a#*/contents/}"; path="${path%%\\?*}";; esac; done\ncat "$ROOT_DIR/$path"\n'
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_step(step, tmp, {'gh': fake_gh}, {'ROOT_DIR': str(ROOT)})
            self.assertEqual(result.returncode, 0, result.stderr)
            trust = Path(tmp) / 'trust'
            policy = json.loads((trust / 'policy.json').read_text()); original = json.loads((host / 'policy.json').read_text())
            self.assertEqual(policy['transports']['docker']['ghcr.io/bearyjd/bazzite-tower'][0]['keyPath'], str(trust / 'cosign.pub'))
            policy['transports']['docker']['ghcr.io/bearyjd/bazzite-tower'][0]['keyPath'] = original['transports']['docker']['ghcr.io/bearyjd/bazzite-tower'][0]['keyPath']
            self.assertEqual(policy, original)
            self.assertEqual((trust / 'cosign.pub').read_bytes(), (ROOT / 'cosign.pub').read_bytes())
            self.assertEqual((trust / 'registries.d/bazzite-tower.yaml').read_bytes(), (host / 'registries.d/bazzite-tower.yaml').read_bytes())
        for mutate in (lambda p: p['transports']['docker']['ghcr.io/bearyjd/bazzite-tower'][0].update(keyPath='/tmp/other.pub'),
                       lambda p: p['transports']['docker']['ghcr.io/bearyjd/bazzite-tower'][0].update(type='insecureAcceptAnything'),
                       lambda p: p.update(default=[{'type': 'insecureAcceptAnything'}])):
            changed = json.loads((host / 'policy.json').read_text()); mutate(changed)
            with tempfile.TemporaryDirectory() as tmp:
                fake_gh = 'case "$*" in *policy.json*) cat "$CHANGED";; *) cat "$ROOT_DIR/cosign.pub";; esac\n'
                (Path(tmp) / 'changed.json').write_text(json.dumps(changed))
                result = self.run_step(step, tmp, {'gh': fake_gh}, {'ROOT_DIR': str(ROOT), 'CHANGED': str(Path(tmp) / 'changed.json')})
                self.assertNotEqual(result.returncode, 0)

    def test_source_verification_keeps_one_key_signed_spdx_predicate_for_the_fixed_digest(self):
        import base64
        _, _, steps = self.promoter()
        step = steps[self.step_index(steps, 'Verify the source')]
        digest = self.FIXED_DIGEST[7:]
        spdx = {'spdxVersion': 'SPDX-2.3', 'packages': [{'name': 'fixture'}]}

        def line(kind=None, subject=digest, predicate=spdx, wrap=True):
            statement = {'_type': 'https://in-toto.io/Statement/v0.1', 'predicateType': kind or 'https://spdx.dev/Document',
                         'subject': [{'name': 'x', 'digest': {'sha256': subject}}], 'predicate': predicate}
            envelope = {'payloadType': 'application/vnd.in-toto+json', 'payload': base64.b64encode(json.dumps(statement).encode()).decode()} if wrap else statement
            return json.dumps(envelope)

        cases = [([line()], True), ([line(), line()], True), ([line(wrap=False)], True),
                 ([line('https://slsa.dev/provenance/v1'), line()], True),
                 ([], False), ([line(subject='0' * 64)], False), ([line('https://slsa.dev/provenance/v1')], False),
                 ([line(), line(predicate=dict(spdx, packages=[]))], False), ([line(predicate={'spdxVersion': 'x'})], False)]
        for lines, success in cases:
            with self.subTest(lines=len(lines), success=success), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / 'lines').write_text('\n'.join(lines) + '\n')
                cosign = 'case "$1" in verify-attestation) cat "$LINES";; esac\n'
                (Path(tmp) / 'trust').mkdir()
                result = self.run_step(step, tmp, {'cosign': cosign, 'gh': self.FAKE_GH},
                                       {'LINES': str(Path(tmp) / 'lines'), 'GHLOG': str(Path(tmp) / 'gh.log')})
                self.assertEqual(result.returncode == 0, success, result.stderr)
                if success:
                    self.assertEqual(json.loads((Path(tmp) / 'spdx.json').read_text()), spdx)
                    self.assertEqual((Path(tmp) / 'gh.log').read_text().split(), self.PROVENANCE_ARGS)
                self.assertNotIn(base64.b64encode(json.dumps(spdx).encode()).decode()[:40], result.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'trust').mkdir()
            result = self.run_step(step, tmp, {'cosign': 'case "$1" in verify) exit 1;; esac\n', 'gh': self.FAKE_GH},
                                   {'GHLOG': str(Path(tmp) / 'gh.log')})
            self.assertNotEqual(result.returncode, 0)
        # A provenance mismatch must stop the step after an otherwise valid SPDX selection.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'lines').write_text(line() + '\n'); (Path(tmp) / 'trust').mkdir()
            cosign = 'case "$1" in verify-attestation) cat "$LINES";; esac\n'
            result = self.run_step(step, tmp, {'cosign': cosign, 'gh': self.FAKE_GH},
                                   {'LINES': str(Path(tmp) / 'lines'), 'GHLOG': str(Path(tmp) / 'gh.log'), 'GH_RC': '1'})
            self.assertNotEqual(result.returncode, 0)

    FAKE_GH = 'echo "$*" >> "$GHLOG"; exit "${GH_RC:-0}"\n'
    PROVENANCE_ARGS = ['attestation', 'verify',
                       'oci://ghcr.io/bearyjd/bazzite-tower-r13-candidate@sha256:df4ec1239abae0cbe1d4d4e27bce85d54f17e2b5155e431c2727c724aa879bf7',
                       '--repo', 'bearyjd/bazzite-tower',
                       '--signer-workflow', 'bearyjd/bazzite-tower/.github/workflows/r13-candidate.yml',
                       '--source-digest', '91908f11eba496cde806298f2d0cbe332213192d',
                       '--source-ref', 'refs/heads/ci/r13-candidate-hold-promotion', '--deny-self-hosted-runners']

    def test_inline_shell_syntax_and_python_compile(self):
        import yaml
        for name in ('r13-candidate.yml', 'r13-promote.yml'):
            workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
            for job in workflow['jobs'].values():
                for step in job['steps']:
                    text = step.get('run')
                    if text:
                        # Expressions only occur in constant stage tags and do
                        # not affect shell grammar after GitHub expansion.
                        result = subprocess.run(['bash', '-n'], input=text, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        if "python3 - <<'PY'\n" in text:
                            program = text.split("python3 - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
                            compile(program, name, 'exec')


if __name__ == '__main__':
    unittest.main()
