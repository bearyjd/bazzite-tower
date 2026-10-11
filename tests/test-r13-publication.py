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
import urllib.error
import urllib.request
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
        self.packet = {'schemaVersion': 1, 'identities': self.ids,
                       'candidate': {'runId': 100, 'artifactId': 101, 'artifactSha256': 'd' * 64,
                                     'metadataSha256': pub.file_sha(self.data / 'candidate.json'), 'image': self.image,
                                     'ref': pub.CANDIDATE_REF, 'workflow': pub.CANDIDATE_WORKFLOW},
                       'publication': {'runId': 100, 'workflowCommit': self.ids['towerCommit'], 'workflow': pub.CANDIDATE_WORKFLOW, 'ref': pub.CANDIDATE_REF},
                       'gui': {'repository': 'bearyjd/snitchwatch', 'sourceCommit': self.ids['sourceCommit'], 'sourceTree': self.ids['sourceTree'],
                               'submoduleCommit': self.ids['submoduleCommit'], 'profile': 'system', 'runtimeRef': 'org.kde.Platform/x86_64/6.11',
                               'manifestSha256': 'b' * 64, 'bundleSha256': 'e' * 64, 'runId': 200, 'workflow': '.github/workflows/candidate-gui.yml',
                               'ref': 'refs/heads/r13-candidate', 'artifactId': 201, 'artifactSha256': 'f' * 64}}
        self.accept_packet()

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

    def accept_packet(self):
        self.packet_hash = self.store('packets', self.packet)
        self.auth['accepted'] = {'packetSha256': self.packet_hash,
                                 'receipts': {kind: self.make_receipt(kind, self.packet_hash) for kind in pub.RECEIPTS}}
        self.write_auth()

    def test_valid_candidate_and_primary_packet(self):
        self.assertEqual(pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100), self.candidate)
        self.assertEqual(pub.accepted(self.root, self.packet_hash), self.packet)

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
                self.assertEqual(argv[:5], ['skopeo', 'copy', '--all', '--preserve-digests',
                                           'containers-storage:localhost/bazzite-tower:r13-build'])
                self.assertEqual(argv[5], 'oci-archive:' + str(out / 'image.oci.tar') + ':r13-build')
                archive(out / 'image.oci.tar', tamper=failure == 'archive')
            else:
                self.assertEqual(argv, ['podman', 'rm', 'extraction-container'])
            return subprocess.CompletedProcess(argv, 0)

        argv = ['collector', '--source', str(ROOT), '--control', str(self.root), '--out', str(out)]
        with patch.object(sys, 'argv', argv), patch.dict(os.environ, {'GITHUB_SHA': self.ids['towerCommit'], 'GITHUB_RUN_ID': '100'}), \
                patch.object(collector.subprocess, 'check_output', side_effect=check_output), \
                patch.object(collector.subprocess, 'run', side_effect=run):
            if failure:
                error = subprocess.CalledProcessError if failure == 'copy' else ValueError
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

    def test_collector_final_validation_failure_removes_container(self):
        out, _ = self.collect_commandless_image('profile')
        self.assertTrue((out / 'candidate.json').exists(), 'failed candidate evidence must remain')

    def test_complete_unmodified_promotion_entrypoint(self):
        inputs = Path(self.tmp.name) / 'inputs'; inputs.mkdir()
        shutil.copytree(self.data, inputs / 'tower'); (inputs / 'gui').mkdir()
        bundle = inputs / 'gui/snitchwatch-system-r13-candidate.flatpak'
        bundle.write_bytes(b'accepted GUI fixture')
        self.packet['gui']['bundleSha256'] = pub.file_sha(bundle); self.accept_packet()
        for prefix, repository, head, kind in [('tower', pub.REPOSITORY, self.ids['towerCommit'], 'candidate'),
                                               ('gui', 'bearyjd/snitchwatch', self.ids['sourceCommit'], 'gui')]:
            item = self.packet[kind]
            run = {'id': item['runId'], 'head_sha': head, 'head_branch': item['ref'].removeprefix('refs/heads/'),
                   'path': item['workflow'], 'repository': {'full_name': repository}, 'head_repository': {'full_name': repository},
                   'event': 'push', 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1}
            artifact = {'id': item['artifactId'], 'name': 'r13-image-data' if prefix == 'tower' else 'r13-system-gui-candidate',
                        'expired': False, 'digest': 'sha256:' + item['artifactSha256'], 'workflow_run': {'id': item['runId']}}
            (inputs / (prefix + '-run.json')).write_bytes(pub.canonical(run))
            (inputs / (prefix + '-artifact.json')).write_bytes(pub.canonical(artifact))
        output = inputs / 'output'; output.write_text('')
        env = dict(os.environ, GITHUB_REPOSITORY=pub.REPOSITORY, GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_SHA='9' * 40,
                   GITHUB_REF='refs/heads/main', GITHUB_WORKFLOW_REF=pub.REPOSITORY + '/' + pub.PROMOTION_WORKFLOW + '@refs/heads/main',
                   GITHUB_RUN_ATTEMPT='1', R13_CONTROL_HEAD='a' * 40, R13_PROMOTION_HEAD='9' * 40, GITHUB_OUTPUT=str(output))
        def invoke():
            return subprocess.run(['python3', str(ROOT / 'ci/r13-publication.py'), 'promote', '--control', str(self.root),
                                   '--data', str(inputs), '--packet-sha256', self.packet_hash], env=env, capture_output=True, text=True)
        result = invoke(); self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        self.assertEqual(set(values), {'digest', 'hashes', 'candidate_head', 'candidate_run_id', 'artifact_id',
                                      'gui_run_id', 'gui_artifact_id', 'gui_sha256', 'gui_identity', 'packet_sha256'})
        self.assertEqual(values['digest'], self.image['manifestDigest'])
        self.assertEqual(values['gui_sha256'], pub.file_sha(bundle))
        self.assertEqual(values['packet_sha256'], self.packet_hash)
        self.assertEqual(json.loads(values['gui_identity']), self.packet['gui'])
        self.assertEqual(json.loads(values['hashes']), {p.name: pub.file_sha(p) for p in (inputs / 'tower').iterdir()})
        for file, key, value in [('tower-run.json', 'head_sha', 'f' * 40), ('gui-run.json', 'head_repository', {'full_name': 'attacker/repo'}),
                                 ('gui-run.json', 'status', 'in_progress'), ('gui-artifact.json', 'digest', 'sha256:' + '0' * 64),
                                 ('tower-artifact.json', 'workflow_run', {'id': 999})]:
            p = inputs / file; old = p.read_bytes(); changed = json.loads(old); changed[key] = value
            p.write_bytes(pub.canonical(changed)); output.write_text('')
            with self.subTest(file=file, key=key):
                self.assertNotEqual(invoke().returncode, 0)
                self.assertEqual(output.read_text(), '')
            p.write_bytes(old)
        bundle.write_bytes(b'substituted GUI'); output.write_text('')
        self.assertNotEqual(invoke().returncode, 0); self.assertEqual(output.read_text(), '')

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

    def test_candidate_cannot_supply_acceptance_receipts(self):
        self.auth['accepted'] = None; self.write_auth()
        (self.data / 'PASS.json').write_bytes(pub.canonical({'verdict': 'PASS', 'packetSha256': self.packet_hash}))
        with self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)
        with self.assertRaises(ValueError): pub.validate_candidate(self.root, self.data, self.ids['towerCommit'], 100)

    def test_missing_and_stale_receipts_refuse(self):
        for kind in pub.RECEIPTS:
            saved = self.auth['accepted']['receipts'][kind]
            del self.auth['accepted']['receipts'][kind]; self.write_auth()
            with self.subTest(kind=kind), self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)
            self.auth['accepted']['receipts'][kind] = self.make_receipt(kind, 'f' * 64); self.write_auth()
            with self.subTest(stale=kind), self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)
            self.auth['accepted']['receipts'][kind] = saved

    def test_receipt_content_and_path_refusals(self):
        for field, value in [('verdict', 'UNVERIFIED'), ('reviewer', ''), ('identities', dict(self.ids, sourceCommit='f' * 40)), ('kind', 'candidate-authored-pass')]:
            self.auth['accepted']['receipts']['runtime-nfqueue'] = self.make_receipt('runtime-nfqueue', self.packet_hash, **{field: value})
            self.write_auth()
            with self.subTest(field=field), self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)
        self.auth['accepted']['receipts']['runtime-nfqueue'] = '../../PASS'; self.write_auth()
        with self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)

    def test_packet_tamper_and_wrong_hash_refuse(self):
        with self.assertRaises(ValueError): pub.accepted(self.root, 'f' * 64)
        path = self.root / '.github/r13/packets' / (self.packet_hash + '.json')
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)

    def test_gui_source_profile_and_runtime_refusals(self):
        for field, value in [('sourceCommit', 'f' * 40), ('sourceTree', 'f' * 40), ('submoduleCommit', 'f' * 40),
                             ('profile', 'legacy'), ('runtimeRef', 'org.kde.Platform/x86_64/latest'), ('repository', 'attacker/repo')]:
            saved = copy.deepcopy(self.packet); self.packet['gui'][field] = value; self.accept_packet()
            with self.subTest(field=field), self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)
            self.packet = saved

    def test_moved_candidate_tag_does_not_change_accepted_digest(self):
        # Tags/target overrides are deliberately absent from the strict packet.
        self.packet['candidate']['tag'] = 'r13-candidate'; self.accept_packet()
        with self.assertRaises(ValueError): pub.accepted(self.root, self.packet_hash)
        del self.packet['candidate']['tag']; self.accept_packet()
        value = pub.accepted(self.root, self.packet_hash)
        self.assertEqual(value['candidate']['image']['manifestDigest'], self.image['manifestDigest'])

    def test_oci_link_duplicate_platform_index_and_blob_tamper_refuse(self):
        for options in [dict(link=True), dict(duplicate=True), dict(arch='arm64'), dict(multi=True), dict(tamper=True)]:
            archive(self.data / 'image.oci.tar', **options)
            with self.subTest(options=options), self.assertRaises(ValueError): pub.image_identity(self.data / 'image.oci.tar')

    def test_duplicate_json_refuses(self):
        with self.assertRaises(ValueError): pub.decode(b'{"verdict":"FAIL","verdict":"PASS"}')

    def test_exact_ci_and_artifact_receipts(self):
        run = {'id': 100, 'head_sha': self.ids['towerCommit'], 'head_branch': pub.CANDIDATE_REF.removeprefix('refs/heads/'),
               'path': pub.CANDIDATE_WORKFLOW, 'repository': {'full_name': pub.REPOSITORY}, 'head_repository': {'full_name': pub.REPOSITORY},
               'event': 'push', 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1}
        kwargs = dict(repository=pub.REPOSITORY, head=self.ids['towerCommit'], workflow=pub.CANDIDATE_WORKFLOW, ref=pub.CANDIDATE_REF, run_id=100)
        pub.run_receipt(run, **kwargs)
        for key, value in [('head_sha', 'f' * 40), ('path', '.github/workflows/build.yml'), ('head_branch', 'main'),
                           ('conclusion', 'failure'), ('status', 'queued'), ('event', 'pull_request'), ('run_attempt', 2)]:
            with self.subTest(key=key), self.assertRaises(ValueError): pub.run_receipt(dict(run, **{key: value}), **kwargs)
        artifact = {'id': 101, 'name': 'r13-image-data', 'expired': False, 'digest': 'sha256:' + 'd' * 64, 'workflow_run': {'id': 100}}
        pub.artifact_receipt(artifact, self.packet['candidate'], 100, 'r13-image-data')
        for key, value in [('id', 999), ('name', 'PASS'), ('expired', True), ('digest', 'sha256:' + 'e' * 64), ('workflow_run', {'id': 999})]:
            with self.subTest(key=key), self.assertRaises(ValueError): pub.artifact_receipt(dict(artifact, **{key: value}), self.packet['candidate'], 100, 'r13-image-data')

    def test_context_ref_workflow_event_head_and_rerun_refuse(self):
        env = {'GITHUB_REPOSITORY': pub.REPOSITORY, 'GITHUB_EVENT_NAME': 'push', 'GITHUB_SHA': self.ids['towerCommit'], 'GITHUB_RUN_ATTEMPT': '1'}
        ref = pub.CANDIDATE_REF; workflow = pub.REPOSITORY + '/' + pub.CANDIDATE_WORKFLOW + '@' + ref
        with patch.dict(os.environ, env, clear=True):
            pub.context('candidate', self.ids['towerCommit'], ref, workflow, 'a' * 40)
            for changed_ref, changed_workflow in [('refs/heads/main', workflow), (ref, 'attacker/workflow')]:
                with self.assertRaises(ValueError): pub.context('candidate', self.ids['towerCommit'], changed_ref, changed_workflow, 'a' * 40)
            with self.assertRaises(ValueError): pub.context('candidate', self.ids['towerCommit'], ref, workflow, '')
        for key, value in [('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REPOSITORY', 'attacker/repo'), ('GITHUB_SHA', 'f' * 40), ('GITHUB_RUN_ATTEMPT', '2')]:
            with patch.dict(os.environ, dict(env, **{key: value}), clear=True), self.assertRaises(ValueError):
                pub.context('candidate', self.ids['towerCommit'], ref, workflow, 'a' * 40)

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


class PublicGuiAcquisition(unittest.TestCase):
    def setUp(self):
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/r13-promote.yml').read_text())
        self.steps = [next(s for s in workflow['jobs'][job]['steps'] if s.get('name') == 'Acquire accepted GUI from fixed public candidate release')
                      for job in ('validate', 'promote')]
        self.programs = [s['run'].split("python3 - <<'PY'\n", 1)[1].split('\nPY', 1)[0] for s in self.steps]
        self.assertEqual(self.programs[0], self.programs[1])
        for job in ('validate', 'promote'):
            self.assertFalse(any(s.get('with', {}).get('repository') == 'bearyjd/snitchwatch' for s in workflow['jobs'][job]['steps']))
        self.assertNotIn("['gui','bearyjd','snitchwatch'", next(s for s in workflow['jobs']['validate']['steps'] if s.get('id') == 'fetch')['with']['script'])
        self.bundle = b'accepted public GUI bytes'
        self.name = 'snitchwatch-system-r13-candidate.flatpak'
        self.api = 'https://api.github.com/repos/bearyjd/snitchwatch'
        self.download = 'https://github.com/bearyjd/snitchwatch/releases/download/r13-candidate/' + self.name
        self.storage = 'https://release-assets.githubusercontent.com/github-production-release-asset/fixture?signature=opaque'
        self.g = {'repository': 'bearyjd/snitchwatch', 'sourceCommit': '1' * 40, 'sourceTree': '2' * 40, 'submoduleCommit': '3' * 40,
                  'profile': 'system', 'runtimeRef': 'org.kde.Platform/x86_64/6.11', 'manifestSha256': '4' * 64,
                  'bundleSha256': pub.sha(self.bundle), 'runId': 100, 'workflow': '.github/workflows/candidate-gui.yml',
                  'ref': 'refs/heads/r13-candidate', 'artifactId': 101, 'artifactSha256': '5' * 64}
        self.repo = {'full_name': 'bearyjd/snitchwatch', 'private': False}
        self.run = {'id': 100, 'head_sha': self.g['sourceCommit'], 'head_branch': 'r13-candidate', 'path': self.g['workflow'],
                    'repository': {'full_name': 'bearyjd/snitchwatch'}, 'head_repository': {'full_name': 'bearyjd/snitchwatch'},
                    'event': 'push', 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1}
        self.artifact = {'id': 101, 'name': 'r13-system-gui-candidate', 'expired': False, 'digest': 'sha256:' + self.g['artifactSha256'],
                         'workflow_run': {'id': 100}}
        self.ref = {'ref': 'refs/tags/r13-candidate', 'object': {'type': 'commit', 'sha': self.g['sourceCommit'],
                    'url': self.api + '/git/commits/' + self.g['sourceCommit']}}
        self.asset = {'id': 400, 'name': self.name, 'state': 'uploaded', 'size': len(self.bundle), 'digest': 'sha256:' + self.g['bundleSha256'],
                      'url': self.api + '/releases/assets/400', 'browser_download_url': self.download}
        self.release = {'id': 300, 'tag_name': 'r13-candidate', 'target_commitish': self.g['sourceCommit'], 'draft': False, 'prerelease': True,
                        'published_at': '2026-10-11T05:00:00Z', 'url': self.api + '/releases/300',
                        'html_url': 'https://github.com/bearyjd/snitchwatch/releases/tag/r13-candidate', 'assets': [self.asset]}

    def execute(self, *, program=0, redirect=None, redirect_loop=False, status=200, raw=None, content_length=None,
                unavailable=None, mutate_after=None, metadata_raw=None, slow=False, preexisting=None, input_symlink=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); requests = []; release_reads = 0
            if preexisting:
                (root / 'gui').symlink_to(root) if preexisting == 'symlink' else (root / 'gui').mkdir()
            if input_symlink:
                link = root / 'input'; link.symlink_to(root, target_is_directory=True); input_dir = link
            else:
                input_dir = root
            class Response(io.BytesIO):
                def __init__(self, data, url, code=200, headers=None):
                    super().__init__(data); self.url = url; self.status = code; self.headers = headers or {}
                def geturl(self): return self.url
            def open_request(request, timeout):
                nonlocal release_reads
                requests.append(request)
                self.assertFalse(any(k.lower() == 'authorization' for k in request.headers))
                self.assertGreater(timeout, 0); self.assertLessEqual(timeout, 30)
                url = request.full_url
                if unavailable and url == self.api + unavailable:
                    raise urllib.error.HTTPError(url, 404, 'not found', {}, io.BytesIO())
                if url == self.download or (redirect_loop and url == self.storage):
                    target = redirect or self.storage
                    raise urllib.error.HTTPError(url, 302, 'redirect', {'Location': target}, io.BytesIO())
                if url == self.storage:
                    data = self.bundle if raw is None else raw
                    headers = {} if content_length is None else {'Content-Length': content_length}
                    return Response(data, url, status, headers)
                values = {'': self.repo, '/actions/runs/100': self.run, '/actions/artifacts/101': self.artifact,
                          '/git/ref/tags/r13-candidate': self.ref, '/releases/tags/r13-candidate': self.release}
                path = url.removeprefix(self.api)
                self.assertIn(path, values)
                if path == '/releases/tags/r13-candidate':
                    release_reads += 1
                    if release_reads == 2 and mutate_after: mutate_after()
                data = pub.canonical(values[path])
                if metadata_raw is not None and path == '': data = metadata_raw
                return Response(data, url)
            opener = unittest.mock.Mock(); opener.open.side_effect = open_request
            handlers = []
            def build_opener(*args):
                handlers.extend(args); return opener
            env = {'GUI_IDENTITY': json.dumps(self.g), 'GUI_INPUT_DIR': str(input_dir), 'GH_TOKEN': 'must-not-transmit', 'GITHUB_TOKEN': 'must-not-transmit'}
            clock = [0, 0, 0, 0, 0, 0, 601] if slow else None
            with patch.dict(os.environ, env), patch.object(urllib.request, 'build_opener', side_effect=build_opener):
                timer = patch('time.monotonic', side_effect=clock) if slow else patch('time.monotonic', return_value=0)
                try:
                    with timer: exec(compile(self.programs[program], 'actual-public-gui-inline', 'exec'), {})
                except Exception:
                    self.assertFalse((root / 'gui' / self.name).exists())
                    self.assertFalse((root / 'gui' / (self.name + '.partial')).exists())
                    raise
            self.assertEqual((root / 'gui' / self.name).read_bytes(), self.bundle)
            self.assertEqual(json.loads((root / 'gui-run.json').read_bytes()), self.run)
            self.assertEqual(json.loads((root / 'gui-artifact.json').read_bytes()), self.artifact)
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsNone(handlers[1].redirect_request(None, None, 302, '', {}, self.storage))
            self.assertTrue(any(r.full_url == self.storage for r in requests))

    def test_actual_inline_both_jobs_success_without_authorization(self):
        for program in (0, 1):
            with self.subTest(job=program): self.execute(program=program, content_length=str(len(self.bundle)))

    def test_public_metadata_and_accepted_identity_refusals(self):
        cases = [(self.repo, 'private', True), (self.repo, 'full_name', 'attacker/repo'),
                 (self.g, 'repository', 'attacker/repo'), (self.g, 'profile', 'legacy'), (self.g, 'runtimeRef', 'latest'),
                 (self.g, 'workflow', '.github/workflows/release.yml'), (self.g, 'ref', 'refs/heads/main'),
                 (self.g, 'sourceCommit', '1;touch /tmp/escape'), (self.g, 'runId', True),
                 (self.run, 'head_sha', 'f' * 40), (self.run, 'path', '.github/workflows/release.yml'), (self.run, 'head_branch', 'main'),
                 (self.run, 'conclusion', 'failure'), (self.run, 'status', 'queued'), (self.run, 'event', 'pull_request'),
                 (self.run, 'run_attempt', 2), (self.run, 'head_repository', {'full_name': 'attacker/repo'}),
                 (self.artifact, 'id', 999), (self.artifact, 'name', 'other'), (self.artifact, 'expired', True),
                 (self.artifact, 'digest', 'sha256:' + '0' * 64), (self.artifact, 'workflow_run', {'id': 999}),
                 (self.ref, 'ref', 'refs/heads/r13-candidate'), (self.ref, 'object', dict(self.ref['object'], sha='f' * 40)),
                 (self.ref, 'object', dict(self.ref['object'], type='tag')),
                 (self.release, 'tag_name', 'stable'), (self.release, 'target_commitish', 'main'),
                 (self.release, 'draft', True), (self.release, 'prerelease', False), (self.release, 'published_at', None),
                 (self.release, 'url', 'https://evil.example/releases/300'), (self.release, 'assets', []),
                 (self.release, 'assets', [self.asset, self.asset]), (self.asset, 'state', 'new'),
                 (self.asset, 'size', 0), (self.asset, 'size', 512 * 1024**2 + 1), (self.asset, 'size', True),
                 (self.asset, 'digest', 'sha256:' + '0' * 64), (self.asset, 'url', self.api + '/releases/assets/999'),
                 (self.asset, 'browser_download_url', 'https://evil.example/' + self.name)]
        for mapping, key, value in cases:
            old = mapping[key]; mapping[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.execute()
            mapping[key] = old

    def test_actual_inline_redirects_bounds_changed_bytes_and_missing_data_refuse(self):
        cases = [dict(redirect=url) for url in ('http://release-assets.githubusercontent.com/asset', 'https://evil.example/asset',
                  'https://github.com/attacker/repo/asset', 'https://release-assets.githubusercontent.com.evil.example/asset',
                  'https://user@release-assets.githubusercontent.com/asset', 'https://release-assets.githubusercontent.com:443/asset',
                  'https://release-assets.githubusercontent.com/asset#fragment', 'file:///etc/passwd')]
        cases += [dict(redirect_loop=True), dict(status=206), dict(raw=b'changed'), dict(raw=self.bundle[:-1]),
                  dict(raw=self.bundle + b'extra'), dict(content_length='999'), dict(slow=True), dict(input_symlink=True),
                  dict(metadata_raw=b'{"private":false,"private":true}'), dict(metadata_raw=b'x' * (16 * 1024**2 + 1))]
        cases += [dict(unavailable=path) for path in ('/releases/tags/r13-candidate', '/actions/runs/100', '/actions/artifacts/101')]
        for case in cases:
            with self.subTest(case=list(case)), self.assertRaises(ValueError): self.execute(**case)
        for kind in ('symlink', 'directory'):
            with self.subTest(preexisting=kind), self.assertRaises(FileExistsError): self.execute(preexisting=kind)
        for target, key, value in [(self.release, 'id', 301), (self.asset, 'id', 401)]:
            old = target[key]
            with self.subTest(changed_during_download=key), self.assertRaises(ValueError):
                self.execute(mutate_after=lambda: target.update({key: value}))
            target[key] = old


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
        self.assertIn('vars.R13_ACCEPTED_PACKET_SHA256', promotion['jobs']['promote']['if'])
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

    def test_original_verified_predicates_are_checked_before_stable_writes(self):
        import base64
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/r13-promote.yml').read_text())
        steps = workflow['jobs']['promote']['steps']
        source_index = next(i for i,s in enumerate(steps) if s.get('name') == 'Verify exact candidate repository policy and original build provenance')
        stage_index = next(i for i,s in enumerate(steps) if s.get('name') == 'Stage unchanged digest at fixed nonpublic-by-convention tag')
        self.assertLess(source_index, stage_index)
        text = steps[source_index]['run']
        program = text.split("python3 - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'data'; data.mkdir()
            for name,file,kind in [('sbom', 'sbom.json', 'https://spdx.dev/Document'), ('provenance', 'build-provenance.json', 'https://github.com/bearyjd/bazzite-tower/attestations/r13-candidate-build/v1')]:
                expected = {'fixture': name}; (data / file).write_bytes(pub.canonical(expected))
                statement = {'predicateType': kind, 'predicate': expected, 'subject': [{'name': pub.CANDIDATE_REPOSITORY, 'digest': {'sha256': 'a' * 64}}]}
                envelope = {'payload': base64.b64encode(pub.canonical(statement)).decode()}
                (root / ('candidate-' + name + '.verified.json')).write_bytes(pub.canonical(envelope))
            env = {'RUNNER_TEMP': tmp, 'DATA': str(data), 'DESTINATION': pub.CANDIDATE_REPOSITORY, 'DIGEST': 'sha256:' + 'a' * 64}
            with patch.dict(os.environ, env): exec(compile(program, 'original-predicate-check', 'exec'), {})
            for bad in [dict(env, DESTINATION=pub.STABLE_REPOSITORY), dict(env, DIGEST='sha256:' + 'b' * 64)]:
                with patch.dict(os.environ, bad), self.assertRaises(AssertionError): exec(compile(program, 'original-predicate-check', 'exec'), {})
            (data / 'build-provenance.json').write_bytes(pub.canonical({'fixture': 'different-original-run'}))
            with patch.dict(os.environ, env), self.assertRaises(AssertionError): exec(compile(program, 'original-predicate-check', 'exec'), {})

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
