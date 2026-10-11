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
        self.packet = {'schemaVersion': 1, 'identities': self.ids,
                       'candidate': {'runId': 100, 'artifactId': 101, 'artifactSha256': 'd' * 64,
                                     'metadataSha256': pub.file_sha(self.data / 'candidate.json'), 'image': self.image,
                                     'ref': pub.CANDIDATE_REF, 'workflow': pub.CANDIDATE_WORKFLOW},
                       'publication': {'runId': 100, 'workflowCommit': self.ids['towerCommit'], 'workflow': pub.CANDIDATE_WORKFLOW, 'ref': pub.CANDIDATE_REF},
                       'gui': {'repository': 'bearyjd/snitchwatch', 'sourceCommit': self.ids['sourceCommit'], 'sourceTree': self.ids['sourceTree'],
                               'submoduleCommit': self.ids['submoduleCommit'], 'profile': 'system', 'runtimeRef': 'org.kde.Platform/x86_64/6.11',
                               'manifestSha256': 'b' * 64, 'bundleSha256': 'e' * 64, 'runId': 200, 'workflow': '.github/workflows/r13-candidate.yml',
                               'ref': 'refs/heads/r13/candidate', 'artifactId': 201, 'artifactSha256': 'f' * 64}}
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


class WorkflowContracts(unittest.TestCase):
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
